package com.negopilot.backend.auth;

import java.util.Map;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.junit.jupiter.params.provider.ValueSource;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class AuthBootstrapLocalUserTest {
    private static final String PASSWORD = "LocalUser2026!";

    @Test void normalizesEmailAndUsesFixedTestProfileWithoutEchoingCredentials() {
        var input = AuthBootstrapLocalUser.inputFromEnvironment(Map.of(
                "LOCAL_USER_EMAIL", "  User@NegoPilot.local  ", "LOCAL_USER_PASSWORD", PASSWORD));
        assertThat(input.email()).isEqualTo("user@negopilot.local");
        assertThat(input.name()).isEqualTo("로컬 테스트 회원");
        assertThat(input.department()).isEqualTo("개발팀");
        assertThat(input.jobTitle()).isEqualTo("테스트");
        assertThat(input.toString()).doesNotContain(PASSWORD, input.email());
    }

    @Test void requiresBothEnvironmentVariablesAndHasNoFallbackCredentials() {
        for (var environment : java.util.List.of(Map.<String, String>of(),
                Map.of("LOCAL_USER_EMAIL", "user@example.com"), Map.of("LOCAL_USER_PASSWORD", PASSWORD),
                Map.of("LOCAL_USER_EMAIL", " ", "LOCAL_USER_PASSWORD", PASSWORD),
                Map.of("LOCAL_USER_EMAIL", "user@example.com", "LOCAL_USER_PASSWORD", " "))) {
            assertThatThrownBy(() -> AuthBootstrapLocalUser.inputFromEnvironment(environment))
                    .isInstanceOf(IllegalArgumentException.class).hasMessageContaining("are required")
                    .hasMessageNotContaining(PASSWORD);
        }
    }

    @ParameterizedTest
    @CsvSource({"invalid-email,LocalUser2026!", "user@example.com,short", "user@example.com,NoDigitsHere!"})
    void rejectsInvalidCredentialsWithoutTheirValuesInTheException(String email, String password) {
        assertThatThrownBy(() -> AuthBootstrapLocalUser.inputFromEnvironment(Map.of(
                "LOCAL_USER_EMAIL", email, "LOCAL_USER_PASSWORD", password)))
                .isInstanceOf(IllegalArgumentException.class).hasMessageContaining("membership validation rules")
                .hasMessageNotContaining(email).hasMessageNotContaining(password).hasNoCause();
    }

    @ParameterizedTest
    @ValueSource(strings = {"--password=SecretValue1!", "--role=ADMIN", "--email=user@example.com"})
    void rejectsCommandLineValuesBeforeStartingSpring(String argument) {
        assertThatThrownBy(() -> AuthBootstrapLocalUser.main(new String[] {argument}))
                .isInstanceOf(IllegalArgumentException.class).hasMessageNotContaining(argument);
    }
}
