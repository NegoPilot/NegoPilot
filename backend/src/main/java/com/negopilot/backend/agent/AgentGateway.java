package com.negopilot.backend.agent;

import com.negopilot.backend.common.api.ApiException;
import com.negopilot.backend.common.http.AgentHttpTransport;

import java.util.Set;
import java.util.UUID;
import org.springframework.http.HttpMethod;
import org.springframework.stereotype.Service;
import tools.jackson.databind.JsonNode;

@Service
final class AgentGateway {
    private final AgentHttpTransport transport;
    private final AgentResponseValidator validator;

    AgentGateway(AgentHttpTransport transport, AgentResponseValidator validator) {
        this.transport = transport;
        this.validator = validator;
    }

    JsonNode execute(QueryRequestValidator.ValidatedRequest request) {
        UUID id = request.requestId();
        return transport.exchange(HttpMethod.POST, transport.uri("/internal/v1/query-executions"),
                request.body(), id, Set.of(200, 400, 503, 504), (status, body) -> {
                    if (status == 200) {
                        try {
                            validator.validate(body, id);
                        } catch (IllegalArgumentException ex) {
                            throw ApiException.invalidResponse(id);
                        }
                        return AgentResponseSanitizer.publicResponse(body, "Query routing failed.");
                    }
                    validateHttpError(body, id, status);
                    throw switch (status) {
                        case 400 -> ApiException.validation(id);
                        case 503 -> ApiException.unavailable(id);
                        default -> ApiException.timeout(id);
                    };
                });
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
}
