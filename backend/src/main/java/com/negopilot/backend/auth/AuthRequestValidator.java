package com.negopilot.backend.auth;

import com.negopilot.backend.common.api.ApiException;

import java.nio.charset.StandardCharsets;
import java.util.Locale;
import java.util.Set;
import java.util.UUID;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

final class AuthRequestValidator {
    record Signup(String name, String email, String password, String department, String jobTitle) {
        @Override public String toString() { return "Signup[credentials redacted]"; }
    }
    record Login(String email, String password) {
        @Override public String toString() { return "Login[credentials redacted]"; }
    }
    static JsonNode parse(String raw, JsonMapper mapper, Set<String> fields) {
        JsonNode body;
        try { body = mapper.readTree(raw == null ? "" : raw); }
        catch (RuntimeException failure) { throw invalid(); }
        if (body == null || !body.isObject() || !fields.containsAll(body.propertyNames())) throw invalid();
        return body;
    }
    static Signup signup(JsonNode body) {
        String password = text(body, "password", 1000, false);
        if (password.codePointCount(0, password.length()) < 8 || password.getBytes(StandardCharsets.UTF_8).length > 72
                || !password.matches("(?s).*[A-Za-z].*") || !password.matches("(?s).*[0-9].*")
                || !password.matches("(?s).*[^A-Za-z0-9].*")) throw invalid();
        String title = body.has("job_title") ? text(body, "job_title", 100, true).strip() : "";
        return new Signup(nonblank(body, "name", 100), email(body), password, nonblank(body, "department", 100), title);
    }
    static Login login(JsonNode body) {
        String password = text(body, "password", 1000, false);
        if (password.getBytes(StandardCharsets.UTF_8).length > 72) throw invalid();
        return new Login(email(body), password);
    }
    static String choice(JsonNode body, String field, Set<String> allowed) {
        String result = text(body, field, 30, false);
        if (!allowed.contains(result)) throw invalid();
        return result;
    }
    static UUID id(String raw) {
        if (raw == null || !raw.matches("[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")) throw invalid();
        return UUID.fromString(raw);
    }
    private static String email(JsonNode body) {
        String value = nonblank(body, "email", 254).toLowerCase(Locale.ROOT);
        if (!value.matches("[^\\s@]+@[^\\s@]+\\.[^\\s@]+")) throw invalid();
        return value;
    }
    private static String nonblank(JsonNode body, String field, int max) {
        String value = text(body, field, max, false).strip();
        if (value.isBlank()) throw invalid();
        return value;
    }
    private static String text(JsonNode body, String field, int max, boolean empty) {
        JsonNode value = body.get(field);
        if (value == null || !value.isString()) throw invalid();
        String result = value.stringValue();
        if ((!empty && result.isEmpty()) || result.codePointCount(0, result.length()) > max || result.indexOf('\0') >= 0) throw invalid();
        return result;
    }
    static ApiException invalid() { return ApiException.of(null, 400, "VALIDATION_ERROR", "Invalid request.", false); }
}
