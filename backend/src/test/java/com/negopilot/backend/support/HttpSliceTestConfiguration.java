package com.negopilot.backend.support;

import com.negopilot.backend.agent.AgentProperties;

import org.springframework.boot.SpringBootConfiguration;
import org.springframework.boot.autoconfigure.EnableAutoConfiguration;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.boot.test.context.TestComponent;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.ComponentScan;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.web.SecurityFilterChain;

/**
 * Starts the agent HTTP layer without the application DB or membership security.
 * Tests add the controller under test; authentication is covered by the PostgreSQL membership tests.
 * It is the boot configuration so Spring Boot does not also load NegoPilotApplication, and
 * {@code @TestComponent} keeps the application's own component scan from picking it up.
 */
@SpringBootConfiguration(proxyBeanMethods = false)
@TestComponent
@EnableAutoConfiguration(excludeName = {
        "org.springframework.boot.jdbc.autoconfigure.DataSourceAutoConfiguration",
        "org.springframework.boot.flyway.autoconfigure.FlywayAutoConfiguration",
        "org.springframework.boot.session.jdbc.autoconfigure.JdbcSessionAutoConfiguration"})
@EnableConfigurationProperties(AgentProperties.class)
@ComponentScan(basePackages = {"com.negopilot.backend.common", "com.negopilot.backend.agent"})
public class HttpSliceTestConfiguration {
    @Bean
    SecurityFilterChain sliceTestSecurity(HttpSecurity http) throws Exception {
        return http.csrf(csrf -> csrf.disable())
                .formLogin(login -> login.disable()).httpBasic(basic -> basic.disable()).logout(logout -> logout.disable())
                .authorizeHttpRequests(access -> access.anyRequest().permitAll()).build();
    }
}
