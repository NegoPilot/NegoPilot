package com.negopilot.backend.supplier;

import com.negopilot.backend.common.api.ApiException;
import com.negopilot.backend.common.api.RequestIds;
import com.negopilot.backend.common.http.AgentHttpTransport;
import java.net.URI;
import java.util.HashMap;
import java.util.Set;
import java.util.UUID;
import java.util.function.Consumer;
import org.springframework.http.HttpMethod;
import org.springframework.stereotype.Component;
import org.springframework.web.util.UriComponentsBuilder;
import tools.jackson.databind.JsonNode;

/** Supplier URI encoding and error rules shared by live lookup and legacy chat. */
@Component
final class SupplierGatewayClient {
    private static final Set<Integer> STATUSES = Set.of(200, 400, 404, 503, 504);
    private final AgentHttpTransport transport;

    SupplierGatewayClient(AgentHttpTransport transport) { this.transport = transport; }

    JsonNode get(String path, UUID requestId, Consumer<JsonNode> validate) {
        return exchange(HttpMethod.GET, transport.uri(path), null, requestId, validate);
    }

    JsonNode search(String path, JsonNode filters, UUID requestId, Consumer<JsonNode> validate) {
        var uri = UriComponentsBuilder.fromUri(transport.uri(path));
        var variables = new HashMap<String, String>();
        for (String field : filters.propertyNames()) {
            uri.queryParam(field, "{" + field + "}");
            variables.put(field, filters.get(field).asString());
        }
        return exchange(HttpMethod.GET, uri.encode().buildAndExpand(variables).toUri(), null, requestId, validate);
    }

    JsonNode detail(String path, String supplierId, UUID requestId, Consumer<JsonNode> validate) {
        URI uri = UriComponentsBuilder.fromUri(transport.uri(path))
                .pathSegment("{supplierId}").encode().buildAndExpand(supplierId).toUri();
        return exchange(HttpMethod.GET, uri, null, requestId, validate);
    }

    JsonNode post(String path, JsonNode request, UUID requestId, Consumer<JsonNode> validate) {
        return exchange(HttpMethod.POST, transport.uri(path), request, requestId, validate);
    }

    private JsonNode exchange(HttpMethod method, URI uri, JsonNode request, UUID requestId,
                              Consumer<JsonNode> validate) {
        return transport.exchange(method, uri, request, requestId, STATUSES, (status, body) -> {
            if (status != 200) throw mapError(body, status, requestId, request != null);
            try { validate.accept(body); }
            catch (RuntimeException failure) { throw ApiException.invalidResponse(requestId); }
            return body;
        });
    }

    private static ApiException mapError(JsonNode body, int status, UUID requestId, boolean idWasSent) {
        if (body == null || !body.isObject() || body.size() != 2 || !body.has("request_id") || !body.has("error")) {
            return ApiException.invalidResponse(requestId);
        }
        JsonNode upstreamId = body.get("request_id");
        if (!upstreamId.isNull()) {
            UUID decoded = RequestIds.parse(upstreamId);
            if (decoded == null || (idWasSent && !requestId.equals(decoded))) return ApiException.invalidResponse(requestId);
        }
        JsonNode error = body.get("error");
        if (!error.isObject() || error.size() != 3 || !error.path("code").isString()
                || !error.path("message").isString() || error.path("message").stringValue().isBlank()
                || !error.path("retryable").isBoolean()) return ApiException.invalidResponse(requestId);
        String code = error.get("code").stringValue();
        ApiException mapped = switch (status) {
            case 400 -> code.equals("VALIDATION_ERROR") ? ApiException.validation(requestId) : null;
            case 404 -> code.equals("SUPPLIER_NOT_FOUND") ? ApiException.supplierNotFound(requestId) : null;
            case 503 -> switch (code) {
                case "DEMO_MODE_REQUIRED" -> ApiException.demoRequired(requestId);
                case "UPSTREAM_UNAVAILABLE" -> ApiException.unavailable(requestId);
                default -> null;
            };
            case 504 -> code.equals("UPSTREAM_TIMEOUT") ? ApiException.timeout(requestId) : null;
            default -> null;
        };
        if (mapped == null || mapped.retryable != error.get("retryable").booleanValue()) {
            return ApiException.invalidResponse(requestId);
        }
        return mapped;
    }

}
