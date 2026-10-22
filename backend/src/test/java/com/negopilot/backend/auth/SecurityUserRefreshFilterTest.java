package com.negopilot.backend.auth;

import com.negopilot.backend.UserIdentity;
import jakarta.servlet.FilterChain;
import java.time.Instant;
import java.util.UUID;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;
import org.springframework.mock.web.MockHttpSession;
import org.springframework.security.core.context.SecurityContext;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.web.context.HttpSessionSecurityContextRepository;
import tools.jackson.databind.json.JsonMapper;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

class SecurityUserRefreshFilterTest {
    private final JsonMapper mapper = JsonMapper.builder().build();
    private final MembershipRepository members = mock(MembershipRepository.class);
    private final HttpSessionSecurityContextRepository contexts = new HttpSessionSecurityContextRepository();
    private final SecurityUserRefreshFilter filter = new SecurityUserRefreshFilter(members, mapper, contexts);
    private final MockHttpServletRequest request = new MockHttpServletRequest("GET", "/api/v1/auth/me");
    private final MockHttpServletResponse response = new MockHttpServletResponse();
    private final FilterChain chain = mock(FilterChain.class);
    private final UUID userId = UUID.randomUUID();
    private final UUID requestId = UUID.randomUUID();

    @BeforeEach void setup() {
        SecurityContextHolder.clearContext();
        request.setAttribute("request_id", requestId);
    }

    @AfterEach void clearContext() { SecurityContextHolder.clearContext(); }

    @Test void databaseFailureStopsRequestWithAuthenticationUnavailable() throws Exception {
        signIn("USER", "ACTIVE");
        when(members.byId(userId)).thenThrow(new IllegalStateException("database unavailable"));

        filter.doFilter(request, response, chain);

        assertError(503, "AUTH_UNAVAILABLE");
        verifyNoInteractions(chain);
    }

    @ParameterizedTest
    @ValueSource(strings = {"REJECTED", "DISABLED"})
    void rejectedOrDisabledMembershipInvalidatesExistingSession(String status) throws Exception {
        MockHttpSession session = signIn("USER", "ACTIVE");
        when(members.byId(userId)).thenReturn(member("USER", status));

        filter.doFilter(request, response, chain);

        assertInvalidated(session);
    }

    @Test void missingMembershipInvalidatesExistingSession() throws Exception {
        MockHttpSession session = signIn("USER", "ACTIVE");
        when(members.byId(userId)).thenReturn(null);

        filter.doFilter(request, response, chain);

        assertInvalidated(session);
    }

    @Test void approvalRotatesSessionAndSavesActiveAuthorities() throws Exception {
        MockHttpSession session = signIn("USER", "PENDING");
        String previousId = session.getId();
        when(members.byId(userId)).thenReturn(member("USER", "ACTIVE"));

        filter.doFilter(request, response, chain);

        assertThat(session.getId()).isNotEqualTo(previousId);
        assertSavedIdentity("USER", "ACTIVE");
        verify(chain).doFilter(request, response);
    }

    @Test void directRoleChangeReplacesStaleAdministratorAuthorities() throws Exception {
        MockHttpSession session = signIn("ADMIN", "ACTIVE");
        String previousId = session.getId();
        when(members.byId(userId)).thenReturn(member("USER", "ACTIVE"));

        filter.doFilter(request, response, chain);

        assertThat(session.getId()).isEqualTo(previousId);
        assertSavedIdentity("USER", "ACTIVE");
        verify(chain).doFilter(request, response);
    }

    private MockHttpSession signIn(String role, String status) {
        var context = SecurityContextHolder.createEmptyContext();
        context.setAuthentication(AuthSessionService.authentication(new UserIdentity(userId, role, status)));
        SecurityContextHolder.setContext(context);
        contexts.saveContext(context, request, response);
        return (MockHttpSession) request.getSession(false);
    }

    private MembershipRepository.Member member(String role, String status) {
        Instant now = Instant.parse("2026-10-22T00:00:00Z");
        return new MembershipRepository.Member(userId, "member@example.com", "회원", "구매", "담당", "unused",
                role, status, now, now);
    }

    private void assertInvalidated(MockHttpSession session) throws Exception {
        assertError(401, "UNAUTHENTICATED");
        assertThat(session.isInvalid()).isTrue();
        assertThat(request.getSession(false)).isNull();
        assertThat(SecurityContextHolder.getContext().getAuthentication()).isNull();
        verifyNoInteractions(chain);
    }

    private void assertSavedIdentity(String role, String status) {
        var authentication = SecurityContextHolder.getContext().getAuthentication();
        assertThat(authentication.getPrincipal()).isEqualTo(new UserIdentity(userId, role, status));
        assertThat(authentication.getAuthorities()).extracting("authority")
                .containsExactly("ROLE_" + role, "STATUS_" + status);
        var saved = (SecurityContext) request.getSession(false)
                .getAttribute(HttpSessionSecurityContextRepository.SPRING_SECURITY_CONTEXT_KEY);
        assertThat(saved.getAuthentication()).isEqualTo(authentication);
    }

    private void assertError(int status, String code) throws Exception {
        assertThat(response.getStatus()).isEqualTo(status);
        var body = mapper.readTree(response.getContentAsString());
        assertThat(body.path("request_id").stringValue()).isEqualTo(requestId.toString());
        assertThat(body.at("/error/code").stringValue()).isEqualTo(code);
        assertThat(body.at("/error/retryable").booleanValue()).isFalse();
    }
}
