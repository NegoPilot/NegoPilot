package com.negopilot.backend.chat;

import com.negopilot.backend.common.api.ApiException;
import com.negopilot.backend.auth.CurrentUser;
import com.negopilot.backend.agent.GenerationGateway;
import com.negopilot.backend.agent.GenerationRequestValidator;

import jakarta.servlet.http.HttpServletRequest;
import java.util.Collections;
import java.util.Set;
import java.util.UUID;
import org.springframework.context.annotation.Profile;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

@Profile("!legacy-test")
@RestController
@RequestMapping(produces = MediaType.APPLICATION_JSON_VALUE)
final class PersistentConversationController {
    private final PersistentConversationService conversations;
    private final GenerationRequestValidator validator;
    private final GenerationGateway gateway;
    private final CurrentUser currentUser;
    private final JsonMapper mapper;

    PersistentConversationController(PersistentConversationService conversations, GenerationRequestValidator validator,
                                     GenerationGateway gateway, CurrentUser currentUser, JsonMapper mapper) {
        this.conversations = conversations;
        this.validator = validator;
        this.gateway = gateway;
        this.currentUser = currentUser;
        this.mapper = mapper;
    }

    @PostMapping(value = "/api/v2/agent/queries", consumes = MediaType.APPLICATION_JSON_VALUE)
    JsonNode query(@RequestBody(required = false) String raw, HttpServletRequest request) {
        currentUser.requireActive();
        UUID requestId = begin(request, Set.of());
        var input = validator.validateQuery(parse(raw, requestId));
        request.setAttribute("request_id", input.requestId());
        return gateway.execute(input.requestId(), input.body());
    }

    @PostMapping(value = "/api/v2/conversations", consumes = MediaType.APPLICATION_JSON_VALUE)
    ResponseEntity<JsonNode> create(@RequestBody(required = false) String raw, HttpServletRequest request) {
        UUID owner = currentUser.requireActive().userId();
        UUID requestId = begin(request, Set.of());
        return ResponseEntity.status(201).body(conversations.create(owner, validator.create(parse(raw, requestId)), requestId));
    }

    @GetMapping("/api/v2/conversations")
    JsonNode list(HttpServletRequest request) {
        UUID owner = currentUser.requireActive().userId();
        UUID requestId = begin(request, Set.of("cursor", "size"));
        return conversations.list(owner, request.getParameter("cursor"), size(request, 20, requestId), requestId);
    }

    @GetMapping("/api/v2/conversations/{conversationId}")
    JsonNode get(@PathVariable String conversationId, HttpServletRequest request) {
        UUID owner = currentUser.requireActive().userId();
        UUID requestId = begin(request, Set.of());
        return conversations.get(owner, id(conversationId), requestId);
    }

    @PatchMapping(value = "/api/v2/conversations/{conversationId}", consumes = MediaType.APPLICATION_JSON_VALUE)
    JsonNode rename(@PathVariable String conversationId, @RequestBody(required = false) String raw,
                    HttpServletRequest request) {
        UUID owner = currentUser.requireActive().userId();
        UUID requestId = begin(request, Set.of());
        JsonNode body = parse(raw, requestId);
        if (body == null || !body.isObject() || body.size() != 1 || !body.path("title").isString()) {
            throw ApiException.validation(requestId);
        }
        String title = body.get("title").stringValue().strip();
        if (title.isBlank() || title.indexOf('\0') >= 0 || title.codePointCount(0, title.length()) > 100) throw ApiException.validation(requestId);
        return conversations.rename(owner, id(conversationId), title, requestId);
    }

    @DeleteMapping("/api/v2/conversations/{conversationId}")
    ResponseEntity<Void> delete(@PathVariable String conversationId, HttpServletRequest request) {
        UUID owner = currentUser.requireActive().userId();
        UUID requestId = begin(request, Set.of());
        conversations.delete(owner, id(conversationId), requestId);
        return ResponseEntity.noContent().build();
    }

    @GetMapping("/api/v2/conversations/{conversationId}/messages")
    JsonNode messages(@PathVariable String conversationId, HttpServletRequest request) {
        UUID owner = currentUser.requireActive().userId();
        UUID requestId = begin(request, Set.of("before_seq", "size"));
        Long before = null;
        if (request.getParameter("before_seq") != null) {
            try {
                String raw = request.getParameter("before_seq");
                if (!raw.matches("[1-9][0-9]{0,18}")) throw new IllegalArgumentException();
                before = Long.parseLong(raw);
            } catch (IllegalArgumentException ex) { throw ApiException.validation(requestId); }
        }
        return conversations.messages(owner, id(conversationId), before, size(request, 50, requestId), requestId);
    }

    @GetMapping("/api/v2/conversations/{conversationId}/responses/{responseId}")
    JsonNode response(@PathVariable String conversationId, @PathVariable String responseId, HttpServletRequest request) {
        UUID owner = currentUser.requireActive().userId();
        UUID requestId = begin(request, Set.of());
        return conversations.response(owner, id(conversationId), id(responseId), requestId);
    }

    @PostMapping(value = "/api/v2/conversations/{conversationId}/messages", consumes = MediaType.APPLICATION_JSON_VALUE)
    JsonNode message(@PathVariable String conversationId, @RequestBody(required = false) String raw,
                     HttpServletRequest request) {
        UUID owner = currentUser.requireActive().userId();
        UUID requestId = begin(request, Set.of());
        var keys = Collections.list(request.getHeaders("Idempotency-Key"));
        if (keys.size() > 1) throw ApiException.validation(requestId);
        UUID key = keys.isEmpty() ? null : id(keys.getFirst());
        return conversations.message(owner, id(conversationId), validator.message(parse(raw, requestId)), key, requestId);
    }

    private UUID id(String raw) { return UUID.fromString(validator.conversationId(raw)); }

    private UUID begin(HttpServletRequest request, Set<String> allowed) {
        UUID id = UUID.randomUUID();
        request.setAttribute("request_id", id);
        for (var entry : request.getParameterMap().entrySet()) {
            if (!allowed.contains(entry.getKey()) || entry.getValue().length != 1) throw ApiException.validation(id);
        }
        return id;
    }

    private int size(HttpServletRequest request, int fallback, UUID requestId) {
        String raw = request.getParameter("size");
        if (raw == null) return fallback;
        if (!raw.matches("[1-9][0-9]{0,2}")) throw ApiException.validation(requestId);
        int value = Integer.parseInt(raw);
        if (value > 100) throw ApiException.validation(requestId);
        return value;
    }

    private JsonNode parse(String raw, UUID requestId) {
        try { return raw == null ? null : mapper.readTree(raw); }
        catch (RuntimeException failure) { throw ApiException.validation(requestId); }
    }
}
