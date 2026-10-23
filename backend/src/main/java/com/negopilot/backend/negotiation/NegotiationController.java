package com.negopilot.backend.negotiation;

import com.negopilot.backend.auth.CurrentUser;
import com.negopilot.backend.common.api.ApiException;
import com.negopilot.backend.common.api.ApiPaths;
import jakarta.servlet.http.HttpServletRequest;
import java.nio.charset.StandardCharsets;
import java.util.Collections;
import java.util.Set;
import java.util.UUID;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

@RestController
class NegotiationController {
    private static final String BASE = ApiPaths.NEGOTIATIONS;
    private static final String PROPOSAL = BASE + "/{id}/proposals/{pid}";
    private final NegotiationService service;
    private final CurrentUser currentUser;
    private final NegotiationRequestValidator validator;
    private final JsonMapper mapper;

    NegotiationController(NegotiationService service, CurrentUser currentUser, JsonMapper mapper) {
        this.service = service; this.currentUser = currentUser; this.mapper = mapper;
        this.validator = new NegotiationRequestValidator(mapper);
    }

    @PostMapping(value = BASE, consumes = MediaType.APPLICATION_JSON_VALUE)
    ResponseEntity<JsonNode> create(@RequestBody(required = false) String raw, HttpServletRequest request) {
        UUID owner = currentUser.requireActive().userId(); UUID rid = begin(request, Set.of());
        return response(service.create(owner, validator.create(validator.parse(raw, rid), rid), key(request, rid), rid));
    }

    @GetMapping(BASE)
    JsonNode list(HttpServletRequest request) {
        UUID owner = currentUser.requireActive().userId(); UUID rid = begin(request, Set.of("limit"));
        Integer limit = number(request.getParameter("limit"), rid);
        if (limit != null && limit > 50) throw ApiException.validation(rid);
        return service.list(owner, limit == null ? 20 : limit);
    }

    @GetMapping(BASE + "/{id}")
    JsonNode get(@PathVariable String id, HttpServletRequest request) {
        UUID owner = currentUser.requireActive().userId(); UUID rid = begin(request, Set.of());
        return service.get(owner, uuid(id, rid), rid);
    }

    @PatchMapping(value = BASE + "/{id}", consumes = MediaType.APPLICATION_JSON_VALUE)
    ResponseEntity<JsonNode> patchWorkspace(@PathVariable String id, @RequestBody(required = false) String raw, HttpServletRequest request) {
        UUID owner = currentUser.requireActive().userId(); UUID rid = begin(request, Set.of());
        return response(service.patchWorkspace(owner, uuid(id, rid), validator.workspacePatch(validator.parse(raw, rid), rid), rid));
    }

    @PostMapping(value = BASE + "/{id}/reviews", consumes = MediaType.APPLICATION_JSON_VALUE)
    ResponseEntity<JsonNode> review(@PathVariable String id, @RequestBody(required = false) String raw, HttpServletRequest request) {
        UUID owner = currentUser.requireActive().userId(); UUID rid = begin(request, Set.of());
        return response(service.review(owner, uuid(id, rid), validator.review(validator.parse(raw, rid), rid), key(request, rid), rid));
    }

    @GetMapping(BASE + "/{id}/reviews/{revision}")
    JsonNode getReview(@PathVariable String id, @PathVariable String revision, HttpServletRequest request) {
        UUID owner = currentUser.requireActive().userId(); UUID rid = begin(request, Set.of());
        return service.getReview(owner, uuid(id, rid), number(revision, rid), rid);
    }

    @PostMapping(value = BASE + "/{id}/proposals", consumes = MediaType.APPLICATION_JSON_VALUE)
    ResponseEntity<JsonNode> propose(@PathVariable String id, @RequestBody(required = false) String raw, HttpServletRequest request) {
        UUID owner = currentUser.requireActive().userId(); UUID rid = begin(request, Set.of());
        return response(service.propose(owner, uuid(id, rid), validator.proposal(validator.parse(raw, rid), rid), key(request, rid), rid));
    }

    @GetMapping(PROPOSAL)
    JsonNode proposal(@PathVariable String id, @PathVariable String pid, HttpServletRequest request) {
        UUID owner = currentUser.requireActive().userId(); UUID rid = begin(request, Set.of("revision"));
        return service.getProposal(owner, uuid(id, rid), uuid(pid, rid), number(request.getParameter("revision"), rid), rid);
    }

    @GetMapping(PROPOSAL + "/external")
    NegotiationExternalView external(@PathVariable String id, @PathVariable String pid, HttpServletRequest request) {
        UUID owner = currentUser.requireActive().userId(); UUID rid = begin(request, Set.of("revision"));
        return service.external(owner, uuid(id, rid), uuid(pid, rid), number(request.getParameter("revision"), rid), rid);
    }

    @PatchMapping(value = PROPOSAL, consumes = MediaType.APPLICATION_JSON_VALUE)
    ResponseEntity<JsonNode> patchProposal(@PathVariable String id, @PathVariable String pid, @RequestBody(required = false) String raw, HttpServletRequest request) {
        UUID owner = currentUser.requireActive().userId(); UUID rid = begin(request, Set.of());
        return response(service.patchProposal(owner, uuid(id, rid), uuid(pid, rid), validator.proposalPatchEnvelope(validator.parse(raw, rid), rid), key(request, rid), rid));
    }

    @PostMapping(PROPOSAL + "/revisions/{rev}/validations")
    ResponseEntity<JsonNode> validate(@PathVariable String id, @PathVariable String pid, @PathVariable String rev,
                                      @RequestBody(required = false) String raw, HttpServletRequest request) {
        UUID owner = currentUser.requireActive().userId(); UUID rid = begin(request, Set.of());
        ObjectNode body = raw == null || raw.isBlank() ? mapper.createObjectNode() : validator.parse(raw, rid);
        if (!body.isEmpty()) throw ApiException.validation(rid);
        return response(service.validate(owner, uuid(id, rid), uuid(pid, rid), number(rev, rid), body, key(request, rid), rid));
    }

    @GetMapping(PROPOSAL + "/revisions/{rev}/validations")
    JsonNode validations(@PathVariable String id, @PathVariable String pid, @PathVariable String rev, HttpServletRequest request) {
        UUID owner = currentUser.requireActive().userId(); UUID rid = begin(request, Set.of());
        return service.validations(owner, uuid(id, rid), uuid(pid, rid), number(rev, rid), rid);
    }

    @PostMapping(value = PROPOSAL + "/exports", consumes = MediaType.APPLICATION_JSON_VALUE)
    ResponseEntity<JsonNode> export(@PathVariable String id, @PathVariable String pid, @RequestBody(required = false) String raw, HttpServletRequest request) {
        UUID owner = currentUser.requireActive().userId(); UUID rid = begin(request, Set.of());
        return response(service.export(owner, uuid(id, rid), uuid(pid, rid), validator.export(validator.parse(raw, rid), rid), key(request, rid), rid));
    }

    @GetMapping(ApiPaths.NEGOTIATION_EXPORTS + "/{fileId}/file")
    ResponseEntity<byte[]> download(@PathVariable String fileId, HttpServletRequest request) {
        UUID owner = currentUser.requireActive().userId(); UUID rid = begin(request, Set.of());
        var file = service.download(owner, uuid(fileId, rid), rid);
        return ResponseEntity.ok().contentType(MediaType.parseMediaType(file.contentType()))
                .header("Content-Disposition", disposition(file.filename())).body(file.bytes());
    }

    static String disposition(String name) {
        StringBuilder encoded = new StringBuilder();
        for (byte value : name.getBytes(StandardCharsets.UTF_8)) {
            int b = value & 255;
            if (b >= 'a' && b <= 'z' || b >= 'A' && b <= 'Z' || b >= '0' && b <= '9' || "!#$&+-.^_`|~".indexOf(b) >= 0) encoded.append((char) b);
            else encoded.append("%%%02X".formatted(b));
        }
        String ascii = name.replaceAll("[^A-Za-z0-9._-]", "_");
        return "attachment; filename=\"" + ascii + "\"; filename*=UTF-8''" + encoded;
    }

    private ResponseEntity<JsonNode> response(NegotiationRequests.Result result) { return ResponseEntity.status(result.status()).body(result.body()); }
    private UUID begin(HttpServletRequest request, Set<String> allowed) {
        UUID id = UUID.randomUUID(); request.setAttribute("request_id", id);
        for (var entry : request.getParameterMap().entrySet()) if (!allowed.contains(entry.getKey()) || entry.getValue().length != 1) throw ApiException.validation(id);
        return id;
    }
    private UUID key(HttpServletRequest request, UUID rid) {
        var values = Collections.list(request.getHeaders("Idempotency-Key"));
        if (values.size() > 1) throw ApiException.validation(rid);
        return values.isEmpty() ? null : uuid(values.getFirst(), rid);
    }
    private UUID uuid(String value, UUID rid) {
        if (value == null || !value.matches("(?i)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")) throw ApiException.validation(rid);
        return UUID.fromString(value);
    }
    private Integer number(String value, UUID rid) {
        if (value == null) return null;
        try {
            if (!value.matches("[1-9][0-9]{0,9}")) throw new IllegalArgumentException();
            return Integer.valueOf(value);
        } catch (IllegalArgumentException failure) { throw ApiException.validation(rid); }
    }
}
