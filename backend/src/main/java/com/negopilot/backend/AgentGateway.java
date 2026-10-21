package com.negopilot.backend;

import java.io.IOException;
import java.net.SocketTimeoutException;
import java.net.http.HttpTimeoutException;
import java.util.Set;
import java.util.UUID;
import org.apache.hc.core5.http.ConnectionRequestTimeoutException;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Service;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

@Service
final class AgentGateway {
    private final RestClient client;
    private final AgentProperties properties;
    private final JsonMapper mapper;
    private final AgentResponseValidator validator;

    AgentGateway(RestClient agentRestClient, AgentProperties properties, JsonMapper mapper,
                 AgentResponseValidator validator) {
        this.client = agentRestClient;
        this.properties = properties;
        this.mapper = mapper;
        this.validator = validator;
    }

    JsonNode execute(QueryRequestValidator.ValidatedRequest request) {
        UUID id = request.requestId();
        if (properties.internalApiKey().isBlank()) throw ApiException.unavailable(id);
        try {
            return client.post()
                    .uri(properties.baseUrl().resolve("/internal/v1/query-executions"))
                    .contentType(MediaType.APPLICATION_JSON)
                    .accept(MediaType.APPLICATION_JSON)
                    .header("X-Internal-Api-Key", properties.internalApiKey())
                    .body(mapper.writeValueAsBytes(request.body()))
                    .exchange((httpRequest, response) -> {
                        int status = response.getStatusCode().value();
                        if (!Set.of(200, 400, 503, 504).contains(status)) throw ApiException.invalidResponse(id);
                        MediaType contentType;
                        try {
                            contentType = response.getHeaders().getContentType();
                        } catch (IllegalArgumentException ex) {
                            throw ApiException.invalidResponse(id);
                        }
                        if (contentType == null || !MediaType.APPLICATION_JSON.isCompatibleWith(contentType)) {
                            throw ApiException.invalidResponse(id);
                        }
                        JsonNode body;
                        try {
                            body = mapper.readTree(response.getBody().readAllBytes());
                        } catch (IOException ex) {
                            if (isTimeout(ex)) throw ApiException.timeout(id);
                            throw ApiException.invalidResponse(id);
                        } catch (RuntimeException ex) {
                            throw ApiException.invalidResponse(id);
                        }
                        if (status == 200) {
                            try {
                                validator.validate(body, id);
                            } catch (IllegalArgumentException ex) {
                                throw ApiException.invalidResponse(id);
                            }
                            return publicResponse(body);
                        }
                        validateHttpError(body, id, status);
                        throw switch (status) {
                            case 400 -> ApiException.validation(id);
                            case 503 -> ApiException.unavailable(id);
                            default -> ApiException.timeout(id);
                        };
                    });
        } catch (ApiException ex) {
            throw ex;
        } catch (ResourceAccessException ex) {
            if (isTimeout(ex)) throw ApiException.timeout(id);
            throw ApiException.unavailable(id);
        } catch (RestClientException ex) {
            throw ApiException.invalidResponse(id);
        }
    }

    private static JsonNode publicResponse(JsonNode validatedResponse) {
        ObjectNode response = (ObjectNode) validatedResponse.deepCopy();
        ObjectNode routing = (ObjectNode) response.get("routing");
        for (JsonNode node : routing.get("outcomes")) {
            ObjectNode outcome = (ObjectNode) node;
            sanitizeToolErrors(outcome.get("calls"));
            sanitizeToolErrors(outcome.get("referenced_articles"));
            if (!outcome.get("message").isNull()) {
                outcome.put("message", "Routing details are unavailable.");
            }
        }
        if (!routing.get("halt_reason").isNull()) {
            routing.put("halt_reason", "Routing halted because a dependency returned no results.");
        }
        if (!response.get("error").isNull()) {
            ObjectNode error = (ObjectNode) response.get("error");
            error.put("message", error.get("code").stringValue().equals("DECOMPOSITION_FAILED")
                    ? "Query decomposition failed." : "Query routing failed.");
        }
        return response;
    }

    private static void sanitizeToolErrors(JsonNode calls) {
        for (JsonNode call : calls) {
            if (!call.get("error").isNull()) {
                ((ObjectNode) call).put("error", "Tool execution failed.");
            }
        }
    }

    private static void validateHttpError(JsonNode body, UUID id, int status) {
        String expectedCode = switch (status) {
            case 400 -> "VALIDATION_ERROR";
            case 503 -> "UPSTREAM_UNAVAILABLE";
            default -> "UPSTREAM_TIMEOUT";
        };
        if (body == null || !body.isObject() || body.size() != 2 || !body.has("request_id") || !body.has("error")) {
            throw ApiException.invalidResponse(id);
        }
        // Python may report null for a request validation error before decoding its request ID.
        if (!(status == 400 && body.path("request_id").isNull())
                && !id.equals(QueryRequestValidator.parseId(body.get("request_id")))) {
            throw ApiException.invalidResponse(id);
        }
        JsonNode error = body.path("error");
        if (!error.isObject() || error.size() != 3 || !error.path("code").isString()
                || !expectedCode.equals(error.path("code").stringValue())
                || !error.path("message").isString() || error.path("message").stringValue().isBlank()
                || !error.path("retryable").isBoolean() || error.path("retryable").booleanValue() != (status != 400)) {
            throw ApiException.invalidResponse(id);
        }
    }

    private static boolean isTimeout(Throwable failure) {
        for (Throwable cause = failure; cause != null; cause = cause.getCause()) {
            if (cause instanceof SocketTimeoutException || cause instanceof HttpTimeoutException
                    || cause instanceof ConnectionRequestTimeoutException) return true;
        }
        return false;
    }
}
