package com.negopilot.backend.agent;

import com.negopilot.backend.common.api.ApiException;
import com.negopilot.backend.common.api.RequestIds;
import com.negopilot.backend.common.http.AgentHttpTransport;
import com.negopilot.backend.common.http.InternalPaths;

import java.util.Set;
import java.util.UUID;
import org.springframework.http.HttpMethod;
import org.springframework.stereotype.Service;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.JsonNodeFactory;
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
        return executeAt(requestId, body, InternalPaths.QUERY_EXECUTIONS);
    }

    public JsonNode review(UUID requestId, ObjectNode body) {
        return executeAt(requestId, body, InternalPaths.LEGAL_REVIEWS);
    }

    public JsonNode propose(UUID requestId, ObjectNode offerRequest) {
        ObjectNode body = JsonNodeFactory.instance.objectNode();
        body.put("request_id", requestId.toString());
        body.set("offer", offerRequest);
        return offerExchange(requestId, body, InternalPaths.OFFER_PROPOSALS, null);
    }

    /**
     * 고친 문서를 다시 검사하고 파일을 만든다. 응답의 cover_letter는 인사말을 다시 썼는지 알려 준다 (#293, #306).
     * rewriteCover가 true면 맞지 않는 인사말을 모델로 다시 쓰고, false면 모델 없이 코드 인사말로만 바꾼다.
     */
    public JsonNode render(UUID requestId, ObjectNode document, String files, boolean rewriteCover) {
        if (files == null || !Set.of("pdf", "docx", "both").contains(files)) throw ApiException.validation(requestId);
        ObjectNode body = JsonNodeFactory.instance.objectNode();
        body.put("request_id", requestId.toString());
        body.set("document", document);
        body.put("files", files);
        String query = "&include_cover_status=true" + (rewriteCover ? "" : "&writing=false");
        return offerExchange(requestId, body, InternalPaths.OFFER_RENDER, files, query);
    }

    private JsonNode offerExchange(UUID requestId, ObjectNode body, String path, String files) {
        return offerExchange(requestId, body, path, files, "");
    }

    private JsonNode offerExchange(UUID requestId, ObjectNode body, String path, String files, String query) {
        return transport.exchange(HttpMethod.POST, transport.uri(path + "?format=json" + query), body, requestId,
                Set.of(200, 400, 422, 500, 503, 504), (status, result) -> {
                    if (status == 200) {
                        try {
                            if (files == null) OfferResponseValidator.proposal(result, requestId);
                            else OfferResponseValidator.render(result, requestId, files);
                        } catch (IllegalArgumentException failure) {
                            throw ApiException.invalidResponse(requestId);
                        }
                        return result;
                    }
                    if (status == 422) throw OfferResponseValidator.businessError(result, requestId);
                    validateHttpError(result, requestId, status);
                    throw switch (status) {
                        case 400 -> ApiException.validation(requestId);
                        case 500 -> ApiException.internal(requestId);
                        case 503 -> ApiException.unavailable(requestId);
                        default -> ApiException.timeout(requestId);
                    };
                });
    }

    private JsonNode executeAt(UUID requestId, ObjectNode body, String path) {
        return transport.exchange(HttpMethod.POST, transport.uri(path),
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
