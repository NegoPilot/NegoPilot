package com.negopilot.backend.legacy;

import com.negopilot.backend.common.api.ApiException;
import com.negopilot.backend.supplier.SupplierChatRequestValidator;

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

@org.springframework.context.annotation.Profile("legacy-test")
@RestController
@RequestMapping(produces = MediaType.APPLICATION_JSON_VALUE)
final class SupplierChatController {
    private final SupplierChatService service;
    private final SupplierChatRequestValidator validator;
    private final JsonMapper mapper;

    SupplierChatController(SupplierChatService service, SupplierChatRequestValidator validator, JsonMapper mapper) {
        this.service = service;
        this.validator = validator;
        this.mapper = mapper;
    }

    @GetMapping("/api/v1/business/catalog")
    JsonNode catalog(HttpServletRequest request) {
        UUID id = begin(request);
        noParameters(request, id);
        return service.catalog(id);
    }

    @GetMapping("/api/v1/suppliers")
    JsonNode search(HttpServletRequest request) {
        UUID id = begin(request);
        return service.search(validator.search(request.getParameterMap()), id);
    }

    @GetMapping("/api/v1/suppliers/{supplierId}")
    JsonNode detail(@PathVariable String supplierId, HttpServletRequest request) {
        UUID id = begin(request);
        noParameters(request, id);
        return service.detail(validator.supplierId(supplierId), id);
    }

    @PostMapping(value = "/api/v1/conversations", consumes = MediaType.APPLICATION_JSON_VALUE)
    ResponseEntity<JsonNode> create(@RequestBody(required = false) String raw, HttpServletRequest request) {
        UUID id = begin(request);
        noParameters(request, id);
        return ResponseEntity.status(201).body(service.create(validator.create(parse(raw, id)), id));
    }

    @GetMapping("/api/v1/conversations/{conversationId}")
    JsonNode get(@PathVariable String conversationId, HttpServletRequest request) {
        UUID id = begin(request);
        noParameters(request, id);
        return service.get(validator.conversationId(conversationId), id);
    }

    @PostMapping(value = "/api/v1/conversations/{conversationId}/messages", consumes = MediaType.APPLICATION_JSON_VALUE)
    JsonNode message(@PathVariable String conversationId, @RequestBody(required = false) String raw,
                     HttpServletRequest request) {
        UUID id = begin(request);
        noParameters(request, id);
        return service.message(validator.conversationId(conversationId), validator.message(parse(raw, id)), id);
    }

    private UUID begin(HttpServletRequest request) {
        UUID id = UUID.randomUUID();
        request.setAttribute("request_id", id);
        service.requireDemo(id);
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
