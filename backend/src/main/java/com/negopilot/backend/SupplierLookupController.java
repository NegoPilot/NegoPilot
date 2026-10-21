package com.negopilot.backend;

import jakarta.servlet.http.HttpServletRequest;
import java.util.UUID;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import tools.jackson.databind.JsonNode;

/** v2 live supplier lookup. Python reads business-mcp regardless of the execution mode. */
@RestController
@RequestMapping(produces = MediaType.APPLICATION_JSON_VALUE)
final class SupplierLookupController {
    private final SupplierChatGateway gateway;
    private final SupplierChatRequestValidator validator;

    SupplierLookupController(SupplierChatGateway gateway, SupplierChatRequestValidator validator) {
        this.gateway = gateway;
        this.validator = validator;
    }

    @GetMapping("/api/v2/business/catalog")
    JsonNode catalog(HttpServletRequest request) {
        UUID id = begin(request);
        noParameters(request, id);
        return gateway.liveCatalog(id);
    }

    @GetMapping("/api/v2/suppliers")
    JsonNode search(HttpServletRequest request) {
        UUID id = begin(request);
        return gateway.liveSearch(validator.search(request.getParameterMap()), id);
    }

    @GetMapping("/api/v2/suppliers/{supplierId}")
    JsonNode detail(@PathVariable String supplierId, HttpServletRequest request) {
        UUID id = begin(request);
        noParameters(request, id);
        return gateway.liveDetail(validator.supplierId(supplierId), id);
    }

    private static UUID begin(HttpServletRequest request) {
        UUID id = UUID.randomUUID();
        request.setAttribute("request_id", id);
        return id;
    }

    private static void noParameters(HttpServletRequest request, UUID requestId) {
        if (!request.getParameterMap().isEmpty()) throw ApiException.validation(requestId);
    }
}
