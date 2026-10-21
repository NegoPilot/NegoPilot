package com.negopilot.backend;

import java.time.Instant;
import java.util.Set;
import org.springframework.context.annotation.Profile;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import tools.jackson.databind.json.JsonMapper;

@RestController
@RequestMapping(value = "/api/v1/admin", produces = "application/json")
@Profile("!legacy-test")
final class MembershipController {
    private static final Set<String> ROLES = Set.of("ADMIN", "USER");
    private static final Set<String> STATES = Set.of("PENDING", "ACTIVE", "REJECTED", "DISABLED");
    private static final Set<String> EVENTS = Set.of("LOGIN_SUCCESS", "LOGIN_FAILURE", "LOGOUT");
    private final MembershipRepository members;
    private final MembershipService service;
    private final CurrentUser current;
    private final JsonMapper mapper;
    MembershipController(MembershipRepository members, MembershipService service, CurrentUser current, JsonMapper mapper) {
        this.members = members; this.service = service; this.current = current; this.mapper = mapper;
    }
    @GetMapping("/members")
    MembershipRepository.Page<MembershipRepository.MemberView> members(
            @RequestParam(required = false) String search, @RequestParam(required = false) String role,
            @RequestParam(required = false) String status, @RequestParam(defaultValue = "0") String page,
            @RequestParam(defaultValue = "20") String size) {
        current.requireAdmin();
        if (search != null && (search.length() > 254 || search.indexOf('\0') >= 0)) throw AuthRequestValidator.invalid();
        return members.members(search == null || search.isBlank() ? null : search.strip(), option(role, ROLES), option(status, STATES),
                number(page, 0, 100_000), number(size, 1, 100));
    }
    @GetMapping("/members/{id}")
    AuthController.UserResponse detail(@PathVariable String id) { return new AuthController.UserResponse(service.detail(AuthRequestValidator.id(id))); }
    @PatchMapping(value = "/members/{id}/status", consumes = "application/json")
    AuthController.UserResponse status(@PathVariable String id, @RequestBody(required = false) String raw) {
        var body = AuthRequestValidator.parse(raw, mapper, Set.of("status"));
        return new AuthController.UserResponse(service.change(AuthRequestValidator.id(id), "status", AuthRequestValidator.choice(body, "status", STATES)));
    }
    @PatchMapping(value = "/members/{id}/role", consumes = "application/json")
    AuthController.UserResponse role(@PathVariable String id, @RequestBody(required = false) String raw) {
        var body = AuthRequestValidator.parse(raw, mapper, Set.of("role"));
        return new AuthController.UserResponse(service.change(AuthRequestValidator.id(id), "role", AuthRequestValidator.choice(body, "role", ROLES)));
    }
    @GetMapping("/access-logs")
    MembershipRepository.Page<MembershipRepository.AccessLog> logs(
            @RequestParam(name = "user_id", required = false) String userId, @RequestParam(required = false) String event,
            @RequestParam(required = false) String from, @RequestParam(required = false) String to,
            @RequestParam(required = false) String success, @RequestParam(defaultValue = "0") String page,
            @RequestParam(defaultValue = "20") String size) {
        current.requireAdmin();
        Instant start = timestamp(from), end = timestamp(to);
        if (start != null && end != null && !start.isBefore(end)) throw AuthRequestValidator.invalid();
        Boolean succeeded = success == null ? null : switch (success) {
            case "true" -> true; case "false" -> false; default -> throw AuthRequestValidator.invalid();
        };
        return members.accessLogs(userId == null ? null : AuthRequestValidator.id(userId), option(event, EVENTS),
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
