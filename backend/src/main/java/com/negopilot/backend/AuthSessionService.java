package com.negopilot.backend;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.util.List;
import java.util.UUID;
import org.springframework.context.annotation.Profile;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.web.context.HttpSessionSecurityContextRepository;
import org.springframework.security.web.csrf.HttpSessionCsrfTokenRepository;
import org.springframework.session.FindByIndexNameSessionRepository;
import org.springframework.stereotype.Service;

@Service
@Profile("!legacy-test")
final class AuthSessionService {
    private final JdbcTemplate jdbc;
    private final HttpSessionSecurityContextRepository contexts = new HttpSessionSecurityContextRepository();
    AuthSessionService(JdbcTemplate jdbc) { this.jdbc = jdbc; }

    static UsernamePasswordAuthenticationToken authentication(UserIdentity identity) {
        return new UsernamePasswordAuthenticationToken(identity, null, List.of(
                new SimpleGrantedAuthority("ROLE_" + identity.role()), new SimpleGrantedAuthority("STATUS_" + identity.status())));
    }
    void signIn(UserIdentity identity, HttpServletRequest request, HttpServletResponse response) {
        request.getSession(true);
        request.changeSessionId();
        request.getSession().setAttribute(FindByIndexNameSessionRepository.PRINCIPAL_NAME_INDEX_NAME, identity.getName());
        var context = SecurityContextHolder.createEmptyContext();
        context.setAuthentication(authentication(identity));
        SecurityContextHolder.setContext(context);
        // The pre-login CSRF token must not survive authentication.
        new HttpSessionCsrfTokenRepository().saveToken(null, request, response);
        contexts.saveContext(context, request, response);
    }
    void signOut(HttpServletRequest request) {
        var session = request.getSession(false);
        if (session != null) session.invalidate();
        SecurityContextHolder.clearContext();
    }
    void revoke(UUID userId) {
        // Use the same transaction as membership changes. The schema is owned by Flyway.
        jdbc.update("DELETE FROM spring_session WHERE principal_name=?", userId.toString());
    }
}
