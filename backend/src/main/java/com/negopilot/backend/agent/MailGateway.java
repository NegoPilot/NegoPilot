package com.negopilot.backend.agent;

import com.negopilot.backend.common.api.ApiException;
import com.negopilot.backend.common.api.RequestIds;
import com.negopilot.backend.common.http.AgentHttpTransport;
import com.negopilot.backend.common.http.InternalPaths;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.time.OffsetDateTime;
import java.time.format.DateTimeParseException;
import java.util.Set;
import java.util.UUID;
import org.springframework.http.HttpMethod;
import org.springframework.stereotype.Service;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.JsonNodeFactory;
import tools.jackson.databind.node.ObjectNode;

/** Agent `/internal/offer-mails/*`를 거쳐 mail-mcp를 부른다. Agent는 모델 호출 없이 그대로 전달한다. */
@Service
public final class MailGateway {
    /** mail-mcp가 거절한 이유. Agent가 422로 그대로 넘긴다. */
    public static final Set<String> MAIL_ERRORS = Set.of(
            "INVALID_DOCUMENT", "DOCUMENT_NOT_FINAL", "OFFER_VIOLATION", "HASH_MISMATCH",
            "SENDER_INCOMPLETE", "INVALID_ATTACHMENT", "INVALID_MESSAGE", "RECIPIENT_MISSING",
            "CONFIRMATION_REQUIRED", "CONFIRMATION_INVALID", "CONFIRMATION_EXPIRED",
            "CONFIRMATION_MISMATCH", "ALREADY_SENT", "REQUESTER_REQUIRED", "MAIL_SEND_FAILED",
            "DB_UNAVAILABLE", "MAIL_NOT_CONFIGURED", "INVALID_CLIENT_REF", "CLIENT_REF_USED");
    private static final String HASH = "sha256:[0-9a-f]{64}";
    private final AgentHttpTransport transport;

    MailGateway(AgentHttpTransport transport) { this.transport = transport; }

    public JsonNode preview(UUID requestId, ObjectNode document, ArrayNode attachments, Long contactId, String message) {
        ObjectNode body = input(requestId, document, attachments, contactId, message);
        JsonNode result = post(requestId, body, InternalPaths.OFFER_MAILS + "/preview");
        try { checkPreview(result); } catch (RuntimeException failure) { throw ApiException.invalidResponse(requestId); }
        return result;
    }

    public JsonNode send(UUID requestId, ObjectNode document, ArrayNode attachments, long contactId, String message,
                         String confirmationToken, String requestedBy, String clientRef) {
        ObjectNode body = input(requestId, document, attachments, contactId, message);
        body.put("confirmation_token", confirmationToken).put("requested_by", requestedBy).put("client_ref", clientRef);
        JsonNode result = post(requestId, body, InternalPaths.OFFER_MAILS + "/send");
        try {
            require(result.isObject() && Set.of("SENT", "DRY_RUN").contains(result.path("status").asText(""))
                    && (result.path("send_id").isString() || result.path("send_id").isIntegralNumber())
                    && !result.path("send_id").asText().isBlank()
                    && (result.path("message_id").isNull() || result.path("message_id").isString() || result.path("message_id").isMissingNode()));
        } catch (RuntimeException failure) { throw ApiException.invalidResponse(requestId); }
        return result;
    }

    /** 문서 하나의 발송 기록. client_ref를 주면 그 요청의 기록만 돌려준다. */
    public JsonNode history(UUID requestId, String documentId, String clientRef) {
        String query = "?document_id=" + URLEncoder.encode(documentId, StandardCharsets.UTF_8)
                + (clientRef == null ? "" : "&client_ref=" + URLEncoder.encode(clientRef, StandardCharsets.UTF_8));
        JsonNode result = transport.exchange(HttpMethod.GET, transport.uri(InternalPaths.OFFER_MAILS + query), null, requestId,
                Set.of(200, 400, 422, 500, 503, 504), (status, response) -> handle(requestId, status, response, false));
        try {
            require(result.isArray());
            for (JsonNode row : result) {
                require(row.isObject() && Set.of("SENDING", "SENT", "DRY_RUN", "FAILED").contains(row.path("status").asText(""))
                        && !row.path("send_id").asText("").isBlank());
            }
        } catch (RuntimeException failure) { throw ApiException.invalidResponse(requestId); }
        return result;
    }

    private JsonNode post(UUID requestId, ObjectNode body, String path) {
        return transport.exchange(HttpMethod.POST, transport.uri(path), body, requestId,
                Set.of(200, 400, 422, 500, 503, 504), (status, response) -> handle(requestId, status, response, true));
    }

    private static JsonNode handle(UUID requestId, int status, JsonNode response, boolean sameRequest) {
        if (response == null || !response.isObject()) throw ApiException.invalidResponse(requestId);
        if (sameRequest && !(status == 400 || status == 500) && !requestId.equals(parse(response.get("request_id")))) {
            throw ApiException.invalidResponse(requestId);
        }
        if (status == 200) {
            if (response.size() != 2 || !response.has("result")) throw ApiException.invalidResponse(requestId);
            return response.get("result");
        }
        JsonNode error = response.path("error");
        String code = error.path("code").asText("");
        if (status == 422 && MAIL_ERRORS.contains(code) && error.path("message").isString()) {
            return throwing(ApiException.of(requestId, 422, code, error.path("message").asText(), false));
        }
        throw switch (status) {
            case 503 -> ApiException.unavailable(requestId);
            case 504 -> ApiException.timeout(requestId);
            case 400, 500 -> ApiException.internal(requestId);
            default -> ApiException.invalidResponse(requestId);
        };
    }

    private static void checkPreview(JsonNode result) {
        JsonNode to = result.path("to");
        require(result.isObject() && to.isObject() && to.path("contact_id").isIntegralNumber()
                && to.path("email").isString() && to.path("supplier_id").isString()
                && result.path("from").isString() && result.path("subject").isString() && result.path("body").isString()
                && result.path("attachments").isArray() && result.path("content_hash").asText("").matches(HASH)
                && result.path("dry_run").isBoolean() && !result.path("confirmation_token").asText("").isBlank());
        try { OffsetDateTime.parse(result.path("expires_at").asText()); }
        catch (DateTimeParseException failure) { throw new IllegalArgumentException(failure); }
    }

    private static ObjectNode input(UUID requestId, ObjectNode document, ArrayNode attachments, Long contactId, String message) {
        ObjectNode body = JsonNodeFactory.instance.objectNode().put("request_id", requestId.toString());
        body.set("document", document);
        body.set("attachments", attachments);
        if (contactId != null) body.put("contact_id", contactId);
        if (message != null) body.put("message", message);
        return body;
    }

    private static UUID parse(JsonNode value) {
        try { return RequestIds.parse(value); } catch (RuntimeException failure) { return null; }
    }
    private static JsonNode throwing(ApiException failure) { throw failure; }
    private static void require(boolean condition) { if (!condition) throw new IllegalArgumentException("Invalid mail response"); }
}
