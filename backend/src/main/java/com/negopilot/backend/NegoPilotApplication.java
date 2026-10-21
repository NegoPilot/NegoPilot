package com.negopilot.backend;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.EnableConfigurationProperties;

@SpringBootApplication
@EnableConfigurationProperties(AgentProperties.class)
public class NegoPilotApplication {
    public static void main(String[] args) {
        SpringApplication.run(NegoPilotApplication.class, args);
    }
}
