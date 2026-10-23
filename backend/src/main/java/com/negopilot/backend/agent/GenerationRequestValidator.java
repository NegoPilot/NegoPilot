package com.negopilot.backend.agent;

import com.negopilot.backend.common.api.ApiException;
import com.negopilot.backend.common.api.RequestIds;

import java.util.Set;
import java.util.UUID;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.JsonNodeFactory;
import tools.jackson.databind.node.ObjectNode;

@Component
public final class GenerationRequestValidator {
    public record ValidatedRequest(UUID requestId, ObjectNode body) {}
    public record MessageRequest(String query, ObjectNode context) {}

    public ValidatedRequest validateQuery(JsonNode body) {
        UUID id = RequestIds.parse(body == null ? null : body.get("request_id"));
        if (!onlyFields(body, Set.of("request_id", "query", "history", "context"))
                || (body.has("request_id") && id == null) || !validText(body.get("query"))) {
            throw ApiException.validation(id);
        }
        JsonNode history = body.get("history");
        if (history != null) {
            if (!history.isArray() || history.size() > 20) throw ApiException.validation(id);
            for (JsonNode entry : history) {
                if (!onlyFields(entry, Set.of("role", "content")) || !entry.path("role").isString()
                        || !Set.of("user", "assistant").contains(entry.get("role").stringValue())
                        || !validText(entry.get("content"))) {
                    throw ApiException.validation(id);
                }
            }
        }
        ObjectNode context;
        if (body.has("context")) {
            context = context(body.get("context"), id);
        } else {
            context = JsonNodeFactory.instance.objectNode();
            context.putArray("selected_supplier_ids");
        }
        if (id == null) id = UUID.randomUUID();
        ObjectNode normalized = ((ObjectNode) body).deepCopy();
        normalized.put("request_id", id.toString());
        if (history == null) normalized.putArray("history");
        normalized.set("context", context);
        return new ValidatedRequest(id, normalized);
    }

    public ObjectNode create(JsonNode body) {
        if (!onlyFields(body, Set.of("context")) || !body.has("context")) {
            throw ApiException.validation(null);
        }
        return context(body.get("context"), null);
    }

    public MessageRequest message(JsonNode body) {
        if (!onlyFields(body, Set.of("query", "context")) || !body.path("query").isString()) {
            throw ApiException.validation(null);
        }
        String query = trimWhitespace(body.get("query").stringValue());
        if (query.isEmpty() || query.codePointCount(0, query.length()) > 4000) {
            throw ApiException.validation(null);
        }
        return new MessageRequest(query, body.has("context") ? context(body.get("context"), null) : null);
    }

    public String conversationId(String raw) {
        if (raw == null || !raw.matches("[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")) {
            throw ApiException.validation(null);
        }
        return raw;
    }

    private static ObjectNode context(JsonNode body, UUID requestId) {
        if (!onlyFields(body, Set.of("selected_supplier_ids"))) throw ApiException.validation(requestId);
        JsonNode selected = body.get("selected_supplier_ids");
        if (selected == null || !selected.isArray() || selected.size() > 1) throw ApiException.validation(requestId);
        for (JsonNode id : selected) {
            if (!id.isString() || !id.stringValue().matches("[A-Za-z0-9_-]{1,100}")) {
                throw ApiException.validation(requestId);
            }
        }
        return ((ObjectNode) body).deepCopy();
    }

    private static boolean onlyFields(JsonNode body, Set<String> allowed) {
        return body != null && body.isObject() && body.propertyNames().stream().allMatch(allowed::contains);
    }

    private static boolean validText(JsonNode body) {
        if (body == null || !body.isString()) return false;
        String value = body.stringValue();
        return value.codePointCount(0, value.length()) <= 4000
                && value.codePoints().anyMatch(codePoint -> !whitespace(codePoint));
    }

    private static String trimWhitespace(String value) {
        int start = 0;
        int end = value.length();
        while (start < end && whitespace(value.codePointAt(start))) start += Character.charCount(value.codePointAt(start));
        while (end > start && whitespace(value.codePointBefore(end))) end -= Character.charCount(value.codePointBefore(end));
        return value.substring(start, end);
    }

    private static boolean whitespace(int codePoint) {
        return Character.isWhitespace(codePoint) || Character.isSpaceChar(codePoint) || codePoint == 0x85;
    }
}
