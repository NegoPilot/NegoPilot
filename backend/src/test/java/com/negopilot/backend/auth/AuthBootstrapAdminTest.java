package com.negopilot.backend.auth;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class AuthBootstrapAdminTest {
    @Test void ordinaryCliKeepsStrictBehaviorByDefault() {
        var arguments = AuthBootstrapAdmin.parseArguments(new String[] {
                "--email=admin@example.com", "--name=Admin", "--department=Operations"});
        assertThat(arguments.ifAbsent()).isFalse();
        assertThat(arguments.values()).containsEntry("email", "admin@example.com");
    }

    @Test void idempotentBehaviorRequiresExplicitFlag() {
        var arguments = AuthBootstrapAdmin.parseArguments(new String[] {
                "--if-absent", "--email=admin@example.com", "--name=Admin", "--department=Operations"});
        assertThat(arguments.ifAbsent()).isTrue();
        assertThat(arguments.values()).doesNotContainKey("if-absent");
    }

    @ParameterizedTest
    @ValueSource(strings = {"--if-absent=true", "--password=secret", "--role=ADMIN", "--unknown"})
    void rejectsUnsupportedArgumentsWithoutEchoingTheirValues(String argument) {
        assertThatThrownBy(() -> AuthBootstrapAdmin.parseArguments(new String[] {argument}))
                .isInstanceOf(IllegalArgumentException.class).hasMessageNotContaining(argument);
    }

    @Test void rejectsDuplicateFlagOrField() {
        assertThatThrownBy(() -> AuthBootstrapAdmin.parseArguments(new String[] {"--if-absent", "--if-absent"}))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> AuthBootstrapAdmin.parseArguments(new String[] {"--email=a@example.com", "--email=b@example.com"}))
                .isInstanceOf(IllegalArgumentException.class);
    }
}
