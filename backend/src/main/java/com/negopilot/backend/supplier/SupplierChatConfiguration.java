package com.negopilot.backend.supplier;

import java.io.IOException;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Profile;
import org.springframework.core.io.ClassPathResource;
import tools.jackson.databind.json.JsonMapper;

@Configuration(proxyBeanMethods = false)
@Profile("legacy-test")
class SupplierChatConfiguration {
    @Bean
    SupplierChatResponseValidator supplierChatResponseValidator(JsonMapper mapper) throws IOException {
        try (var input = new ClassPathResource("fixtures/supplier-chat.json").getInputStream()) {
            return new SupplierChatResponseValidator(mapper.readTree(input));
        }
    }
}
