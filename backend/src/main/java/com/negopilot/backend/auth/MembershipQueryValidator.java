package com.negopilot.backend.auth;

import java.time.Instant;
import java.util.Set;
import java.util.UUID;

final class MembershipQueryValidator {
    static final Set<String> ROLES = Set.of("ADMIN", "USER");
    static final Set<String> STATES = Set.of("PENDING", "ACTIVE", "REJECTED", "DISABLED");
    private static final Set<String> EVENTS = Set.of("LOGIN_SUCCESS", "LOGIN_FAILURE", "LOGOUT");

    record Members(String search, String role, String status, int page, int size) { }
    record AccessLogs(UUID userId, String event, Instant from, Instant to, Boolean success, int page, int size) { }

    private MembershipQueryValidator() { }

    static Members members(String search, String role, String status, String page, String size) {
        if (search != null && (search.length() > 254 || search.indexOf('\0') >= 0)) throw AuthRequestValidator.invalid();
        return new Members(search == null || search.isBlank() ? null : search.strip(), option(role, ROLES), option(status, STATES),
                number(page, 0, 100_000), number(size, 1, 100));
    }

    static AccessLogs accessLogs(String userId, String event, String from, String to, String success, String page, String size) {
        Instant start = timestamp(from), end = timestamp(to);
        if (start != null && end != null && !start.isBefore(end)) throw AuthRequestValidator.invalid();
        Boolean succeeded = success == null ? null : switch (success) {
            case "true" -> true; case "false" -> false; default -> throw AuthRequestValidator.invalid();
        };
        return new AccessLogs(userId == null ? null : AuthRequestValidator.id(userId), option(event, EVENTS),
                start, end, succeeded, number(page, 0, 100_000), number(size, 1, 100));
    }

    private static String option(String value, Set<String> allowed) {
        if (value != null && !allowed.contains(value)) throw AuthRequestValidator.invalid();
        return value;
    }

    private static Instant timestamp(String value) {
        try { return value == null ? null : Instant.parse(value); }
        catch (RuntimeException failure) { throw AuthRequestValidator.invalid(); }
    }

    private static int number(String value, int min, int max) {
        try {
            if (!value.matches("[0-9]{1,6}")) throw AuthRequestValidator.invalid();
            int parsed = Integer.parseInt(value);
            if (parsed < min || parsed > max) throw AuthRequestValidator.invalid();
            return parsed;
        } catch (NumberFormatException failure) { throw AuthRequestValidator.invalid(); }
    }
}
