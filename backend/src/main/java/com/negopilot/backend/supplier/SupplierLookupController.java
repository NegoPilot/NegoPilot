package com.negopilot.backend.supplier;

import com.negopilot.backend.common.api.ApiException;
import com.negopilot.backend.common.api.ApiPaths;

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
    private final SupplierLookupGateway gateway;
    private final SupplierLookupRequestValidator validator;

    SupplierLookupController(SupplierLookupGateway gateway, SupplierLookupRequestValidator validator) {
        this.gateway = gateway;
        this.validator = validator;
    }

    @GetMapping(ApiPaths.BUSINESS_CATALOG)
    JsonNode catalog(HttpServletRequest request) {
        UUID id = begin(request);
        noParameters(request, id);
        return gateway.catalog(id);
    }

    @GetMapping(ApiPaths.SUPPLIERS)
    JsonNode search(HttpServletRequest request) {
        UUID id = begin(request);
        return gateway.search(validator.search(request.getParameterMap()), id);
    }

    @GetMapping(ApiPaths.SUPPLIERS + "/{supplierId}")
    JsonNode detail(@PathVariable String supplierId, HttpServletRequest request) {
        UUID id = begin(request);
        noParameters(request, id);
        return gateway.detail(validator.supplierId(supplierId), id);
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
