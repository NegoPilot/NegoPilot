package com.negopilot.backend.supplier;

import java.util.UUID;
import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Service;
import tools.jackson.databind.JsonNode;

/** Fixture-backed upstream adapter for the legacy-test conversation API. */
@Service
@Profile("legacy-test")
public final class SupplierChatGateway {
    private final SupplierGatewayClient client;
    private final SupplierChatResponseValidator validator;

    SupplierChatGateway(SupplierGatewayClient client, SupplierChatResponseValidator validator) {
        this.client = client;
        this.validator = validator;
    }

    public JsonNode catalog(UUID requestId) {
        return client.get("/internal/v1/business/catalog", requestId, validator::validateCatalog);
    }

    public JsonNode search(JsonNode filters, UUID requestId) {
        return client.search("/internal/v1/suppliers", filters, requestId,
                body -> validator.validateSearch(body, filters));
    }

    public JsonNode detail(String supplierId, UUID requestId) {
        return client.detail("/internal/v1/suppliers/", supplierId, requestId,
                body -> validator.validateDetail(body, supplierId));
    }

    public JsonNode chat(JsonNode request, UUID requestId, UUID conversationId, JsonNode context, String query) {
        return client.post("/internal/v1/chat/responses", request, requestId,
                body -> validator.validateChat(body, requestId, conversationId, context, query));
    }
}
