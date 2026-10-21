package com.negopilot.backend;

import jakarta.servlet.http.HttpServletRequest;
import java.util.Map;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

@RestController
final class AgentController {
    private final QueryRequestValidator validator;
    private final AgentGateway gateway;
    private final JsonMapper mapper;

    AgentController(QueryRequestValidator validator, AgentGateway gateway, JsonMapper mapper) {
        this.validator = validator;
        this.gateway = gateway;
        this.mapper = mapper;
    }

    @GetMapping(value = "/health", produces = MediaType.APPLICATION_JSON_VALUE)
    Map<String, String> health() {
        return Map.of("status", "ok");
    }

    @PostMapping(value = "/api/v1/agent/queries", consumes = MediaType.APPLICATION_JSON_VALUE,
            produces = MediaType.APPLICATION_JSON_VALUE)
    JsonNode query(@RequestBody(required = false) String raw, HttpServletRequest servletRequest) {
        JsonNode body;
        try {
            body = raw == null ? null : mapper.readTree(raw);
        } catch (RuntimeException ex) {
            throw ApiException.validation(null);
        }
        var request = validator.validate(body);
        servletRequest.setAttribute("request_id", request.requestId());
        return gateway.execute(request);
    }
}
