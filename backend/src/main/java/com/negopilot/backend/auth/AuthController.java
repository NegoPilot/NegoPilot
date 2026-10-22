package com.negopilot.backend.auth;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.util.Set;
import org.springframework.context.annotation.Profile;
import org.springframework.http.ResponseEntity;
import org.springframework.security.web.csrf.CsrfToken;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import tools.jackson.databind.json.JsonMapper;

@RestController
@RequestMapping(value = "/api/v1/auth", produces = "application/json")
@Profile("!legacy-test")
final class AuthController {
    record CsrfResponse(String header_name, String token) { }
    private final AuthService auth;
    private final AuthSessionService sessions;
    private final CurrentUser current;
    private final JsonMapper mapper;
    AuthController(AuthService auth, AuthSessionService sessions, CurrentUser current, JsonMapper mapper) {
        this.auth = auth; this.sessions = sessions; this.current = current; this.mapper = mapper;
    }
    @GetMapping("/csrf")
    CsrfResponse csrf(HttpServletRequest request) {
        CsrfToken token = (CsrfToken) request.getAttribute(CsrfToken.class.getName());
        return new CsrfResponse(token.getHeaderName(), token.getToken());
    }
    @PostMapping(value = "/signup", consumes = "application/json")
    ResponseEntity<UserResponse> signup(@RequestBody(required = false) String raw) {
        var body = AuthRequestValidator.parse(raw, mapper, Set.of("name", "email", "password", "department", "job_title"));
        return ResponseEntity.status(201).body(new UserResponse(auth.signup(AuthRequestValidator.signup(body))));
    }
    @PostMapping(value = "/login", consumes = "application/json")
    UserResponse login(@RequestBody(required = false) String raw, HttpServletRequest request, HttpServletResponse response) {
        var body = AuthRequestValidator.parse(raw, mapper, Set.of("email", "password"));
        var member = auth.authenticate(AuthRequestValidator.login(body), request.getRemoteAddr(), request.getHeader("User-Agent"));
        sessions.signIn(member.identity(), request, response);
        return new UserResponse(member.view());
    }
    @GetMapping("/me")
    UserResponse me() { return new UserResponse(auth.currentMember(current.requireSignedIn().userId())); }
    @PostMapping("/logout")
    ResponseEntity<Void> logout(HttpServletRequest request) {
        var identity = current.requireSignedIn();
        auth.recordLogout(identity.userId(), request.getRemoteAddr(), request.getHeader("User-Agent"));
        sessions.signOut(request);
        return ResponseEntity.noContent().build();
    }
}
