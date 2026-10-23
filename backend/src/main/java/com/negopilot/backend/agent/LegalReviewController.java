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
    private final LegalReviewRequestFactory requests;

    LegalReviewController(GenerationGateway gateway, JsonMapper mapper, LegalReviewRequestFactory requests) {
        this.gateway = gateway;
        this.mapper = mapper;
        this.requests = requests;
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
        return gateway.review(id, requests.create(id, offer));
    }
}
