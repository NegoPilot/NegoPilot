package com.negopilot.backend.auth;

import com.negopilot.backend.common.api.ApiException;
import java.time.Instant;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.SimpleTransactionStatus;
import org.springframework.transaction.support.TransactionTemplate;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.inOrder;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class AuthServiceTest {
    private final MembershipRepository members = mock(MembershipRepository.class);
    private final PasswordEncoder passwords = mock(PasswordEncoder.class);
    private final PlatformTransactionManager manager = mock(PlatformTransactionManager.class);
    private final UUID id = UUID.randomUUID();
    private final AuthRequestValidator.Login input = new AuthRequestValidator.Login("user@example.com", "Password1!");
    private AuthService service;

    @BeforeEach void setUp() {
        when(passwords.encode(anyString())).thenReturn("dummy-hash");
        when(manager.getTransaction(any())).thenReturn(new SimpleTransactionStatus());
        service = new AuthService(members, passwords, new TransactionTemplate(manager));
    }

    @Test void unknownEmailChecksDummyHashAndRecordsFailureWithoutUserId() {
        assertInvalidCredentials();

        verify(passwords).matches(input.password(), "dummy-hash");
        verify(members).log(null, "LOGIN_FAILURE", "127.0.0.1", "AuthServiceTest");
        verify(members, never()).lock(any());
        verify(members, never()).log(any(), eq("LOGIN_SUCCESS"), anyString(), anyString());
    }

    @ParameterizedTest
    @ValueSource(strings = {"missing", "new-password", "REJECTED", "DISABLED"})
    void changedCredentialsOrMembershipBeforeLockCannotAuthenticate(String change) {
        when(members.byEmail(input.email())).thenReturn(member("original-hash", "ACTIVE", "Before"));
        when(passwords.matches(input.password(), "original-hash")).thenReturn(true);
        MembershipRepository.Member latest = switch (change) {
            case "missing" -> null;
            case "new-password" -> member("replacement-hash", "ACTIVE", "After");
            default -> member("original-hash", change, "After");
        };
        when(members.lock(id)).thenReturn(latest);

        assertInvalidCredentials();

        var order = inOrder(manager, members);
        order.verify(members).lock(id);
        order.verify(manager).commit(any());
        order.verify(members).log(id, "LOGIN_FAILURE", "127.0.0.1", "AuthServiceTest");
        verify(members, never()).log(any(), eq("LOGIN_SUCCESS"), anyString(), anyString());
    }

    @ParameterizedTest
    @ValueSource(strings = {"PENDING", "ACTIVE"})
    void successfulAuthenticationUsesLockedMemberAndCommitsItsAccessLog(String status) {
        when(members.byEmail(input.email())).thenReturn(member("original-hash", "PENDING", "Before"));
        when(passwords.matches(input.password(), "original-hash")).thenReturn(true);
        var latest = member("original-hash", status, "Updated profile");
        when(members.lock(id)).thenReturn(latest);

        var result = service.authenticate(input, "127.0.0.1", "AuthServiceTest");

        assertThat(result.identity()).isEqualTo(latest.identity());
        assertThat(result.view()).isEqualTo(latest.view());
        var order = inOrder(manager, members);
        order.verify(members).lock(id);
        order.verify(members).log(id, "LOGIN_SUCCESS", "127.0.0.1", "AuthServiceTest");
        order.verify(manager).commit(any());
        verify(members, never()).log(any(), eq("LOGIN_FAILURE"), anyString(), anyString());
    }

    private void assertInvalidCredentials() {
        assertThatThrownBy(() -> service.authenticate(input, "127.0.0.1", "AuthServiceTest"))
                .isInstanceOfSatisfying(ApiException.class, failure -> {
                    assertThat(failure.status.value()).isEqualTo(401);
                    assertThat(failure.code).isEqualTo("INVALID_CREDENTIALS");
                });
    }

    private MembershipRepository.Member member(String hash, String status, String name) {
        Instant now = Instant.parse("2026-10-22T00:00:00Z");
        return new MembershipRepository.Member(id, input.email(), name, "Purchasing", "Manager",
                hash, "USER", status, now, now);
    }
}
