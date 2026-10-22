package com.negopilot.backend.supplier;

import com.negopilot.backend.agent.AgentHttpConfiguration;
import com.negopilot.backend.agent.AgentProperties;
import com.negopilot.backend.common.http.AgentHttpTransport;
import java.net.URI;
import java.time.Duration;
import org.junit.jupiter.api.Test;
import org.springframework.context.annotation.AnnotationConfigApplicationContext;

import static org.assertj.core.api.Assertions.assertThat;

class SupplierGatewayProfileTest {
    @Test
    void productionLookupDoesNotLoadLegacyFixtureBeans() {
        try (var context = context(false)) {
            assertThat(context.getBeansOfType(SupplierLookupGateway.class)).hasSize(1);
            assertThat(context.getBeansOfType(SupplierLookupController.class)).hasSize(1);
            assertThat(context.getBeansOfType(SupplierChatGateway.class)).isEmpty();
            assertThat(context.getBeansOfType(SupplierChatConfiguration.class)).isEmpty();
            assertThat(context.getBeansOfType(SupplierChatResponseValidator.class)).isEmpty();
        }
    }

    @Test
    void legacyProfileProvidesLiveLookupAndFixtureAdapterTogether() {
        try (var context = context(true)) {
            assertThat(context.getBeansOfType(SupplierLookupGateway.class)).hasSize(1);
            assertThat(context.getBeansOfType(SupplierChatGateway.class)).hasSize(1);
            assertThat(context.getBeansOfType(SupplierChatResponseValidator.class)).hasSize(1);
        }
    }

    private AnnotationConfigApplicationContext context(boolean legacy) {
        var context = new AnnotationConfigApplicationContext();
        if (legacy) context.getEnvironment().setActiveProfiles("legacy-test");
        context.registerBean(AgentProperties.class, () -> new AgentProperties(
                URI.create("http://127.0.0.1:1"), "test-key", Duration.ofSeconds(1), Duration.ofSeconds(1)));
        context.register(AgentHttpConfiguration.class, AgentHttpTransport.class, SupplierGatewayClient.class,
                SupplierLookupGateway.class, SupplierLookupController.class, SupplierLookupResponseValidator.class,
                SupplierChatRequestValidator.class, SupplierChatGateway.class, SupplierChatConfiguration.class);
        context.refresh();
        return context;
    }
}
