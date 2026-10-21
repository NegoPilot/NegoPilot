package com.negopilot.backend;

import jakarta.servlet.http.HttpServletRequest;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.Set;
import java.util.UUID;
import org.springframework.context.annotation.Profile;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import tools.jackson.databind.JsonNode;

@RestController
@Profile("!legacy-test")
@RequestMapping("/api/v1/admin/laws")
final class AdminLawController {
    private final AdminLawGateway laws;
    private final CurrentUser currentUser;
    AdminLawController(AdminLawGateway laws, CurrentUser currentUser) {
        this.laws = laws; this.currentUser = currentUser;
    }
    @GetMapping
    JsonNode list(HttpServletRequest request) {
        UUID id = begin(request, Set.of());
        return laws.read("/internal/v1/admin/laws", id);
    }
    @GetMapping("/refresh-jobs")
    JsonNode latestRefresh(HttpServletRequest request) {
        UUID id = begin(request, Set.of());
        return laws.latestRefresh(id);
    }
    @PostMapping("/refresh-jobs")
    ResponseEntity<JsonNode> refresh(@RequestBody(required = false) String body, HttpServletRequest request) {
        UUID id = begin(request, Set.of());
        if (body != null && !body.isEmpty()) throw ApiException.validation(id);
        return ResponseEntity.accepted().body(laws.startRefresh(id));
    }
    @GetMapping("/refresh-jobs/{jobId}")
    JsonNode refreshJob(@PathVariable String jobId, HttpServletRequest request) {
        UUID id = begin(request, Set.of());
        if (!jobId.matches("[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}")) {
            throw ApiException.validation(id);
        }
        return laws.refreshJob(UUID.fromString(jobId), id);
    }
    @GetMapping("/{lawKey}/versions")
    JsonNode versions(@PathVariable String lawKey, HttpServletRequest request) {
        UUID id = begin(request, Set.of());
        return laws.read(base(lawKey, id) + "/versions", id);
    }
    @GetMapping("/{lawKey}/versions/{versionId}/articles")
    JsonNode articles(@PathVariable String lawKey, @PathVariable String versionId, HttpServletRequest request) {
        UUID id = begin(request, Set.of("offset", "limit"));
        int offset = number(request.getParameter("offset"), 0, 0, 100000, id);
        int limit = number(request.getParameter("limit"), 50, 1, 100, id);
        return laws.read(versionPath(lawKey, versionId, id) + "/articles?offset="+offset+"&limit="+limit, id);
    }
    @GetMapping("/{lawKey}/versions/{versionId}/articles/{articleNo}")
    JsonNode article(@PathVariable String lawKey, @PathVariable String versionId,
                     @PathVariable String articleNo, HttpServletRequest request) {
        UUID id = begin(request, Set.of());
        if (!articleNo.matches("제?[0-9]+(?:조)?(?:의[0-9]+)?")) throw ApiException.validation(id);
        return laws.read(versionPath(lawKey, versionId, id) + "/articles/"
                + URLEncoder.encode(articleNo, StandardCharsets.UTF_8), id);
    }
    private UUID begin(HttpServletRequest request, Set<String> allowed) {
        currentUser.requireAdmin();
        UUID id = UUID.randomUUID(); request.setAttribute("request_id", id);
        request.getParameterMap().forEach((key, values) -> {
            if (!allowed.contains(key) || values.length != 1) throw ApiException.validation(id);
        });
        return id;
    }
    private String base(String key, UUID id) {
        if (!key.matches("[a-z_]{1,80}")) throw ApiException.validation(id);
        return "/internal/v1/admin/laws/" + key;
    }
    private String versionPath(String key, String version, UUID id) {
        if (!version.matches("[0-9]{4}-[0-9]{2}-[0-9]{2}_[a-f0-9]{40}")) throw ApiException.validation(id);
        return base(key, id) + "/versions/" + version;
    }
    private int number(String raw, int fallback, int min, int max, UUID id) {
        if (raw == null) return fallback;
        if (!raw.matches("[0-9]{1,6}")) throw ApiException.validation(id);
        int value = Integer.parseInt(raw);
        if (value < min || value > max) throw ApiException.validation(id);
        return value;
    }
}
