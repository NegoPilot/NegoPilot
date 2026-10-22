package com.negopilot.backend.supplier;

import java.util.UUID;
import org.springframework.stereotype.Service;
import tools.jackson.databind.JsonNode;

/** Live business-mcp lookup through the Python /internal/v2 routes. */
@Service
final class SupplierLookupGateway {
    private final SupplierGatewayClient client;
    private final SupplierLookupResponseValidator validator;

    SupplierLookupGateway(SupplierGatewayClient client, SupplierLookupResponseValidator validator) {
        this.client = client;
        this.validator = validator;
    }

    JsonNode catalog(UUID requestId) {
        return client.get("/internal/v2/business/catalog", requestId, validator::validateCatalog);
    }

    JsonNode search(JsonNode filters, UUID requestId) {
        return client.search("/internal/v2/suppliers", filters, requestId,
                body -> validator.validateSearch(body, filters));
    }

    JsonNode detail(String supplierId, UUID requestId) {
        return client.detail("/internal/v2/suppliers/", supplierId, requestId,
                body -> validator.validateDetail(body, supplierId));
    }
}
