package com.negopilot.backend.agent;

import java.util.UUID;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

/** 기존 검토와 저장 검토가 같은 Agent 요청을 사용한다. */
public final class LegalReviewRequestFactory {
    private final JsonMapper mapper;

    public LegalReviewRequestFactory(JsonMapper mapper) {
        this.mapper = mapper;
    }

    public ObjectNode create(UUID requestId, JsonNode offer) {
        var body = mapper.createObjectNode();
        body.put("request_id", requestId.toString());
        body.put("query", "입력한 기업 정보와 계약 조건의 법규 검토");
        body.putObject("context").putArray("selected_supplier_ids");
        body.set("offer", offer.deepCopy());
        return body;
    }
}
