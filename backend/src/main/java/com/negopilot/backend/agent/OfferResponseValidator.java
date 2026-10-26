package com.negopilot.backend.agent;

import com.negopilot.backend.common.api.ApiException;
import com.negopilot.backend.common.api.RequestIds;
import java.util.Base64;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import tools.jackson.databind.JsonNode;

/** 협상 문서와 첨부의 Agent 응답 계약을 확인한다. */
final class OfferResponseValidator {
    private static final int MAX_ATTACHMENT_BYTES = 10_000_000;
    private static final String DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
    private static final Set<String> INPUT_ERRORS = Set.of(
            "RFQ_NOT_FOUND", "SUPPLIER_NOT_FOUND", "VALUE_OUT_OF_RANGE", "NO_OFFER", "OFFER_VIOLATION");

    private OfferResponseValidator() { }

    static void proposal(JsonNode body, UUID requestId) {
        envelope(body, requestId, Set.of("request_id", "offer_document", "check", "internal_briefing"));
        JsonNode document = body.get("offer_document");
        require(document.isNull() || document.isObject());
        require(body.get("internal_briefing").isObject());
        check(body.get("check"), document.isNull());
    }

    static void render(JsonNode body, UUID requestId, String files) {
        envelope(body, requestId, Set.of("request_id", "document", "attachments", "check", "cover_letter"));
        coverLetter(body.get("cover_letter"));
        JsonNode document = body.get("document");
        require(document.isObject() && document.path("content_hash").isString()
                && document.path("content_hash").stringValue().matches("sha256:[0-9a-f]{64}"));
        check(body.get("check"), false);
        JsonNode attachments = body.get("attachments");
        require(attachments.isArray());
        Set<String> expected = files.equals("both") ? Set.of("pdf", "docx") : Set.of(files);
        Set<String> actual = new HashSet<>();
        Set<String> names = new HashSet<>();
        require(attachments.size() == expected.size());
        for (JsonNode attachment : attachments) {
            keys(attachment, Set.of("filename", "content_type", "data_base64"));
            require(text(attachment.get("filename")) && text(attachment.get("content_type"))
                    && text(attachment.get("data_base64")));
            String name = attachment.get("filename").stringValue();
            require(!name.contains("/") && !name.contains("\\")
                    && name.codePoints().noneMatch(Character::isISOControl) && names.add(name));
            String media = attachment.get("content_type").stringValue();
            String extension = switch (media) {
                case "application/pdf" -> "pdf";
                case DOCX -> "docx";
                default -> throw new IllegalArgumentException("Invalid attachment type");
            };
            require(name.endsWith("." + extension) && expected.contains(extension) && actual.add(extension));
            String encoded = attachment.get("data_base64").stringValue();
            require(encoded.length() <= ((MAX_ATTACHMENT_BYTES + 2) / 3) * 4);
            byte[] bytes = Base64.getDecoder().decode(encoded);
            require(bytes.length > 0 && bytes.length <= MAX_ATTACHMENT_BYTES);
        }
        require(actual.equals(expected));
    }

    /** /render?include_cover_status=true의 인사말 다시 쓰기 결과 {regenerated, source, problems}. */
    private static void coverLetter(JsonNode cover) {
        keys(cover, Set.of("regenerated", "source", "problems"));
        require(cover.get("regenerated").isBoolean() && cover.get("source").isString() && cover.get("problems").isArray());
        String source = cover.get("source").stringValue();
        require(Set.of("kept", "model", "fallback").contains(source)
                && cover.get("regenerated").booleanValue() == !source.equals("kept"));
        for (JsonNode problem : cover.get("problems")) require(problem.isString());
    }

    static ApiException businessError(JsonNode body, UUID requestId) {
        try {
            require(body != null && body.isObject() && requestId.equals(RequestIds.parse(body.get("request_id"))));
            JsonNode error = body.get("error");
            keys(error, Set.of("code", "message", "retryable"));
            require(text(error.get("code")) && INPUT_ERRORS.contains(error.get("code").stringValue())
                    && text(error.get("message")) && error.get("retryable").isBoolean()
                    && !error.get("retryable").booleanValue());
            String code = error.get("code").stringValue();
            if (code.equals("OFFER_VIOLATION")) {
                keys(body, Set.of("request_id", "error", "findings"));
                JsonNode findings = body.get("findings");
                require(findings.isArray() && !findings.isEmpty());
                for (JsonNode finding : findings) {
                    keys(finding, Set.of("code", "message", "law_refs"));
                    require(text(finding.get("code")) && text(finding.get("message"))
                            && (finding.get("law_refs").isNull() || finding.get("law_refs").isArray()));
                }
                return ApiException.of(requestId, 422, code, "The edited terms violate a rule.", false,
                        Map.of("findings", findings.deepCopy()));
            }
            keys(body, Set.of("request_id", "error"));
            return ApiException.of(requestId, 422, code, "Offer input was rejected.", false);
        } catch (IllegalArgumentException failure) {
            return ApiException.invalidResponse(requestId);
        }
    }

    private static void check(JsonNode check, boolean nullableVerdict) {
        keys(check, Set.of("verdict_code", "warnings"));
        JsonNode verdict = check.get("verdict_code");
        require((nullableVerdict && verdict.isNull()) || (verdict.isString()
                && Set.of("OK", "CHECK_NEEDED", "VIOLATION").contains(verdict.stringValue())));
        require(check.get("warnings").isArray());
        for (JsonNode warning : check.get("warnings")) {
            keys(warning, Set.of("code", "message"));
            require(text(warning.get("code")) && text(warning.get("message")));
        }
    }

    private static void envelope(JsonNode body, UUID requestId, Set<String> fields) {
        keys(body, fields);
        require(requestId.equals(RequestIds.parse(body.get("request_id"))));
    }

    private static void keys(JsonNode value, Set<String> expected) {
        require(value != null && value.isObject() && value.propertyNames().equals(expected));
    }

    private static boolean text(JsonNode value) {
        return value != null && value.isString() && !value.stringValue().isBlank();
    }

    private static void require(boolean value) {
        if (!value) throw new IllegalArgumentException("Invalid offer response");
    }
}
