package com.negopilot.backend;

import java.io.IOException;
import java.net.SocketTimeoutException;
import java.net.URI;
import java.net.http.HttpTimeoutException;
import java.util.Set;
import java.util.HashMap;
import java.util.UUID;
import java.util.function.Consumer;
import org.apache.hc.core5.http.ConnectionRequestTimeoutException;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Service;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;
import org.springframework.web.util.UriComponentsBuilder;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/** Uses the existing transport with retries and redirects disabled. */
@Service
final class SupplierChatGateway {
    private final RestClient client;
    private final AgentProperties properties;
    private final JsonMapper mapper;
    private final SupplierChatResponseValidator validator;
    private final SupplierLookupResponseValidator liveValidator;

    SupplierChatGateway(RestClient agentRestClient, AgentProperties properties, JsonMapper mapper,
                        SupplierChatResponseValidator validator, SupplierLookupResponseValidator liveValidator) {
        this.client = agentRestClient;
        this.properties = properties;
        this.mapper = mapper;
        this.validator = validator;
        this.liveValidator = liveValidator;
    }

    JsonNode catalog(UUID requestId) {
        return exchange(HttpMethod.GET, path("/internal/v1/business/catalog"), null, requestId,
                validator::validateCatalog);
    }

    JsonNode search(JsonNode filters, UUID requestId) {
        return exchange(HttpMethod.GET, searchUri("/internal/v1/suppliers", filters), null, requestId,
                body -> validator.validateSearch(body, filters));
    }

    JsonNode detail(String supplierId, UUID requestId) {
        return exchange(HttpMethod.GET, detailUri("/internal/v1/suppliers/", supplierId), null, requestId,
                body -> validator.validateDetail(body, supplierId));
    }

    // v2 live lookups: same transport, business-mcp data validated by shape and filters.
    JsonNode liveCatalog(UUID requestId) {
        return exchange(HttpMethod.GET, path("/internal/v2/business/catalog"), null, requestId,
                liveValidator::validateCatalog);
    }

    JsonNode liveSearch(JsonNode filters, UUID requestId) {
        return exchange(HttpMethod.GET, searchUri("/internal/v2/suppliers", filters), null, requestId,
                body -> liveValidator.validateSearch(body, filters));
    }

    JsonNode liveDetail(String supplierId, UUID requestId) {
        return exchange(HttpMethod.GET, detailUri("/internal/v2/suppliers/", supplierId), null, requestId,
                body -> liveValidator.validateDetail(body, supplierId));
    }

    private URI searchUri(String path, JsonNode filters) {
        var uri = UriComponentsBuilder.fromUri(properties.baseUrl()).replacePath(path);
        var variables = new HashMap<String, String>();
        for (String field : filters.propertyNames()) {
            uri.queryParam(field, "{" + field + "}");
            variables.put(field, filters.get(field).asString());
        }
        return uri.encode().buildAndExpand(variables).toUri();
    }

    private URI detailUri(String path, String supplierId) {
        return UriComponentsBuilder.fromUri(properties.baseUrl()).replacePath(path)
                .pathSegment("{supplierId}").encode().buildAndExpand(supplierId).toUri();
    }

    JsonNode chat(JsonNode request, UUID requestId, UUID conversationId, JsonNode context, String query) {
        return exchange(HttpMethod.POST, path("/internal/v1/chat/responses"), request, requestId,
                body -> validator.validateChat(body, requestId, conversationId, context, query));
    }

    private URI path(String path) { return properties.baseUrl().resolve(path); }

    private JsonNode exchange(HttpMethod method, URI uri, JsonNode request, UUID requestId,
                              Consumer<JsonNode> validate) {
        if (properties.internalApiKey().isBlank()) throw ApiException.unavailable(requestId);
        try {
            var call = client.method(method).uri(uri).accept(MediaType.APPLICATION_JSON)
                    .header("X-Internal-Api-Key", properties.internalApiKey());
            if (request != null) call.contentType(MediaType.APPLICATION_JSON).body(mapper.writeValueAsBytes(request));
            return call.exchange((ignored, response) -> {
                int status = response.getStatusCode().value();
                if (!Set.of(200, 400, 404, 503, 504).contains(status)) throw ApiException.invalidResponse(requestId);
                MediaType contentType;
                try { contentType = response.getHeaders().getContentType(); }
                catch (IllegalArgumentException failure) { throw ApiException.invalidResponse(requestId); }
                if (contentType == null || !MediaType.APPLICATION_JSON.isCompatibleWith(contentType)) {
                    throw ApiException.invalidResponse(requestId);
                }
                JsonNode body;
                try { body = mapper.readTree(response.getBody().readAllBytes()); }
                catch (IOException failure) {
                    if (isTimeout(failure)) throw ApiException.timeout(requestId);
                    throw ApiException.invalidResponse(requestId);
                } catch (RuntimeException failure) { throw ApiException.invalidResponse(requestId); }
                if (status != 200) throw mapError(body, status, requestId, request != null);
                try { validate.accept(body); }
                catch (RuntimeException failure) { throw ApiException.invalidResponse(requestId); }
                return body;
            });
        } catch (ApiException failure) {
            throw failure;
        } catch (ResourceAccessException failure) {
            if (isTimeout(failure)) throw ApiException.timeout(requestId);
            throw ApiException.unavailable(requestId);
        } catch (RestClientException failure) {
            throw ApiException.invalidResponse(requestId);
        }
    }

    private static ApiException mapError(JsonNode body, int status, UUID requestId, boolean idWasSent) {
        if (body == null || !body.isObject() || body.size() != 2 || !body.has("request_id") || !body.has("error")) {
            return ApiException.invalidResponse(requestId);
        }
        JsonNode upstreamId = body.get("request_id");
        if (!upstreamId.isNull()) {
            UUID decoded = QueryRequestValidator.parseId(upstreamId);
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

    private static boolean isTimeout(Throwable failure) {
        for (Throwable cause = failure; cause != null; cause = cause.getCause()) {
            if (cause instanceof SocketTimeoutException || cause instanceof HttpTimeoutException
                    || cause instanceof ConnectionRequestTimeoutException) return true;
        }
        return false;
    }
}
