package com.negopilot.backend.auth;

import com.negopilot.backend.common.api.ApiPaths;
import java.util.Set;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import tools.jackson.databind.json.JsonMapper;

@RestController
@RequestMapping(value = ApiPaths.ADMIN, produces = "application/json")
final class MembershipController {
    private final MembershipService service;
    private final JsonMapper mapper;
    MembershipController(MembershipService service, JsonMapper mapper) {
        this.service = service; this.mapper = mapper;
    }
    @GetMapping("/members")
    MembershipRepository.Page<MemberView> members(
            @RequestParam(required = false) String search, @RequestParam(required = false) String role,
            @RequestParam(required = false) String status, @RequestParam(defaultValue = "0") String page,
            @RequestParam(defaultValue = "20") String size) {
        return service.members(search, role, status, page, size);
    }
    @GetMapping("/members/{id}")
    UserResponse detail(@PathVariable String id) { return new UserResponse(service.detail(AuthRequestValidator.id(id))); }
    @PatchMapping(value = "/members/{id}/status", consumes = "application/json")
    UserResponse status(@PathVariable String id, @RequestBody(required = false) String raw) {
        var body = AuthRequestValidator.parse(raw, mapper, Set.of("status"));
        return new UserResponse(service.changeStatus(AuthRequestValidator.id(id), AuthRequestValidator.choice(body, "status", MembershipQueryValidator.STATES)));
    }
    @PatchMapping(value = "/members/{id}/role", consumes = "application/json")
    UserResponse role(@PathVariable String id, @RequestBody(required = false) String raw) {
        var body = AuthRequestValidator.parse(raw, mapper, Set.of("role"));
        return new UserResponse(service.changeRole(AuthRequestValidator.id(id), AuthRequestValidator.choice(body, "role", MembershipQueryValidator.ROLES)));
    }
    @GetMapping("/access-logs")
    MembershipRepository.Page<MembershipRepository.AccessLog> logs(
            @RequestParam(name = "user_id", required = false) String userId, @RequestParam(required = false) String event,
            @RequestParam(required = false) String from, @RequestParam(required = false) String to,
            @RequestParam(required = false) String success, @RequestParam(defaultValue = "0") String page,
            @RequestParam(defaultValue = "20") String size) {
        return service.accessLogs(userId, event, from, to, success, page, size);
    }
}
