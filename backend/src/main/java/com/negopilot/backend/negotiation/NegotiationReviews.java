package com.negopilot.backend.negotiation;

import java.util.UUID;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

/**
 * 다른 기능(엑셀 법규 검토 #284)이 협상 작업 생성과 검토 저장을 공개 API와 같은 검증·저장 규칙으로 쓰는 입구.
 * 검토는 neg_review_revisions에 저장되므로 그대로 협상안 생성(POST /api/negotiations/{id}/proposals)으로 이어진다.
 */
@Component
public final class NegotiationReviews {
    /** 201이면 저장한 검토, 200이면 Agent 실행 실패로 저장하지 않은 응답 (negotiation-workspace.md 2절). */
    public record Outcome(int status, JsonNode body) { }

    private static final int TITLE_LIMIT = 100;
    private final NegotiationService service;
    private final NegotiationRequestValidator validator;
    private final JsonMapper mapper;

    NegotiationReviews(NegotiationService service, JsonMapper mapper) {
        this.service = service;
        this.validator = new NegotiationRequestValidator(mapper);
        this.mapper = mapper;
    }

    /** 새 협상 작업. 기본 제목(RFQ · 협력사명) 뒤에 출처를 붙여 업로드 행끼리 구분한다. */
    public UUID create(UUID owner, String supplierId, String rfqId, String titleSuffix, UUID requestId) {
        ObjectNode body = mapper.createObjectNode().put("supplier_id", supplierId).put("rfq_id", rfqId);
        JsonNode created = service.create(owner, validator.create(body, requestId), null, requestId).body();
        UUID id = UUID.fromString(created.get("negotiation_id").asText());
        String title = fit(created.path("title").asText() + " · " + titleSuffix);
        if (!title.equals(created.path("title").asText())) {
            ObjectNode patch = mapper.createObjectNode().put("row_version", created.path("row_version").longValue()).put("title", title);
            service.patchWorkspace(owner, id, validator.workspacePatch(patch, requestId), requestId);
        }
        return id;
    }

    /** 작업의 최신 검토 위에 새 검토 버전을 저장한다. offer의 supplier·rfq_id는 작업 값으로 대신한다. */
    public Outcome review(UUID owner, UUID workspaceId, ObjectNode offer, UUID requestId) {
        ObjectNode body = offer.deepCopy();
        body.remove("supplier");
        body.remove("rfq_id");
        int head = service.get(owner, workspaceId, requestId).path("review_head_revision").intValue();
        if (head > 0) body.put("base_revision", head);
        var result = service.review(owner, workspaceId, validator.review(body, requestId), null, requestId);
        return new Outcome(result.status(), result.body());
    }

    private static String fit(String title) {
        if (title.codePointCount(0, title.length()) <= TITLE_LIMIT) return title;
        return title.substring(0, title.offsetByCodePoints(0, TITLE_LIMIT - 1)) + "…";
    }
}
