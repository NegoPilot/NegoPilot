package com.negopilot.backend.agent;

import java.util.Map;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
final class HealthController {
    @GetMapping(value = "/health", produces = MediaType.APPLICATION_JSON_VALUE)
    Map<String, String> health() {
        return Map.of("status", "ok");
    }
}
