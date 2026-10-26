package com.negopilot.backend.negotiation;

import java.nio.charset.StandardCharsets;
import java.util.UUID;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

/**
 * 다른 기능(엑셀 법규 검토 #284)이 협상 작업 생성과 검토 저장을 공개 API와 같은 검증·저장 규칙으로 쓰는 입구.
 * 검토는 neg_review_revisions에 저장되므로 그대로 협상안 생성(POST /api/negotiations/{id}/proposals)으로 이어진다.
 * source(업로드 행마다 고정된 값)에서 멱등 키를 만들어, 같은 행을 다시 실행해도 작업과 검토가 한 번만 저장되게 한다.
 */
@Component
public final class NegotiationReviews {
    /** 201이면 저장한 검토, 200이면 Agent 실행 실패로 저장하지 않은 응답 (negotiation-workspace.md 2절). */
    public record Outcome(int status, JsonNode body) { }

    private final NegotiationService service;
    private final NegotiationRequests requests;
    private final NegotiationRequestValidator validator;
    private final JsonMapper mapper;

    NegotiationReviews(NegotiationService service, NegotiationRequests requests, JsonMapper mapper) {
        this.service = service;
        this.requests = requests;
        this.validator = new NegotiationRequestValidator(mapper);
        this.mapper = mapper;
    }

    /**
     * 새 협상 작업. 기본 제목(RFQ · 협력사명) 뒤에 출처를 붙여 업로드 행끼리 구분한다.
     * 같은 source로 이미 만든 작업이 있으면 새로 만들지 않고 그 작업을 돌려준다.
     */
    public UUID create(UUID owner, String supplierId, String rfqId, String titleSuffix, UUID source, UUID requestId) {
        ObjectNode body = mapper.createObjectNode().put("supplier_id", supplierId).put("rfq_id", rfqId);
        JsonNode created = service.create(owner, validator.create(body, requestId), titleSuffix, key(source, "create"), requestId).body();
        return UUID.fromString(created.get("negotiation_id").asText());
    }

    /**
     * 작업의 첫 검토로 저장한다. offer의 supplier·rfq_id는 작업 값으로 대신한다.
     * 같은 source의 검토가 이미 저장됐으면 Agent를 다시 부르지 않고 그 결과를 돌려준다.
     * 작업에 다른 검토(사용자가 조건을 바꾼 재검토)가 있으면 덮어쓰지 않고 REVISION_CONFLICT로 끝난다.
     */
    public Outcome review(UUID owner, UUID workspaceId, ObjectNode offer, UUID source, UUID requestId) {
        ObjectNode body = offer.deepCopy();
        body.remove("supplier");
        body.remove("rfq_id");
        UUID key = key(source, "review");
        var result = service.review(owner, workspaceId, validator.review(body, requestId), key, requestId);
        if (result.status() == 200) requests.forgetUnsaved(owner, key);
        return new Outcome(result.status(), result.body());
    }

    private static UUID key(UUID source, String operation) {
        return UUID.nameUUIDFromBytes((source + ":" + operation).getBytes(StandardCharsets.UTF_8));
    }
}
