package com.negopilot.backend.auth;

import com.negopilot.backend.UserIdentity;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.web.context.HttpSessionSecurityContextRepository;
import org.springframework.web.filter.OncePerRequestFilter;
import tools.jackson.databind.json.JsonMapper;

final class SecurityUserRefreshFilter extends OncePerRequestFilter {
    private final MembershipRepository members;
    private final JsonMapper mapper;
    private final HttpSessionSecurityContextRepository contexts;
    SecurityUserRefreshFilter(MembershipRepository members, JsonMapper mapper, HttpSessionSecurityContextRepository contexts) {
        this.members = members; this.mapper = mapper; this.contexts = contexts;
    }
    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain) throws ServletException, IOException {
        var authentication = SecurityContextHolder.getContext().getAuthentication();
        if (authentication != null && authentication.getPrincipal() instanceof UserIdentity identity) {
            MembershipRepository.Member member;
            try { member = members.byId(identity.userId()); }
            catch (RuntimeException failure) {
                SecurityJsonErrors.write(request, response, mapper, 503, "AUTH_UNAVAILABLE", "Authentication service is unavailable.");
                return;
            }
            if (member == null || !MembershipPolicy.permitsSignIn(member.status())) {
                SecurityContextHolder.clearContext();
                var session = request.getSession(false);
                if (session != null) session.invalidate();
                SecurityJsonErrors.write(request, response, mapper, 401, "UNAUTHENTICATED", "Authentication is required.");
                return;
            }
            if (!member.identity().equals(identity)) {
                if (identity.status().equals("PENDING") && member.status().equals("ACTIVE") && request.getSession(false) != null) {
                    request.changeSessionId();
                }
                var context = SecurityContextHolder.createEmptyContext();
                context.setAuthentication(AuthSessionService.authentication(member.identity()));
                SecurityContextHolder.setContext(context);
                contexts.saveContext(context, request, response);
            }
        }
        chain.doFilter(request, response);
    }
}
