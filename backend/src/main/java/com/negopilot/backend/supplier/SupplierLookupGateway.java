package com.negopilot.backend.supplier;

import com.negopilot.backend.common.http.InternalPaths;
import java.util.UUID;
import org.springframework.stereotype.Service;
import tools.jackson.databind.JsonNode;

/** Live business-mcp lookup through the Python internal routes. */
@Service
final class SupplierLookupGateway {
    private final SupplierGatewayClient client;
    private final SupplierLookupResponseValidator validator;

    SupplierLookupGateway(SupplierGatewayClient client, SupplierLookupResponseValidator validator) {
        this.client = client;
        this.validator = validator;
    }

    JsonNode catalog(UUID requestId) {
        return client.get(InternalPaths.BUSINESS_CATALOG, requestId, validator::validateCatalog);
    }

    JsonNode search(JsonNode filters, UUID requestId) {
        return client.search(InternalPaths.SUPPLIERS, filters, requestId,
                body -> validator.validateSearch(body, filters));
    }

    JsonNode detail(String supplierId, UUID requestId) {
        return client.detail(InternalPaths.SUPPLIERS + "/", supplierId, requestId,
                body -> validator.validateDetail(body, supplierId));
    }
}
