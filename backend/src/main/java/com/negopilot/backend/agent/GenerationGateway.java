package com.negopilot.backend.agent;

import com.negopilot.backend.common.api.ApiException;
import com.negopilot.backend.common.api.RequestIds;
import com.negopilot.backend.common.http.AgentHttpTransport;

import java.util.Set;
import java.util.UUID;
import org.springframework.http.HttpMethod;
import org.springframework.stereotype.Service;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ObjectNode;

@Service
public final class GenerationGateway {
    private final AgentHttpTransport transport;
    private final GenerationResponseValidator validator;

    GenerationGateway(AgentHttpTransport transport, GenerationResponseValidator validator) {
        this.transport = transport;
        this.validator = validator;
    }

    public JsonNode execute(UUID requestId, ObjectNode body) {
        return transport.exchange(HttpMethod.POST, transport.uri("/internal/v2/query-executions"),
                body, requestId, Set.of(200, 400, 500, 503, 504), (status, result) -> {
                    if (status == 200) {
                        try {
                            validator.validate(result, requestId, body.get("context"));
                        } catch (IllegalArgumentException ex) {
                            throw ApiException.invalidResponse(requestId);
                        }
                        if (!result.get("decomposition").get("original_query").equals(body.get("query"))) {
                            throw ApiException.invalidResponse(requestId);
                        }
                        return AgentResponseSanitizer.publicResponse(result, "Answer generation failed.");
                    }
                    validateHttpError(result, requestId, status);
                    throw switch (status) {
                        case 400 -> ApiException.validation(requestId);
                        case 500 -> ApiException.internal(requestId);
                        case 503 -> ApiException.unavailable(requestId);
                        default -> ApiException.timeout(requestId);
                    };
                });
    }

    private static void validateHttpError(JsonNode body, UUID id, int status) {
        String expectedCode = switch (status) {
            case 400 -> "VALIDATION_ERROR";
            case 500 -> "INTERNAL_ERROR";
            case 503 -> "UPSTREAM_UNAVAILABLE";
            default -> "UPSTREAM_TIMEOUT";
        };
        if (body == null || !body.isObject() || body.size() != 2 || !body.has("request_id") || !body.has("error")) {
            throw ApiException.invalidResponse(id);
        }
        // Generic Python validation and exception handlers may not have decoded an ID.
        if (!((status == 400 || status == 500) && body.get("request_id").isNull())
                && !id.equals(RequestIds.parse(body.get("request_id")))) {
            throw ApiException.invalidResponse(id);
        }
        JsonNode error = body.get("error");
        boolean retryable = status == 503 || status == 504;
        if (!error.isObject() || error.size() != 3 || !error.path("code").isString()
                || !expectedCode.equals(error.path("code").stringValue())
                || !error.path("message").isString() || error.path("message").stringValue().isBlank()
                || !error.path("retryable").isBoolean() || error.path("retryable").booleanValue() != retryable) {
            throw ApiException.invalidResponse(id);
        }
    }
}
