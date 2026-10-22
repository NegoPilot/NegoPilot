package com.negopilot.backend.agent;

import com.negopilot.backend.chat.AgentQueryEndpointTestConfiguration;
import com.negopilot.backend.common.api.ApiException;
import com.negopilot.backend.common.http.AgentHttpTransport;
import com.negopilot.backend.support.HttpSliceTestConfiguration;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.Function;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.web.client.RestClient;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

@SpringBootTest(classes = {HttpSliceTestConfiguration.class, AgentQueryEndpointTestConfiguration.class},
        webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class GenerationHttpIntegrationTest {
    private static final JsonMapper JSON = JsonMapper.builder().build();
    private static final String ID = "62751dc6-e6ad-41dc-a7fd-a11f79b5f3c2";
    private static final String TEST_KEY = "generation-test-internal-key";
    private static final String QUERIES = "/api/v2/agent/queries";
    private static final HttpServer UPSTREAM;
    private static final ExecutorService EXECUTOR = Executors.newCachedThreadPool();
    private static final AtomicReference<Function<JsonNode, StubResponse>> RESPONSE = new AtomicReference<>();
    private static final AtomicReference<JsonNode> LAST_REQUEST = new AtomicReference<>();
    private static final AtomicReference<String> LAST_KEY = new AtomicReference<>();
    private static final AtomicReference<String> LAST_PATH = new AtomicReference<>();
    private static final AtomicReference<String> LAST_AUTHORIZATION = new AtomicReference<>();
    private static final AtomicReference<String> LAST_COOKIE = new AtomicReference<>();
    private static final AtomicInteger CALLS = new AtomicInteger();
    private static final HttpClient CLIENT = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(2)).build();

    static {
        try {
            UPSTREAM = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            UPSTREAM.setExecutor(EXECUTOR);
            UPSTREAM.createContext("/", GenerationHttpIntegrationTest::serve);
            UPSTREAM.start();
        } catch (IOException failure) {
            throw new ExceptionInInitializerError(failure);
        }
    }

    @LocalServerPort int port;
    @Autowired RestClient agentRestClient;
    @Autowired JsonMapper mapper;
    @Autowired GenerationResponseValidator responseValidator;

    @DynamicPropertySource
    static void properties(DynamicPropertyRegistry registry) {
        registry.add("agent.base-url", () -> "http://127.0.0.1:" + UPSTREAM.getAddress().getPort());
        registry.add("agent.internal-api-key", () -> TEST_KEY);
        registry.add("agent.connect-timeout", () -> "250ms");
        registry.add("agent.response-timeout", () -> "1s");
        registry.add("agent.execution-mode", () -> "live");
    }

    @BeforeEach
    void reset() {
        CALLS.set(0);
        LAST_REQUEST.set(null);
        LAST_KEY.set(null);
        LAST_PATH.set(null);
        LAST_AUTHORIZATION.set(null);
        LAST_COOKIE.set(null);
        RESPONSE.set(request -> StubResponse.json(200, completed(request)));
    }

    @AfterAll
    static void closeStub() {
        UPSTREAM.stop(0);
        EXECUTOR.shutdownNow();
        CLIENT.close();
    }

    @Test
    void liveQueriesGenerateIdAndDefaultsAndForwardOnlyConfiguredCredentials() throws Exception {
        var response = post(QUERIES, JSON.createObjectNode().put("query", "새 기업에 대해 설명해 줘"));
        assertThat(response.statusCode()).isEqualTo(200);
        JsonNode body = JSON.readTree(response.body());
        assertThat(UUID.fromString(body.path("request_id").stringValue())).isNotNull();
        assertThat(body.path("request_id")).isEqualTo(LAST_REQUEST.get().path("request_id"));
        assertThat(LAST_REQUEST.get().path("history").isEmpty()).isTrue();
        assertThat(LAST_REQUEST.get().path("context")).isEqualTo(context(null));
        assertThat(LAST_PATH.get()).isEqualTo("POST /internal/v2/query-executions");
        assertThat(LAST_KEY.get()).isEqualTo(TEST_KEY);
        assertThat(LAST_AUTHORIZATION.get()).isNull();
        assertThat(LAST_COOKIE.get()).isNull();
        assertThat(body.path("schema_version").stringValue()).isEqualTo("0.3");
        assertThat(body.path("mode").stringValue()).isEqualTo("live");
        assertThat(CALLS.get()).isEqualTo(1);
        assertThat(response.body()).doesNotContain(TEST_KEY, "DEMO_MODE_REQUIRED");
    }

    @Test
    void preservesOriginalQueryHistoryContextAndEveryAnswerField() throws Exception {
        ObjectNode request = query("\u00a0  선택값과 다른 CUSTOM_42 기업을 설명해 줘 😀 e\u0301\n\u0085");
        request.set("context", context("NEW_SUPPLIER-never-in-fixtures"));
        request.putArray("history").addObject().put("role", "assistant").put("content", "  이전 원문 😀  ");
        var response = post(QUERIES, request);
        assertThat(response.statusCode()).isEqualTo(200);
        assertThat(LAST_REQUEST.get()).isEqualTo(request);
        JsonNode body = JSON.readTree(response.body());
        assertThat(body).isEqualTo(completed(request));
        assertThat(body.at("/decomposition/original_query")).isEqualTo(request.get("query"));
        assertThat(body.at("/answer/verification/masked").booleanValue()).isTrue();
        assertThat(body.at("/answer/verification/ungrounded_numbers/0").stringValue()).isEqualTo("999");
        assertThat(body.at("/answer/verification").has("ok")).isFalse();
        assertThat(body.at("/answer/evidence/0/meta/nested/0").intValue()).isEqualTo(1234567890);
        assertThat(CALLS.get()).isEqualTo(1);
    }

    @ParameterizedTest
    @ValueSource(strings = {"different", "trimmed", "unicode-normalized"})
    void rejectsOriginalQueryMismatchIncludingWhitespaceAndUnicodeChanges(String kind) throws Exception {
        ObjectNode request = query("  원문 질문 😀 e\u0301  \n");
        RESPONSE.set(input -> {
            ObjectNode body = completed(input);
            String original = input.get("query").stringValue();
            String changed = switch (kind) {
                case "trimmed" -> original.strip();
                case "unicode-normalized" -> original.replace("e\u0301", "\u00e9");
                default -> "다른 질문";
            };
            ((ObjectNode) body.get("decomposition")).put("original_query", changed);
            return StubResponse.json(200, body);
        });
        var response = post(QUERIES, request);
        assertError(response, 502, "UPSTREAM_INVALID_RESPONSE", false);
        assertThat(JSON.readTree(response.body()).path("request_id").stringValue()).isEqualTo(ID);
        assertThat(LAST_REQUEST.get()).isEqualTo(request);
        assertThat(CALLS.get()).isEqualTo(1);
    }

    @ParameterizedTest
    @ValueSource(strings = {"live", "stub"})
    void preservesExplicitUpstreamModeWithoutApplyingDemoGuard(String mode) throws Exception {
        RESPONSE.set(request -> StubResponse.json(200, completed(request).put("mode", mode)));
        var response = post(QUERIES, query("q"));
        assertThat(response.statusCode()).isEqualTo(200);
        assertThat(JSON.readTree(response.body()).path("mode").stringValue()).isEqualTo(mode);
    }

    @ParameterizedTest
    @ValueSource(strings = {"error", "missing_args"})
    void partialRoutingFailureStillAllowsCompletedGeneratedAnswer(String status) throws Exception {
        RESPONSE.set(request -> {
            ObjectNode body = completed(request);
            ObjectNode subQuery = ((ObjectNode) body.at("/decomposition/sub_queries/0")).deepCopy().put("id", "q2");
            ((ArrayNode) body.at("/decomposition/sub_queries")).add(subQuery);
            ObjectNode outcome = ((ObjectNode) body.at("/routing/outcomes/0")).deepCopy().put("id", "q2")
                    .put("status", status).put("message", "private partial failure");
            if (status.equals("missing_args")) {
                outcome.putArray("missing").add("supplier");
                outcome.putArray("calls");
            } else {
                ((ObjectNode) outcome.at("/calls/0")).put("error", "private tool exception");
            }
            ((ArrayNode) body.at("/routing/outcomes")).add(outcome);
            ((ArrayNode) body.at("/answer/notices")).add("일부 조회 결과가 없어 확인된 근거만 사용했습니다.");
            return StubResponse.json(200, body);
        });
        var response = post(QUERIES, query("q"));
        assertThat(response.statusCode()).isEqualTo(200);
        JsonNode body = JSON.readTree(response.body());
        assertThat(body.path("execution_status").stringValue()).isEqualTo("COMPLETED");
        assertThat(body.path("generation_status").stringValue()).isEqualTo("COMPLETED");
        assertThat(body.path("error").isNull()).isTrue();
        assertThat(body.at("/answer/kind").stringValue()).isEqualTo("answer");
        assertThat(body.at("/answer/notices").size()).isEqualTo(2);
        assertThat(body.at("/routing/outcomes/1/status").stringValue()).isEqualTo(status);
        assertThat(body.at("/routing/outcomes/1/message").stringValue()).isEqualTo("Routing details are unavailable.");
    }

    @ParameterizedTest
    @ValueSource(strings = {"RFQ_NOT_FOUND", "SUPPLIER_NOT_FOUND", "SUPPLIER_AMBIGUOUS", "VALUE_OUT_OF_RANGE"})
    void exposesPublicToolErrorCodeWhileMaskingUpstreamDiagnostics(String code) throws Exception {
        RESPONSE.set(request -> {
            ObjectNode body = completed(request);
            ObjectNode first = (ObjectNode) body.at("/routing/outcomes/0");
            first.put("status", "error");
            ObjectNode call = (ObjectNode) first.at("/calls/0");
            call.put("error", "private upstream connection details").put("error_code", code).putNull("result");
            first.putArray("referenced_articles").add(call.deepCopy());
            return StubResponse.json(200, body);
        });
        var response = post(QUERIES, query("q"));
        assertThat(response.statusCode()).isEqualTo(200);
        JsonNode body = JSON.readTree(response.body());
        assertThat(body.at("/routing/outcomes/0/calls/0/error_code").stringValue()).isEqualTo(code);
        assertThat(body.at("/routing/outcomes/0/referenced_articles/0/error_code").stringValue()).isEqualTo(code);
        assertThat(body.at("/routing/outcomes/0/calls/0/error").stringValue()).isEqualTo("Tool execution failed.");
        assertThat(body.at("/routing/outcomes/0/referenced_articles/0/error").stringValue()).isEqualTo("Tool execution failed.");
        assertThat(response.body()).doesNotContain("private upstream connection details");
    }

    @ParameterizedTest
    @ValueSource(strings = {"DB_UNAVAILABLE", "INTERNAL", "private-secret"})
    void rejectsNonpublicToolErrorCodeWithoutReturningIt(String code) throws Exception {
        RESPONSE.set(request -> {
            ObjectNode body = completed(request);
            ((ObjectNode) body.at("/routing/outcomes/0/calls/0"))
                    .put("error", "private upstream connection details").put("error_code", code);
            return StubResponse.json(200, body);
        });
        var response = post(QUERIES, query("q"));
        assertError(response, 502, "UPSTREAM_INVALID_RESPONSE", false);
        assertThat(response.body()).doesNotContain(code, "private upstream connection details");
    }

    @ParameterizedTest
    @ValueSource(booleans = {false, true})
    void sanitizesDiagnosticsOnValidatedCopyWithoutChangingAnswerOrToolResults(boolean empty) throws Exception {
        String diagnostic = empty ? "" : "private diagnostic stack trace";
        AtomicReference<ObjectNode> raw = new AtomicReference<>();
        RESPONSE.set(request -> {
            ObjectNode body = failed(request, false);
            ObjectNode first = (ObjectNode) body.at("/routing/outcomes/0");
            first.put("status", "error").put("message", diagnostic);
            ((ObjectNode) first.at("/calls/0")).put("error", diagnostic);
            ((ObjectNode) first.at("/calls/0/result")).put("unchanged_diagnostic_text", diagnostic);
            ObjectNode reference = ((ObjectNode) first.at("/calls/0")).deepCopy().put("server", "law-mcp")
                    .put("tool", "get_article");
            first.putArray("referenced_articles").add(reference).add(reference.deepCopy().putNull("error"));
            ObjectNode secondQuery = ((ObjectNode) body.at("/decomposition/sub_queries/0")).deepCopy().put("id", "q2");
            ((ArrayNode) body.at("/decomposition/sub_queries")).add(secondQuery);
            ObjectNode second = first.deepCopy().put("id", "q2").put("status", "empty").putNull("message");
            second.putArray("calls");
            second.putArray("referenced_articles");
            ((ArrayNode) body.at("/routing/outcomes")).add(second);
            ((ObjectNode) body.get("routing")).put("halted", true).put("halt_reason", diagnostic);
            raw.set(body.deepCopy());
            return StubResponse.json(200, body);
        });
        var response = post(QUERIES, query("q"));
        assertThat(response.statusCode()).isEqualTo(200);
        ObjectNode expected = raw.get().deepCopy();
        ((ObjectNode) expected.at("/routing/outcomes/0")).put("message", "Routing details are unavailable.");
        ((ObjectNode) expected.at("/routing/outcomes/0/calls/0")).put("error", "Tool execution failed.");
        ((ObjectNode) expected.at("/routing/outcomes/0/referenced_articles/0")).put("error", "Tool execution failed.");
        ((ObjectNode) expected.get("routing")).put("halt_reason", "Routing halted because a dependency returned no results.");
        ((ObjectNode) expected.get("error")).put("message", "Answer generation failed.");
        JsonNode body = JSON.readTree(response.body());
        assertThat(body).isEqualTo(expected);
        assertThat(body.get("answer")).isEqualTo(raw.get().get("answer"));
        assertThat(body.at("/routing/outcomes/0/calls/0/result")).isEqualTo(raw.get().at("/routing/outcomes/0/calls/0/result"));
        assertThat(body.at("/routing/outcomes/0/referenced_articles/0/result"))
                .isEqualTo(raw.get().at("/routing/outcomes/0/referenced_articles/0/result"));
        assertThat(raw.get().at("/routing/outcomes/0/message").stringValue()).isEqualTo(diagnostic);
    }

    @Test
    void decompositionFailureRetainsAnswerAndUsesSafeFailureMessage() throws Exception {
        RESPONSE.set(request -> StubResponse.json(200, failed(request, true)));
        var response = post(QUERIES, query("q"));
        assertThat(response.statusCode()).isEqualTo(200);
        JsonNode body = JSON.readTree(response.body());
        assertThat(body.at("/error/message").stringValue()).isEqualTo("Query decomposition failed.");
        assertThat(body.at("/error/code").stringValue()).isEqualTo("DECOMPOSITION_FAILED");
        assertThat(body.path("answer")).isEqualTo(failed(LAST_REQUEST.get(), true).path("answer"));
        assertThat(body.at("/routing/status").stringValue()).isEqualTo("not_routed");
        assertThat(body.at("/routing/outcomes").isEmpty()).isTrue();
    }

    @ParameterizedTest
    @ValueSource(strings = {"clarify", "refusal", "no_result"})
    void preservesCompletedGenerationForFixedAnswers(String kind) throws Exception {
        RESPONSE.set(request -> StubResponse.json(200, fixedAnswer(request, kind)));
        var response = post(QUERIES, query("q"));
        assertThat(response.statusCode()).isEqualTo(200);
        assertThat(JSON.readTree(response.body())).isEqualTo(fixedAnswer(LAST_REQUEST.get(), kind));
    }

    @ParameterizedTest
    @ValueSource(strings = {"request-id", "context", "missing-answer", "extra-envelope", "generation-status", "empty-answer", "blank-answer",
            "citation-target", "citation-number", "evidence-id", "not-citable", "citation-label", "citation-url", "verification-property"})
    void rejectsInvalidAnswerOrEnvelopeBeforeReturningIt(String kind) throws Exception {
        RESPONSE.set(request -> {
            ObjectNode body = completed(request);
            ObjectNode answer = (ObjectNode) body.get("answer");
            switch (kind) {
                case "request-id" -> body.put("request_id", UUID.randomUUID().toString());
                case "context" -> body.set("context", context("another-company"));
                case "missing-answer" -> body.remove("answer");
                case "extra-envelope" -> body.put("debug", "private detail");
                case "generation-status" -> body.put("generation_status", "FAILED");
                case "empty-answer" -> answer.put("text", "");
                case "blank-answer" -> answer.put("text", " \t\n");
                case "citation-target" -> ((ObjectNode) answer.at("/citations/0")).put("evidence_id", "missing");
                case "citation-number" -> ((ArrayNode) answer.get("citations")).add(answer.at("/citations/0").deepCopy());
                case "evidence-id" -> ((ArrayNode) answer.get("evidence")).add(answer.at("/evidence/0").deepCopy());
                case "not-citable" -> ((ObjectNode) answer.at("/evidence/0")).put("citable", false);
                case "citation-label" -> ((ObjectNode) answer.at("/citations/0")).put("label", "wrong source");
                case "citation-url" -> ((ObjectNode) answer.at("/citations/0")).put("source_url", "https://invalid.example/other");
                default -> ((ObjectNode) answer.get("verification")).put("ok", false);
            }
            return StubResponse.json(200, body);
        });
        assertError(post(QUERIES, query("q")), 502, "UPSTREAM_INVALID_RESPONSE", false);
        assertThat(CALLS.get()).isEqualTo(1);
    }

    @ParameterizedTest
    @ValueSource(strings = {"{", "null", "[]", "{}", "{} {}", "{\"x\":1,\"x\":2}"})
    void rejectsMalformedSuccessJson(String body) throws Exception {
        RESPONSE.set(request -> new StubResponse(200, body, "application/json", 0));
        assertError(post(QUERIES, query("q")), 502, "UPSTREAM_INVALID_RESPONSE", false);
    }

    @ParameterizedTest
    @ValueSource(ints = {400, 500, 503, 504})
    void knownHttpErrorsKeepStatusAndRequestIdWithSafeMessages(int status) throws Exception {
        RESPONSE.set(request -> StubResponse.json(status, httpError(request, status)));
        var response = post(QUERIES, query("q"));
        assertError(response, status, errorCode(status), status == 503 || status == 504);
        assertThat(JSON.readTree(response.body()).path("request_id").stringValue()).isEqualTo(ID);
        assertThat(response.body()).doesNotContain("private upstream exception");
        assertThat(CALLS.get()).isEqualTo(1);
    }

    @ParameterizedTest
    @ValueSource(ints = {400, 500})
    void genericPythonErrorWithNullIdReturnsTheGatewayId(int status) throws Exception {
        RESPONSE.set(request -> StubResponse.json(status, httpError(request, status).putNull("request_id")));
        var response = post(QUERIES, query("q"));
        assertError(response, status, errorCode(status), false);
        assertThat(JSON.readTree(response.body()).path("request_id").stringValue()).isEqualTo(ID);
    }

    @ParameterizedTest
    @ValueSource(strings = {"wrong-id", "wrong-code", "retryable", "blank-message", "unknown-field"})
    void malformedInternalServerErrorBecomesBadGateway(String kind) throws Exception {
        RESPONSE.set(request -> {
            ObjectNode body = httpError(request, 500);
            ObjectNode error = (ObjectNode) body.get("error");
            switch (kind) {
                case "wrong-id" -> body.put("request_id", UUID.randomUUID().toString());
                case "wrong-code" -> error.put("code", "UPSTREAM_UNAVAILABLE");
                case "retryable" -> error.put("retryable", true);
                case "blank-message" -> error.put("message", " ");
                default -> error.put("debug", "private detail");
            }
            return StubResponse.json(500, body);
        });
        assertError(post(QUERIES, query("q")), 502, "UPSTREAM_INVALID_RESPONSE", false);
    }

    @ParameterizedTest
    @ValueSource(ints = {201, 204, 302, 307, 401, 403, 404, 429})
    void rejectsUnauthorizedAndUnexpectedHttpStatusWithoutRetry(int status) throws Exception {
        RESPONSE.set(request -> new StubResponse(status, "private detail " + TEST_KEY, "text/plain", 0));
        var response = post(QUERIES, query("q"));
        assertError(response, 502, "UPSTREAM_INVALID_RESPONSE", false);
        assertThat(response.body()).doesNotContain(TEST_KEY, "private detail");
        assertThat(CALLS.get()).isEqualTo(1);
    }

    @Test
    void rejectsWrongContentType() throws Exception {
        RESPONSE.set(request -> new StubResponse(200, JSON.writeValueAsString(completed(request)), "text/html", 0));
        assertError(post(QUERIES, query("q")), 502, "UPSTREAM_INVALID_RESPONSE", false);
    }

    @Test
    void timeoutDoesNotRetry() throws Exception {
        RESPONSE.set(request -> new StubResponse(200, JSON.writeValueAsString(completed(request)), "application/json", 1800));
        assertError(post(QUERIES, query("q")), 504, "UPSTREAM_TIMEOUT", true);
        assertThat(CALLS.get()).isEqualTo(1);
    }

    @Test
    void missingConfiguredKeyDoesNotCallPython() {
        var properties = properties(UPSTREAM.getAddress().getPort(), "");
        GenerationGateway gateway = new GenerationGateway(
                new AgentHttpTransport(agentRestClient, properties, mapper), responseValidator);
        assertThatThrownBy(() -> gateway.execute(UUID.fromString(ID), query("q")))
                .isInstanceOfSatisfying(ApiException.class, failure -> {
                    assertThat(failure.status.value()).isEqualTo(503);
                    assertThat(failure.code).isEqualTo("UPSTREAM_UNAVAILABLE");
                });
        assertThat(CALLS.get()).isZero();
    }

    @Test
    void refusedConnectionIsUnavailable() throws Exception {
        int closedPort;
        try (ServerSocket socket = new ServerSocket(0, 0, InetAddress.getLoopbackAddress())) {
            closedPort = socket.getLocalPort();
        }
        GenerationGateway gateway = new GenerationGateway(
                new AgentHttpTransport(agentRestClient, properties(closedPort, TEST_KEY), mapper), responseValidator);
        assertThatThrownBy(() -> gateway.execute(UUID.fromString(ID), query("q")))
                .isInstanceOfSatisfying(ApiException.class, failure -> {
                    assertThat(failure.status.value()).isEqualTo(503);
                    assertThat(failure.retryable).isTrue();
                });
    }

    private static AgentProperties properties(int port, String key) {
        return new AgentProperties(URI.create("http://127.0.0.1:" + port), key, Duration.ofMillis(250), Duration.ofSeconds(1));
    }

    private URI uri(String path) { return URI.create("http://127.0.0.1:" + port + path); }

    private HttpResponse<String> post(String path, JsonNode body) throws Exception {
        return CLIENT.send(HttpRequest.newBuilder(uri(path)).timeout(Duration.ofSeconds(5))
                .header("Content-Type", "application/json").header("X-Internal-Api-Key", "public-key-must-not-forward")
                .header("Authorization", "Bearer public-token-must-not-forward").header("Cookie", "public-cookie=private")
                .POST(HttpRequest.BodyPublishers.ofString(JSON.writeValueAsString(body))).build(), HttpResponse.BodyHandlers.ofString());
    }

    private static void assertError(HttpResponse<String> response, int status, String code, boolean retryable) {
        assertThat(response.statusCode()).isEqualTo(status);
        JsonNode body = JSON.readTree(response.body());
        assertThat(body.size()).isEqualTo(2);
        assertThat(body.has("request_id")).isTrue();
        assertThat(body.path("error").size()).isEqualTo(3);
        assertThat(body.at("/error/code").stringValue()).isEqualTo(code);
        assertThat(body.at("/error/retryable").booleanValue()).isEqualTo(retryable);
    }

    private static ObjectNode query(String text) {
        ObjectNode request = JSON.createObjectNode().put("request_id", ID).put("query", text);
        request.putArray("history");
        request.set("context", context(null));
        return request;
    }

    private static ObjectNode context(String supplierId) {
        ObjectNode context = JSON.createObjectNode();
        ArrayNode ids = context.putArray("selected_supplier_ids");
        if (supplierId != null) ids.add(supplierId);
        return context;
    }

    private static ObjectNode completed(JsonNode request) {
        ObjectNode body = (ObjectNode) JSON.readTree("""
                {"schema_version":"0.3","request_id":"placeholder","mode":"live",
                 "execution_status":"COMPLETED","generation_status":"COMPLETED","context":{},"error":null,
                 "decomposition":{"schema_version":"0.1","status":"ok","original_query":"q",
                   "sub_queries":[{"id":"q1","question":"기업 실적 조회","original_text":"q","intent":"supplier_detail",
                     "grounding":"required","confidence":0.95,"entities":{"supplier":"CUSTOM_42"},"inputs":[],"depends_on":[]}],
                   "clarification":null,"assumptions":[]},
                 "routing":{"schema_version":"0.1","status":"routed","outcomes":[{"id":"q1","intent":"supplier_detail",
                   "status":"ok","calls":[{"server":"business-mcp","tool":"get_supplier","args":{"supplier":"CUSTOM_42"},
                     "result":{"supplier_id":"CUSTOM_42","revenue":1234567890},"error":null}],
                   "referenced_articles":[],"missing":[],"message":null}],"halted":false,"halt_reason":null},
                 "answer":{"kind":"answer","text":"원문 생성 답변 1234567890원 [1].","citations":[
                   {"n":1,"evidence_id":"E1","label":"기업 실적","source_url":"https://example.test/supplier/CUSTOM_42"}],
                   "options":["추가 질문"],"grounded":true,"notices":["입력 기준 답변"],
                   "evidence":[{"id":"E1","sub_query":"q1","source":"business","kind":"supplier_detail",
                     "label":"기업 실적","text":"매출은 1234567890원이다.","citable":true,
                     "source_url":"https://example.test/supplier/CUSTOM_42","meta":{"nested":[1234567890,null,true]}}],
                   "verification":{"attempts":2,"unknown_citations":[],"ungrounded_numbers":["999"],
                     "ungrounded_articles":[],"masked":true}}}
                """);
        body.set("request_id", request.get("request_id"));
        body.set("context", request.get("context").deepCopy());
        ((ObjectNode) body.get("decomposition")).set("original_query", request.get("query"));
        ((ObjectNode) body.at("/decomposition/sub_queries/0")).set("original_text", request.get("query"));
        return body;
    }

    private static ObjectNode failed(JsonNode request, boolean decompositionFailed) {
        ObjectNode body = completed(request).put("execution_status", "FAILED").put("generation_status", "FAILED");
        ((ObjectNode) body.get("answer")).put("kind", "failed").put("text", "코어가 생성한 실패 안내 원문");
        if (decompositionFailed) {
            ((ObjectNode) body.get("decomposition")).put("status", "failed").putArray("sub_queries");
            ((ObjectNode) body.get("routing")).put("status", "not_routed").putArray("outcomes");
            ((ObjectNode) body.get("answer")).putArray("citations");
            ((ObjectNode) body.get("answer")).putArray("evidence");
        }
        body.putObject("error").put("code", decompositionFailed ? "DECOMPOSITION_FAILED" : "GENERATION_FAILED")
                .put("message", decompositionFailed ? "Query decomposition failed." : "Answer generation failed.")
                .put("retryable", false);
        return body;
    }

    private static ObjectNode fixedAnswer(JsonNode request, String kind) {
        ObjectNode body = completed(request);
        ObjectNode answer = (ObjectNode) body.get("answer");
        answer.put("kind", kind).put("text", "코어 고정 안내 원문").putNull("verification");
        answer.putArray("citations");
        answer.putArray("evidence");
        if (kind.equals("clarify")) {
            body.put("execution_status", "NEEDS_INPUT");
            ObjectNode decomposition = (ObjectNode) body.get("decomposition");
            decomposition.put("status", "clarify").putArray("sub_queries");
            ObjectNode clarification = decomposition.putObject("clarification").put("reason", "ambiguous").put("question", "어느 조건인가요?");
            clarification.putArray("options").add("품질").add("재무");
            ((ObjectNode) body.get("routing")).put("status", "not_routed").putArray("outcomes");
        } else if (kind.equals("refusal")) {
            ObjectNode subQuery = (ObjectNode) body.at("/decomposition/sub_queries/0");
            subQuery.put("intent", "out_of_scope").put("grounding", "none").putObject("entities");
            ObjectNode outcome = (ObjectNode) body.at("/routing/outcomes/0");
            outcome.put("intent", "out_of_scope").put("status", "out_of_scope").putArray("calls");
        } else {
            ((ObjectNode) body.at("/routing/outcomes/0")).put("status", "empty").putArray("calls");
        }
        return body;
    }

    private static ObjectNode httpError(JsonNode request, int status) {
        ObjectNode body = JSON.createObjectNode();
        body.set("request_id", request.get("request_id"));
        body.putObject("error").put("code", errorCode(status)).put("message", "private upstream exception")
                .put("retryable", status == 503 || status == 504);
        return body;
    }

    private static String errorCode(int status) {
        return switch (status) {
            case 400 -> "VALIDATION_ERROR";
            case 500 -> "INTERNAL_ERROR";
            case 503 -> "UPSTREAM_UNAVAILABLE";
            default -> "UPSTREAM_TIMEOUT";
        };
    }

    private record StubResponse(int status, String body, String contentType, long delayMillis) {
        static StubResponse json(int status, JsonNode body) {
            return new StubResponse(status, JSON.writeValueAsString(body), "application/json", 0);
        }
    }

    private static void serve(HttpExchange exchange) throws IOException {
        try (exchange) {
            CALLS.incrementAndGet();
            LAST_PATH.set(exchange.getRequestMethod() + " " + exchange.getRequestURI().getPath());
            LAST_KEY.set(exchange.getRequestHeaders().getFirst("X-Internal-Api-Key"));
            LAST_AUTHORIZATION.set(exchange.getRequestHeaders().getFirst("Authorization"));
            LAST_COOKIE.set(exchange.getRequestHeaders().getFirst("Cookie"));
            JsonNode request = JSON.readTree(exchange.getRequestBody().readAllBytes());
            LAST_REQUEST.set(request);
            StubResponse response = RESPONSE.get().apply(request);
            if (response.delayMillis() > 0) {
                try { Thread.sleep(response.delayMillis()); }
                catch (InterruptedException ignored) { Thread.currentThread().interrupt(); return; }
            }
            exchange.getResponseHeaders().set("Content-Type", response.contentType());
            exchange.getResponseHeaders().set("Location", "/must-not-follow-redirect");
            byte[] bytes = response.body().getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(response.status(), response.status() == 204 ? -1 : bytes.length);
            if (response.status() != 204) exchange.getResponseBody().write(bytes);
        }
    }
}
