package com.negopilot.backend.legalreview;

import java.util.List;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.JsonNodeFactory;
import tools.jackson.databind.node.ObjectNode;

/**
 * 저장 행 + 연결한 협상 작업의 검토 → 공개 응답. review_token은 읽지도 넣지도 않는다.
 * 요약은 거래 사실 검토 상태, check_offer 판정, 기업 규모 판정, 실행 실패를 서로 다른 필드로 둔다.
 */
final class LegalReviewRowView {
    private static final List<String> ROW_FIELDS = List.of("row_number", "status", "supplier_id", "rfq_id", "input_errors",
            "negotiation_id", "review_revision", "review_head_revision", "attempts", "created_at", "started_at", "finished_at");

    private LegalReviewRowView() { }

    static ObjectNode summary(ObjectNode row, JsonMapper mapper) {
        ObjectNode view = JsonNodeFactory.instance.objectNode();
        for (String field : ROW_FIELDS) view.set(field, row.path(field).isMissingNode() ? null : row.get(field));
        JsonNode review = row.path("review");
        JsonNode response = review.path("response");
        JsonNode assessment = response.path("legal_review").path("assessment");
        // 실패한 실행은 협상 작업에 저장되지 않으므로 행의 실행 상태를 쓴다
        view.set("execution_status", review.path("execution_status").isString() ? review.get("execution_status") : row.get("execution_status"));
        view.set("assessment_status", text(review.path("assessment_status")));
        view.set("eligibility", text(review.path("eligibility")));
        view.set("missing_fields", assessment.path("missing_fields").isArray() ? assessment.get("missing_fields")
                : JsonNodeFactory.instance.arrayNode());
        view.set("sources_complete", assessment.path("sources_complete").isBoolean() ? assessment.get("sources_complete") : null);
        view.set("check_verdict_code", checkVerdict(response));
        view.set("check_error_code", checkError(response));
        view.set("company_size", companySize(response, mapper));
        if (row.path("error_code").isString()) {
            view.putObject("error").put("code", row.get("error_code").stringValue())
                    .put("retryable", row.path("error_retryable").asBoolean(false));
        } else {
            view.putNull("error");
        }
        return view;
    }

    static ObjectNode detail(ObjectNode row, JsonMapper mapper) {
        ObjectNode view = summary(row, mapper);
        view.set("source_cells", row.get("source_cells"));
        view.set("offer", row.get("offer"));
        view.set("response", row.path("review").path("response").isObject() ? row.get("review").get("response") : null);
        return view;
    }

    private static JsonNode text(JsonNode node) { return node.isString() ? node : null; }

    /** routing의 check_offer 결과 verdict_code (VIOLATION / CHECK_NEEDED / OK). 호출하지 못했으면 null. */
    private static JsonNode checkVerdict(JsonNode response) {
        for (JsonNode call : checkOfferCalls(response)) {
            if (call.path("result").path("verdict_code").isString()) return call.get("result").get("verdict_code");
        }
        return null;
    }

    /** check_offer가 입력을 거절한 공개 오류 코드(RFQ_NOT_FOUND 등). Agent가 허용 목록만 넘긴다. */
    private static JsonNode checkError(JsonNode response) {
        for (JsonNode call : checkOfferCalls(response)) {
            if (call.path("error_code").isString()) return call.get("error_code");
        }
        return null;
    }

    /**
     * 기업 규모 판정. answer.evidence의 company_size 근거를 먼저 보고(legal-review.md), 답변에서 빠졌으면
     * 같은 값을 담은 check_offer 결과의 company_size를 쓴다. 판정 보류는 is_sme / is_small_business가 null이다.
     */
    private static JsonNode companySize(JsonNode response, JsonMapper mapper) {
        ObjectNode sizes = JsonNodeFactory.instance.objectNode();
        for (JsonNode evidence : response.path("answer").path("evidence")) {
            if (!"company_size".equals(evidence.path("kind").asString(""))) continue;
            String role = evidence.path("meta").path("role").asString("");
            if (!role.equals("supplier") && !role.equals("buyer")) continue;
            try { putSize(sizes, role, mapper.readTree(evidence.path("text").asString(""))); }
            catch (RuntimeException ignored) { /* 형식이 다른 근거는 건너뛴다 */ }
        }
        if (sizes.isEmpty()) {
            for (JsonNode call : checkOfferCalls(response)) {
                JsonNode found = call.path("result").path("company_size");
                for (String role : List.of("supplier", "buyer")) if (found.path(role).isObject()) putSize(sizes, role, found.get(role));
            }
        }
        return sizes.isEmpty() ? null : sizes;
    }

    private static List<JsonNode> checkOfferCalls(JsonNode response) {
        List<JsonNode> calls = new java.util.ArrayList<>();
        for (JsonNode outcome : response.path("routing").path("outcomes")) {
            for (JsonNode call : outcome.path("calls")) if ("check_offer".equals(call.path("tool").asString(""))) calls.add(call);
        }
        return calls;
    }

    private static void putSize(ObjectNode sizes, String role, JsonNode assessment) {
        ObjectNode size = sizes.putObject(role);
        size.set("status", assessment.path("status").isString() ? assessment.get("status") : null);
        size.set("is_sme", assessment.path("is_sme").isBoolean() ? assessment.get("is_sme") : null);
        size.set("is_small_business", assessment.path("is_small_business").isBoolean() ? assessment.get("is_small_business") : null);
        size.set("missing", assessment.path("missing").isArray() ? assessment.get("missing") : JsonNodeFactory.instance.arrayNode());
    }
}
