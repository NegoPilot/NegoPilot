package com.negopilot.backend.admin.law;

import com.negopilot.backend.agent.AgentHttpConfiguration;
import com.negopilot.backend.agent.AgentProperties;
import com.negopilot.backend.common.api.ApiException;
import com.negopilot.backend.common.http.AgentHttpTransport;
import com.negopilot.backend.auth.CurrentUser;

import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.URI;
import java.time.Duration;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.Consumer;
import org.apache.hc.client5.http.impl.classic.CloseableHttpClient;
import org.junit.jupiter.api.*;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.mock.web.MockHttpServletRequest;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class AdminLawGatewayHttpTest {
    private static final String ROOT = "/internal/v1/admin/laws";
    private static final String KEY = "private-test-law-key";
    private static final String COMMIT = "a".repeat(40);
    private static final String VERSION = "2026-01-01_" + COMMIT;
    private static final String LAW = ROOT + "/subcontract_act/versions";
    private static final UUID JOB = UUID.fromString("74af7d24-2c46-47a9-a281-7b3a11b54482");
    private final AgentHttpConfiguration configuration = new AgentHttpConfiguration();
    private final JsonMapper mapper = configuration.contractJsonMapper();
    private final AtomicReference<Reply> reply = new AtomicReference<>();
    private final AtomicReference<String> lastPath = new AtomicReference<>();
    private final AtomicReference<String> lastMethod = new AtomicReference<>();
    private final AtomicReference<String> lastKey = new AtomicReference<>();
    private final AtomicReference<String> lastAuthorization = new AtomicReference<>();
    private final AtomicReference<String> lastCookie = new AtomicReference<>();
    private final AtomicReference<String> lastBody = new AtomicReference<>();
    private final AtomicReference<String> lastContentType = new AtomicReference<>();
    private final AtomicInteger calls = new AtomicInteger();
    private final ExecutorService workers = Executors.newCachedThreadPool();
    private HttpServer upstream;
    private CloseableHttpClient http;
    private AdminLawGateway gateway;
    private AgentProperties properties;

    @BeforeAll void start() throws Exception {
        upstream = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        upstream.setExecutor(workers);
        upstream.createContext("/", this::serve);
        upstream.start();
        properties = new AgentProperties(URI.create("http://127.0.0.1:" + upstream.getAddress().getPort()), KEY,
                Duration.ofMillis(250), Duration.ofMillis(250));
        http = configuration.agentHttpClient(properties);
        gateway = new AdminLawGateway(new AgentHttpTransport(configuration.agentRestClient(http), properties, mapper));
    }
    @AfterAll void close() throws Exception { upstream.stop(0); http.close(); workers.shutdownNow(); }
    @BeforeEach void resetStub() { calls.set(0); respond(200, catalog()); }

    private void serve(HttpExchange exchange) throws IOException {
        calls.incrementAndGet();
        lastPath.set(exchange.getRequestURI().toASCIIString()); lastMethod.set(exchange.getRequestMethod());
        lastKey.set(exchange.getRequestHeaders().getFirst("X-Internal-Api-Key"));
        lastAuthorization.set(exchange.getRequestHeaders().getFirst("Authorization"));
        lastCookie.set(exchange.getRequestHeaders().getFirst("Cookie"));
        lastContentType.set(exchange.getRequestHeaders().getFirst("Content-Type"));
        lastBody.set(new String(exchange.getRequestBody().readAllBytes(), java.nio.charset.StandardCharsets.UTF_8));
        Reply chosen = reply.get();
        try {
            if (chosen.delay > 0) Thread.sleep(chosen.delay);
            exchange.getResponseHeaders().set("Content-Type", chosen.contentType);
            if (chosen.status == 302) exchange.getResponseHeaders().set("Location", properties.baseUrl() + "/must-not-follow");
            byte[] bytes = chosen.body.getBytes(java.nio.charset.StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(chosen.status, bytes.length);
            exchange.getResponseBody().write(bytes);
        } catch (InterruptedException ignored) { Thread.currentThread().interrupt(); }
        finally { exchange.close(); }
    }
    private record Reply(int status, String body, String contentType, int delay) { }
    private ObjectNode envelope(JsonNode data) {
        ObjectNode result = mapper.createObjectNode().put("schema_version", "1.0"); result.set("data", data); return result;
    }
    private void respond(int status, JsonNode data) { raw(status, mapper.writeValueAsString(envelope(data))); }
    private void raw(int status, String body) { reply.set(new Reply(status, body, "application/json", 0)); }
    private JsonNode read(String path) { return gateway.read(path, UUID.randomUUID()); }
    private void error(Runnable action, int status, String code) {
        assertThatThrownBy(action::run).isInstanceOfSatisfying(ApiException.class, failure -> {
            assertThat(failure.status.value()).isEqualTo(status); assertThat(failure.code).isEqualTo(code);
            assertThat(failure.getMessage()).doesNotContain(KEY, "upstream-secret");
        });
    }
    private ObjectNode catalog() {
        ObjectNode data = mapper.createObjectNode().put("dataset_id", "dataset-old").put("loaded_at", "2026-10-21T05:00:00+00:00")
                .putNull("collected_at").put("law_count", 1).put("version_count", 1).put("article_count", 2);
        data.putArray("items").addObject().put("law_key", "subcontract_act").put("law_name", "하도급법")
                .put("version_count", 1).put("article_count", 2);
        return data;
    }
    private ObjectNode version() {
        return mapper.createObjectNode().put("version_id", VERSION).put("effective_date", "2026-01-01")
                .put("promulgation_date", "2025-12-01").put("promulgation_no", "12345")
                .put("source_url", "https://www.law.go.kr/법령/하도급법?시행일=20260101")
                .put("source_commit", COMMIT).put("article_count", 2);
    }
    private ObjectNode versions() {
        ObjectNode data = mapper.createObjectNode().put("law_key", "subcontract_act").put("law_name", "하도급법");
        data.putArray("items").add(version()); return data;
    }
    private ObjectNode article() {
        ObjectNode data = version().put("law_key", "subcontract_act").put("law_name", "하도급법").put("citation", "하도급법 제16조의2");
        ObjectNode article = data.putObject("article").put("no", "제16조의2").put("title", "조건 변경").put("deleted", false)
                .putNull("chapter").putNull("section").put("text", "원문 😀\n둘째 줄");
        article.putArray("paragraphs").addObject().putNull("no").put("text", "원문 😀\n둘째 줄");
        return data;
    }
    private ObjectNode page() {
        ObjectNode data = version().put("law_key", "subcontract_act").put("total", 2).put("offset", 1).put("limit", 1);
        data.putArray("items").addObject().put("no", "제16조의2").put("title", "조건 변경").put("deleted", false);
        return data;
    }
    private ObjectNode job(String status) {
        ObjectNode data = mapper.createObjectNode().put("job_id", JOB.toString()).put("status", status)
                .put("started_at", "2026-10-21T05:00:00+00:00").put("previous_dataset_id", "dataset-old")
                .putNull("finished_at").putNull("dataset_id").putNull("error_code");
        if (!status.equals("RUNNING")) data.put("finished_at", "2026-10-21T05:01:00Z");
        if (status.equals("SUCCEEDED")) data.put("dataset_id", "dataset-new");
        if (status.equals("FAILED")) data.put("error_code", "REFRESH_FAILED");
        return data;
    }
    private ObjectNode upstreamError(String code) {
        ObjectNode result = mapper.createObjectNode().putNull("request_id");
        result.putObject("error").put("code", code).put("message", "upstream-secret " + KEY).put("retryable", true);
        return result;
    }

    @Test void forwardsConfiguredCredentialsOnlyAndUnwrapsCatalog() {
        assertThat(read(ROOT)).isEqualTo(catalog());
        assertThat(lastKey.get()).isEqualTo(KEY);
        assertThat(lastAuthorization.get()).isNull(); assertThat(lastCookie.get()).isNull();
        assertThat(lastBody.get()).isEmpty(); assertThat(lastMethod.get()).isEqualTo("GET");
    }
    @Test void versionsAndExactPagedArticlesPreserveSourceAndContent() {
        respond(200, versions()); assertThat(read(LAW)).isEqualTo(versions());
        respond(200, page()); assertThat(read(LAW + "/" + VERSION + "/articles?offset=1&limit=1")).isEqualTo(page());
        respond(200, article());
        CurrentUser current = mock(CurrentUser.class);
        AdminLawController controller = new AdminLawController(gateway, current);
        JsonNode actual = controller.article("subcontract_act", VERSION, "제16조의2", new MockHttpServletRequest());
        assertThat(actual).isEqualTo(article());
        assertThat(lastPath.get()).endsWith("/articles/%EC%A0%9C16%EC%A1%B0%EC%9D%982").doesNotContain("%25");
        verify(current).requireAdmin();
    }
    @Test void emptyCollectionsRemainAvailableAndDoNotTurnIntoErrors() {
        ObjectNode empty = catalog().put("law_count", 0).put("version_count", 0).put("article_count", 0);
        empty.putArray("items"); respond(200, empty); assertThat(read(ROOT)).isEqualTo(empty);
        ObjectNode noVersions = versions(); noVersions.putArray("items"); respond(200, noVersions);
        assertThat(read(LAW)).isEqualTo(noVersions);
    }
    @Test void refreshRoutesPreserveStateAndAcceptedStatus() {
        respond(200, mapper.createObjectNode().putNull("item"));
        assertThat(gateway.latestRefresh(UUID.randomUUID()).get("item").isNull()).isTrue();
        respond(202, job("RUNNING")); assertThat(gateway.startRefresh(UUID.randomUUID())).isEqualTo(job("RUNNING"));
        assertThat(lastMethod.get()).isEqualTo("POST"); assertThat(lastBody.get()).isEmpty();
        assertThat(lastContentType.get()).isNull();
        assertThat(lastPath.get()).isEqualTo(ROOT + "/refresh-jobs");
        for (String state : List.of("RUNNING", "SUCCEEDED", "FAILED")) {
            respond(200, job(state)); assertThat(gateway.refreshJob(JOB, UUID.randomUUID())).isEqualTo(job(state));
        }
        ObjectNode latest = mapper.createObjectNode(); latest.set("item", job("SUCCEEDED"));
        respond(200, latest); assertThat(gateway.latestRefresh(UUID.randomUUID())).isEqualTo(latest);
    }
    @Test void rejectsInvalidJobIdentityAndImpossibleStates() {
        List<Consumer<ObjectNode>> changes = List.of(
                data -> data.put("job_id", UUID.randomUUID().toString()), data -> data.put("job_id", "1-1-1-1-1"),
                data -> data.put("status", "QUEUED"), data -> data.put("started_at", "yesterday"),
                data -> data.put("finished_at", "2026-10-21T05:01:00Z"), data -> data.put("error_code", "upstream-secret"),
                data -> data.put("dataset_id", "dataset-new"), data -> data.put("unexpected", "upstream-secret"));
        for (Consumer<ObjectNode> change : changes) {
            ObjectNode invalid = job("RUNNING"); change.accept(invalid); respond(200, invalid);
            error(() -> gateway.refreshJob(JOB, UUID.randomUUID()), 502, "UPSTREAM_INVALID_RESPONSE");
        }
        respond(200, job("SUCCEEDED").putNull("dataset_id"));
        error(() -> gateway.refreshJob(JOB, UUID.randomUUID()), 502, "UPSTREAM_INVALID_RESPONSE");
        respond(200, job("FAILED").put("finished_at", "2026-10-20T00:00:00Z"));
        error(() -> gateway.refreshJob(JOB, UUID.randomUUID()), 502, "UPSTREAM_INVALID_RESPONSE");
    }
    @Test void rejectsReadCountsUnknownFieldsWrongIdentityAndUnsafeSource() {
        for (Consumer<ObjectNode> change : List.<Consumer<ObjectNode>>of(
                data -> data.put("law_count", 4), data -> data.put("version_count", -1),
                data -> data.put("article_count", 3), data -> data.put("law_count", "1"),
                data -> data.put("loaded_at", "invalid"), data -> data.put("internal_key", KEY))) {
            ObjectNode invalid = catalog(); change.accept(invalid); respond(200, invalid);
            error(() -> read(ROOT), 502, "UPSTREAM_INVALID_RESPONSE");
        }
        for (String url : List.of("javascript:alert(1)", "file:///tmp/secret", "https://user:pass@example.com/path", "/relative")) {
            ObjectNode invalid = versions(); ((ObjectNode) invalid.get("items").get(0)).put("source_url", url); respond(200, invalid);
            error(() -> read(LAW), 502, "UPSTREAM_INVALID_RESPONSE");
        }
        respond(200, versions().put("law_key", "sme_act")); error(() -> read(LAW), 502, "UPSTREAM_INVALID_RESPONSE");
        respond(200, page().put("offset", 0)); error(() -> read(LAW + "/" + VERSION + "/articles?offset=1&limit=1"), 502, "UPSTREAM_INVALID_RESPONSE");
        ObjectNode wrongArticle = article(); ((ObjectNode) wrongArticle.get("article")).put("no", "제1조"); respond(200, wrongArticle);
        error(() -> read(LAW + "/" + VERSION + "/articles/16조의2"), 502, "UPSTREAM_INVALID_RESPONSE");
    }
    @ParameterizedTest @ValueSource(strings = {"null", "{}", "[]", "<html>secret</html>", "{\"schema_version\":\"1.0\",\"schema_version\":\"1.0\",\"data\":{}}", "{} {}"})
    void rejectsMalformedOrAmbiguousJson(String body) {
        raw(200, body); error(() -> read(ROOT), 502, "UPSTREAM_INVALID_RESPONSE");
    }
    @Test void rejectsWrongEnvelopeAndMediaType() {
        raw(200, mapper.writeValueAsString(envelope(catalog()).put("schema_version", "0.3")));
        error(() -> read(ROOT), 502, "UPSTREAM_INVALID_RESPONSE");
        reply.set(new Reply(200, mapper.writeValueAsString(envelope(catalog())), "text/html", 0));
        error(() -> read(ROOT), 502, "UPSTREAM_INVALID_RESPONSE");
    }
    @Test void mapsKnownErrorsWithoutExposingUpstreamText() {
        int[] statuses = {400, 404, 409, 502, 503, 504};
        String[] codes = {"VALIDATION_ERROR", "LAW_NOT_FOUND", "REFRESH_BUSY", "UPSTREAM_INVALID_RESPONSE", "UPSTREAM_UNAVAILABLE", "UPSTREAM_TIMEOUT"};
        for (int i = 0; i < statuses.length; i++) {
            raw(statuses[i], mapper.writeValueAsString(upstreamError(codes[i])));
            error(() -> gateway.startRefresh(UUID.randomUUID()), statuses[i], codes[i]);
        }
        raw(409, mapper.writeValueAsString(upstreamError("SENSITIVE_ERROR")));
        error(() -> gateway.startRefresh(UUID.randomUUID()), 502, "UPSTREAM_INVALID_RESPONSE");
        raw(503, "upstream-secret"); error(() -> read(ROOT), 502, "UPSTREAM_INVALID_RESPONSE");
    }
    @Test void preservesLawErrorRequestIdShapeAndBooleanRetryablePolicy() {
        ObjectNode valid = upstreamError("UPSTREAM_UNAVAILABLE").put("request_id", JOB.toString().toUpperCase(java.util.Locale.ROOT));
        ((ObjectNode) valid.get("error")).put("retryable", false);
        raw(503, mapper.writeValueAsString(valid));
        UUID requestId = UUID.randomUUID();
        assertThatThrownBy(() -> gateway.read(ROOT, requestId)).isInstanceOfSatisfying(ApiException.class, failure -> {
            assertThat(failure.status.value()).isEqualTo(503);
            assertThat(failure.code).isEqualTo("UPSTREAM_UNAVAILABLE");
            assertThat(failure.requestId).isEqualTo(requestId);
            assertThat(failure.retryable).isTrue();
        });
        raw(503, mapper.writeValueAsString(valid.put("request_id", "1-1-1-1-1")));
        error(() -> read(ROOT), 502, "UPSTREAM_INVALID_RESPONSE");
        valid.putNull("request_id");
        ((ObjectNode) valid.get("error")).put("retryable", "true");
        raw(503, mapper.writeValueAsString(valid));
        error(() -> read(ROOT), 502, "UPSTREAM_INVALID_RESPONSE");
        raw(409, mapper.writeValueAsString(upstreamError("REFRESH_BUSY")));
        error(() -> read(ROOT), 502, "UPSTREAM_INVALID_RESPONSE");
    }
    @Test void doesNotFollowRedirectOrAcceptInternalAuthenticationFailure() {
        raw(302, "upstream-secret"); error(() -> read(ROOT), 502, "UPSTREAM_INVALID_RESPONSE");
        assertThat(calls.get()).isEqualTo(1);
        raw(401, "upstream-secret"); error(() -> read(ROOT), 502, "UPSTREAM_INVALID_RESPONSE");
    }
    @Test void timeoutAndMissingKeyAreSafeAndDoNotRetry() {
        reply.set(new Reply(200, mapper.writeValueAsString(envelope(catalog())), "application/json", 800));
        error(() -> read(ROOT), 504, "UPSTREAM_TIMEOUT"); assertThat(calls.get()).isEqualTo(1);
        var missing = new AgentProperties(properties.baseUrl(), "", properties.connectTimeout(), properties.responseTimeout());
        var noKey = new AdminLawGateway(new AgentHttpTransport(configuration.agentRestClient(http), missing, mapper));
        error(() -> noKey.read(ROOT, UUID.randomUUID()), 503, "UPSTREAM_UNAVAILABLE"); assertThat(calls.get()).isEqualTo(1);
    }
}
