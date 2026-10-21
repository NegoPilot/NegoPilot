package com.negopilot.backend;

import jakarta.servlet.http.HttpServletRequest;
import java.util.UUID;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

@RestController
@RequestMapping(produces = MediaType.APPLICATION_JSON_VALUE)
final class GenerationController {
    private final GenerationRequestValidator validator;
    private final GenerationGateway gateway;
    private final GenerationConversationService conversations;
    private final JsonMapper mapper;

    GenerationController(GenerationRequestValidator validator, GenerationGateway gateway,
                         GenerationConversationService conversations, JsonMapper mapper) {
        this.validator = validator;
        this.gateway = gateway;
        this.conversations = conversations;
        this.mapper = mapper;
    }

    @PostMapping(value = "/api/v2/agent/queries", consumes = MediaType.APPLICATION_JSON_VALUE)
    JsonNode query(@RequestBody(required = false) String raw, HttpServletRequest request) {
        var input = validator.validateQuery(parse(raw, null));
        request.setAttribute("request_id", input.requestId());
        noParameters(request, input.requestId());
        return gateway.execute(input.requestId(), input.body());
    }

    @PostMapping(value = "/api/v2/conversations", consumes = MediaType.APPLICATION_JSON_VALUE)
    ResponseEntity<JsonNode> create(@RequestBody(required = false) String raw, HttpServletRequest request) {
        UUID id = begin(request);
        return ResponseEntity.status(201).body(conversations.create(validator.create(parse(raw, id)), id));
    }

    @GetMapping("/api/v2/conversations/{conversationId}")
    JsonNode get(@PathVariable String conversationId, HttpServletRequest request) {
        UUID id = begin(request);
        return conversations.get(validator.conversationId(conversationId), id);
    }

    @PostMapping(value = "/api/v2/conversations/{conversationId}/messages", consumes = MediaType.APPLICATION_JSON_VALUE)
    JsonNode message(@PathVariable String conversationId, @RequestBody(required = false) String raw,
                     HttpServletRequest request) {
        UUID id = begin(request);
        return conversations.message(validator.conversationId(conversationId), validator.message(parse(raw, id)), id);
    }

    private UUID begin(HttpServletRequest request) {
        UUID id = UUID.randomUUID();
        request.setAttribute("request_id", id);
        noParameters(request, id);
        return id;
    }

    private JsonNode parse(String raw, UUID requestId) {
        try { return raw == null ? null : mapper.readTree(raw); }
        catch (RuntimeException failure) { throw ApiException.validation(requestId); }
    }

    private void noParameters(HttpServletRequest request, UUID requestId) {
        if (!request.getParameterMap().isEmpty()) throw ApiException.validation(requestId);
    }
}
