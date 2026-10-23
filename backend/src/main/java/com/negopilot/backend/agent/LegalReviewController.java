package com.negopilot.backend.agent;

import com.negopilot.backend.common.api.ApiException;

import jakarta.servlet.http.HttpServletRequest;
import java.util.UUID;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/** Authenticated, CSRF-protected structured reviews; internal credentials stay server-side. */
@RestController
final class LegalReviewController {
    private final GenerationGateway gateway;
    private final JsonMapper mapper;

    LegalReviewController(GenerationGateway gateway, JsonMapper mapper) {
        this.gateway = gateway;
        this.mapper = mapper;
    }

    @PostMapping(value = "/api/legal-reviews", consumes = MediaType.APPLICATION_JSON_VALUE,
            produces = MediaType.APPLICATION_JSON_VALUE)
    JsonNode review(@RequestBody String raw, HttpServletRequest request) {
        UUID id = UUID.randomUUID();
        request.setAttribute("request_id", id);
        JsonNode offer;
        try { offer = mapper.readTree(raw); }
        catch (RuntimeException e) { throw ApiException.validation(id); }
        if (!request.getParameterMap().isEmpty() || offer == null || !offer.isObject()
                || raw.length() > 16000) throw ApiException.validation(id);
        var body = mapper.createObjectNode();
        body.put("request_id", id.toString());
        body.put("query", "입력한 기업 정보와 계약 조건의 법규 검토");
        body.putObject("context").putArray("selected_supplier_ids");
        body.set("offer", offer);
        return gateway.review(id, body);
    }
}
