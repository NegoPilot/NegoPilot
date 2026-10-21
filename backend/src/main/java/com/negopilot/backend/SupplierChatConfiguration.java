package com.negopilot.backend;

import java.io.IOException;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.io.ClassPathResource;
import tools.jackson.databind.json.JsonMapper;

@Configuration(proxyBeanMethods = false)
class SupplierChatConfiguration {
    @Bean
    SupplierChatResponseValidator supplierChatResponseValidator(
            JsonMapper mapper, @Value("${agent.execution-mode:live}") String mode) throws IOException {
        try (var input = new ClassPathResource("fixtures/supplier-chat.json").getInputStream()) {
            return new SupplierChatResponseValidator(mapper.readTree(input), !"stub".equals(mode));
        }
    }
}
