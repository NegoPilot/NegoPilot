package com.negopilot.backend;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.net.InetSocketAddress;
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
import java.util.stream.Stream;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.MethodSource;
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

@org.springframework.test.context.ActiveProfiles("legacy-test")
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class AgentHttpIntegrationTest {
    private static final JsonMapper JSON = JsonMapper.builder().build();
    private static final String ID = "62751dc6-e6ad-41dc-a7fd-a11f79b5f3c2";
    private static final String TEST_KEY = "test-internal-key";
    private static final HttpServer UPSTREAM;
    private static final ExecutorService EXECUTOR = Executors.newCachedThreadPool();
    private static final AtomicReference<Function<JsonNode, StubResponse>> RESPONSE = new AtomicReference<>();
    private static final AtomicReference<JsonNode> LAST_REQUEST = new AtomicReference<>();
    private static final AtomicReference<String> LAST_KEY = new AtomicReference<>();
    private static final AtomicReference<String> LAST_PATH = new AtomicReference<>();
    private static final AtomicInteger CALLS = new AtomicInteger();
    private static final HttpClient CLIENT = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(2)).build();

    static {
        try {
            UPSTREAM = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            UPSTREAM.setExecutor(EXECUTOR);
            UPSTREAM.createContext("/", AgentHttpIntegrationTest::serve);
            UPSTREAM.start();
        } catch (IOException failure) {
            throw new ExceptionInInitializerError(failure);
        }
    }

    @LocalServerPort int port;
    @Autowired RestClient agentRestClient;
    @Autowired JsonMapper mapper;
    @Autowired AgentResponseValidator responseValidator;

    @DynamicPropertySource
    static void properties(DynamicPropertyRegistry registry) {
        registry.add("agent.base-url", () -> "http://127.0.0.1:" + UPSTREAM.getAddress().getPort());
        registry.add("agent.internal-api-key", () -> TEST_KEY);
        registry.add("agent.connect-timeout", () -> "250ms");
        registry.add("agent.response-timeout", () -> "250ms");
    }

    @BeforeEach
    void reset() {
        CALLS.set(0);
        LAST_REQUEST.set(null);
        LAST_KEY.set(null);
        LAST_PATH.set(null);
        RESPONSE.set(request -> StubResponse.json(200, completed(request)));
    }

    @AfterAll
    static void closeStub() {
        UPSTREAM.stop(0);
        EXECUTOR.shutdownNow();
        CLIENT.close();
    }

    @Test
    void healthDoesNotCallPython() throws Exception {
        var response = CLIENT.send(HttpRequest.newBuilder(uri("/health")).GET().build(), HttpResponse.BodyHandlers.ofString());
        assertThat(response.statusCode()).isEqualTo(200);
        assertThat(JSON.readTree(response.body())).isEqualTo(JSON.readTree("{\"status\":\"ok\"}"));
        assertThat(CALLS.get()).isZero();
    }

    @Test
    void generatesIdAndDefaultHistoryAndForwardsHeaderOnce() throws Exception {
        var response = post("{\"query\":\"하도급법 제4조 보여줘\"}");
        assertThat(response.statusCode()).isEqualTo(200);
        JsonNode body = JSON.readTree(response.body());
        assertThat(UUID.fromString(body.path("request_id").stringValue())).isNotNull();
        assertThat(body.path("request_id")).isEqualTo(LAST_REQUEST.get().path("request_id"));
        assertThat(LAST_REQUEST.get().path("history").isEmpty()).isTrue();
        assertThat(LAST_REQUEST.get().path("query").stringValue()).isEqualTo("하도급법 제4조 보여줘");
        assertThat(LAST_KEY.get()).isEqualTo(TEST_KEY);
        assertThat(LAST_PATH.get()).isEqualTo("POST /internal/v1/query-executions");
        assertThat(CALLS.get()).isEqualTo(1);
        assertThat(response.body()).doesNotContain(TEST_KEY);
    }

    @Test
    void preservesProvidedIdHistoryAndFullPartialResult() throws Exception {
        String request = """
                {"request_id":"%s","query":"follow up","history":[
                  {"role":"user","content":"before"},{"role":"assistant","content":"answer"}]}
                """.formatted(ID);
        var response = post(request);
        assertThat(response.statusCode()).isEqualTo(200);
        assertThat(LAST_REQUEST.get()).isEqualTo(JSON.readTree(request));
        assertThat(JSON.readTree(response.body())).isEqualTo(completed(LAST_REQUEST.get()));
    }

    @ParameterizedTest
    @ValueSource(strings = {"62751dc6e6ad41dca7fda11f79b5f3c2", "{62751dc6-e6ad-41dc-a7fd-a11f79b5f3c2}",
            "urn:uuid:62751dc6-e6ad-41dc-a7fd-a11f79b5f3c2", "62751DC6-E6AD-41DC-A7FD-A11F79B5F3C2"})
    void normalizesUuidTextFormsAcceptedByPython(String id) throws Exception {
        var response = post(JSON.writeValueAsString(JSON.createObjectNode().put("request_id", id).put("query", "q")));
        assertThat(response.statusCode()).isEqualTo(200);
        assertThat(LAST_REQUEST.get().path("request_id").stringValue()).isEqualTo(ID);
        assertThat(JSON.readTree(response.body()).path("request_id").stringValue()).isEqualTo(ID);
    }

    static Stream<String> invalidRequests() {
        return Stream.of("", "{", "null", "[]", "{}", "{\"query\":null}", "{\"query\":2}",
                "{\"query\":\"\"}", "{\"query\":\" \\t\\n\"}", "{\"query\":\"\\u00a0\\u0085\"}",
                "{\"query\":\"q\",\"request_id\":null}", "{\"query\":\"q\",\"request_id\":\"not-a-uuid\"}",
                "{\"query\":\"q\",\"request_id\":\"1-1-1-1-1\"}",
                "{\"query\":\"q\",\"requestId\":\"" + ID + "\"}",
                "{\"query\":\"q\",\"extra\":true}", "{\"query\":\"q\",\"history\":null}",
                "{\"query\":\"q\",\"history\":{}}", "{\"query\":\"q\",\"history\":[null]}",
                "{\"query\":\"q\",\"history\":[{\"role\":\"system\",\"content\":\"c\"}]}",
                "{\"query\":\"q\",\"history\":[{\"role\":\"user\",\"content\":\"c\",\"extra\":1}]}",
                "{\"query\":\"q\",\"history\":[{\"role\":\"user\",\"content\":\" \"}]}",
                "{\"query\":\"q\",\"history\":[{\"role\":\"user\"}]}",
                "{\"query\":\"q\",\"history\":[{\"role\":\"user\",\"content\":false}]}",
                "{\"query\":\"" + "q".repeat(4001) + "\"}",
                "{\"query\":\"q\",\"history\":[{\"role\":\"assistant\",\"content\":\"" + "c".repeat(4001) + "\"}]}",
                "{\"query\":\"q\",\"history\":[" + String.join(",", java.util.Collections.nCopies(21,
                        "{\"role\":\"user\",\"content\":\"c\"}")) + "]}",
                "{\"query\":\"q\",\"query\":\"duplicate\"}", "{\"query\":\"q\"} {}");
    }

    @ParameterizedTest
    @MethodSource("invalidRequests")
    void rejectsInvalidInputBeforeCallingPython(String request) throws Exception {
        assertError(post(request), 400, "VALIDATION_ERROR", false);
        assertThat(CALLS.get()).isZero();
    }

    @Test
    void validationErrorKeepsParseableId() throws Exception {
        var response = post("{\"request_id\":\"" + ID + "\",\"query\":\" \"}");
        assertError(response, 400, "VALIDATION_ERROR", false);
        assertThat(JSON.readTree(response.body()).path("request_id").stringValue()).isEqualTo(ID);
    }

    @Test
    void unicodeLengthAndTwentyHistoryItemsAreAccepted() throws Exception {
        ObjectNode request = JSON.createObjectNode().put("query", "😀".repeat(4000));
        var history = request.putArray("history");
        for (int index = 0; index < 20; index++) history.addObject().put("role", "user").put("content", "😀".repeat(4000));
        assertThat(post(JSON.writeValueAsString(request)).statusCode()).isEqualTo(200);
        assertThat(CALLS.get()).isEqualTo(1);
    }

    @ParameterizedTest
    @ValueSource(strings = {"clarify", "failed", "missing_args", "error"})
    void mapsAllExecutionStatuses(String kind) throws Exception {
        RESPONSE.set(request -> StubResponse.json(200, execution(request, kind)));
        var response = post("{\"query\":\"q\"}");
        assertThat(response.statusCode()).isEqualTo(200);
        assertThat(JSON.readTree(response.body()).path("execution_status").stringValue())
                .isEqualTo(kind.equals("failed") || kind.equals("error") ? "FAILED" : "NEEDS_INPUT");
        assertThat(JSON.readTree(response.body())).isEqualTo(execution(LAST_REQUEST.get(), kind));
    }

    @Test
    void referencedArticleFailurePreservesCompletedPrimaryResult() throws Exception {
        RESPONSE.set(request -> {
            ObjectNode body = completed(request);
            ObjectNode subQuery = (ObjectNode) body.at("/decomposition/sub_queries/0");
            subQuery.put("intent", "supplier_ranking").put("grounding", "required");
            subQuery.putObject("entities").put("rfq_id", "RFQ-2026-001");
            ObjectNode outcome = (ObjectNode) body.at("/routing/outcomes/0");
            outcome.put("intent", "supplier_ranking").put("status", "ok");
            outcome.putArray("calls").add(JSON.readTree("""
                    {"server":"business-mcp","tool":"rank_suppliers","args":{"rfq_id":"RFQ-2026-001"},
                     "result":{"ranked":[{"supplier_id":"SUP-0034","total_score":91}]},"error":null}
                    """));
            outcome.putArray("referenced_articles").add(JSON.readTree("""
                    {"server":"law-mcp","tool":"get_article","args":{"law":"subcontract_act","article_no":"제13조"},
                     "result":null,"error":"Tool execution failed."}
                    """));
            return StubResponse.json(200, body);
        });
        var response = post("{\"query\":\"q\"}");
        assertThat(response.statusCode()).isEqualTo(200);
        JsonNode body = JSON.readTree(response.body());
        assertThat(body.path("execution_status").stringValue()).isEqualTo("COMPLETED");
        assertThat(body.path("error").isNull()).isTrue();
        assertThat(body.at("/routing/outcomes/0/status").stringValue()).isEqualTo("ok");
        assertThat(body.at("/routing/outcomes/0/calls/0/result/ranked/0/total_score").intValue()).isEqualTo(91);
        assertThat(body.at("/routing/outcomes/0/referenced_articles/0/error").stringValue()).isEqualTo("Tool execution failed.");
        assertThat(CALLS.get()).isEqualTo(1);
    }

    @ParameterizedTest
    @ValueSource(booleans = {false, true})
    void sanitizesRoutingDiagnosticsWithoutChangingResultJsonOrReferences(boolean emptyDiagnostics) throws Exception {
        String sensitive = "Example SDK stack trace: Authorization=demo-private-token";
        String diagnostic = emptyDiagnostics ? "" : sensitive;
        AtomicReference<ObjectNode> upstreamBody = new AtomicReference<>();
        RESPONSE.set(request -> {
            ObjectNode body = execution(request, "error");
            ObjectNode first = (ObjectNode) body.at("/routing/outcomes/0");
            first.put("message", diagnostic);
            ObjectNode primaryCall = (ObjectNode) first.at("/calls/0");
            primaryCall.put("error", diagnostic);
            primaryCall.set("result", JSON.createObjectNode().put("unchanged_text", sensitive)
                    .set("law_refs", JSON.readTree("[{\"law\":\"subcontract_act\",\"article_no\":\"제13조\"}]")));
            ObjectNode successfulCall = primaryCall.deepCopy().putNull("error");
            successfulCall.set("result", JSON.readTree("[1,null,true,{\"supplier_id\":\"SUP-0034\"}]"));
            ((ArrayNode) first.get("calls")).add(successfulCall);
            ObjectNode reference = (ObjectNode) JSON.readTree("""
                    {"server":"law-mcp","tool":"get_article","args":{"law":"subcontract_act","article_no":"제13조"},
                     "result":null,"error":null}
                    """);
            reference.put("error", diagnostic);
            reference.set("result", JSON.createObjectNode().put("citation", "제13조").put("unchanged_text", sensitive));
            first.putArray("referenced_articles").add(reference).add(reference.deepCopy().putNull("error"));
            ObjectNode secondQuery = ((ObjectNode) body.at("/decomposition/sub_queries/0")).deepCopy().put("id", "q2");
            ((ArrayNode) body.at("/decomposition/sub_queries")).add(secondQuery);
            ObjectNode emptyOutcome = first.deepCopy().put("id", "q2").put("status", "empty").putNull("message");
            emptyOutcome.putArray("calls");
            emptyOutcome.putArray("referenced_articles");
            ((ArrayNode) body.at("/routing/outcomes")).add(emptyOutcome);
            ((ObjectNode) body.get("routing")).put("halted", true).put("halt_reason", diagnostic);
            ((ObjectNode) body.get("error")).put("message", sensitive);
            upstreamBody.set(body.deepCopy());
            return StubResponse.json(200, body);
        });

        var response = post("{\"query\":\"q\"}");
        assertThat(response.statusCode()).isEqualTo(200);
        JsonNode actual = JSON.readTree(response.body());
        ObjectNode expected = upstreamBody.get().deepCopy();
        ((ObjectNode) expected.at("/routing/outcomes/0/calls/0")).put("error", "Tool execution failed.");
        ((ObjectNode) expected.at("/routing/outcomes/0/referenced_articles/0")).put("error", "Tool execution failed.");
        ((ObjectNode) expected.at("/routing/outcomes/0")).put("message", "Routing details are unavailable.");
        ((ObjectNode) expected.get("routing")).put("halt_reason", "Routing halted because a dependency returned no results.");
        ((ObjectNode) expected.get("error")).put("message", "Query routing failed.");
        assertThat(actual).isEqualTo(expected);
        assertThat(actual.at("/routing/outcomes/0/calls/0/result")).isEqualTo(upstreamBody.get().at("/routing/outcomes/0/calls/0/result"));
        assertThat(actual.at("/routing/outcomes/0/referenced_articles/0/result"))
                .isEqualTo(upstreamBody.get().at("/routing/outcomes/0/referenced_articles/0/result"));
        assertThat(actual.at("/routing/outcomes/0/calls/0/result/unchanged_text").stringValue()).isEqualTo(sensitive);
        assertThat(actual.at("/routing/outcomes/0/referenced_articles/0/result/unchanged_text").stringValue()).isEqualTo(sensitive);
        assertThat(actual.at("/routing/outcomes/0/calls/1/error").isNull()).isTrue();
        assertThat(actual.at("/routing/outcomes/0/referenced_articles/1/error").isNull()).isTrue();
        assertThat(actual.at("/routing/outcomes/1/message").isNull()).isTrue();
        assertThat(actual.at("/routing/outcomes/0/message").stringValue()).doesNotContain(sensitive);
        assertThat(actual.at("/routing/halt_reason").stringValue()).doesNotContain(sensitive);
        assertThat(actual.at("/error/message").stringValue()).doesNotContain(sensitive);
        assertThat(CALLS.get()).isEqualTo(1);
    }

    @Test
    void sanitizesDecompositionFailureMessageAndPreservesNotRoutedNulls() throws Exception {
        String sensitive = "Example provider stack trace: api_key=demo-private-token";
        RESPONSE.set(request -> {
            ObjectNode body = execution(request, "failed");
            ((ObjectNode) body.get("error")).put("message", sensitive);
            return StubResponse.json(200, body);
        });
        var response = post("{\"query\":\"q\"}");
        assertThat(response.statusCode()).isEqualTo(200);
        assertThat(JSON.readTree(response.body())).isEqualTo(execution(LAST_REQUEST.get(), "failed"));
        assertThat(response.body()).doesNotContain(sensitive);
        assertThat(CALLS.get()).isEqualTo(1);
    }

    @ParameterizedTest
    @ValueSource(strings = {"wrong-id", "missing-field", "bad-state", "bad-generation", "bad-schema", "bad-mode", "null-routing"})
    void rejectsInvalidSuccessEnvelope(String kind) throws Exception {
        RESPONSE.set(request -> {
            ObjectNode body = completed(request);
            switch (kind) {
                case "wrong-id" -> body.put("request_id", UUID.randomUUID().toString());
                case "missing-field" -> body.remove("decomposition");
                case "bad-state" -> body.put("execution_status", "NEEDS_INPUT");
                case "bad-generation" -> body.put("generation_status", "COMPLETED");
                case "bad-schema" -> body.put("schema_version", "0.2");
                case "bad-mode" -> body.put("mode", "production");
                default -> body.putNull("routing");
            }
            return StubResponse.json(200, body);
        });
        assertError(post("{\"query\":\"q\"}"), 502, "UPSTREAM_INVALID_RESPONSE", false);
        assertThat(CALLS.get()).isEqualTo(1);
    }

    @ParameterizedTest
    @ValueSource(strings = {"{", "null", "[]", "{}", "{} {}", "{\"x\":1,\"x\":2}"})
    void rejectsMalformedJson(String body) throws Exception {
        RESPONSE.set(request -> new StubResponse(200, body, "application/json", 0));
        assertError(post("{\"query\":\"q\"}"), 502, "UPSTREAM_INVALID_RESPONSE", false);
    }

    @ParameterizedTest
    @ValueSource(ints = {201, 204, 301, 307, 401, 403, 404, 429, 500})
    void unknownOrAuthenticationStatusIsSanitizedWithoutRetry(int status) throws Exception {
        RESPONSE.set(request -> new StubResponse(status, "private detail " + TEST_KEY, "text/plain", 0));
        var response = post("{\"query\":\"q\"}");
        assertError(response, 502, "UPSTREAM_INVALID_RESPONSE", false);
        assertThat(CALLS.get()).isEqualTo(1);
        assertThat(response.body()).doesNotContain(TEST_KEY, "private detail");
    }

    @ParameterizedTest
    @ValueSource(ints = {400, 503, 504})
    void preservesKnownHttpErrorsWithSafeMessages(int status) throws Exception {
        RESPONSE.set(request -> StubResponse.json(status, httpError(request, status)));
        var response = post("{\"query\":\"q\"}");
        assertError(response, status, errorCode(status), status != 400);
        assertThat(JSON.readTree(response.body()).path("request_id")).isEqualTo(LAST_REQUEST.get().path("request_id"));
        assertThat(response.body()).doesNotContain("sensitive upstream detail");
        assertThat(CALLS.get()).isEqualTo(1);
    }

    @ParameterizedTest
    @ValueSource(strings = {"wrong-id", "wrong-code", "object-code", "array-code", "null-code",
            "wrong-retryable", "missing-message", "extra"})
    void rejectsMalformedHttpError(String kind) throws Exception {
        RESPONSE.set(request -> {
            ObjectNode body = httpError(request, 503);
            ObjectNode error = (ObjectNode) body.get("error");
            switch (kind) {
                case "wrong-id" -> body.put("request_id", UUID.randomUUID().toString());
                case "wrong-code" -> error.put("code", "INTERNAL_ERROR");
                case "object-code" -> error.putObject("code");
                case "array-code" -> error.putArray("code");
                case "null-code" -> error.putNull("code");
                case "wrong-retryable" -> error.put("retryable", false);
                case "missing-message" -> error.remove("message");
                default -> body.put("debug", "secret");
            }
            return StubResponse.json(503, body);
        });
        assertError(post("{\"query\":\"q\"}"), 502, "UPSTREAM_INVALID_RESPONSE", false);
    }

    @Test
    void validationErrorWithNullIdUsesGatewayId() throws Exception {
        RESPONSE.set(request -> StubResponse.json(400, httpError(request, 400).putNull("request_id")));
        var response = post("{\"query\":\"q\"}");
        assertError(response, 400, "VALIDATION_ERROR", false);
        assertThat(JSON.readTree(response.body()).path("request_id")).isEqualTo(LAST_REQUEST.get().path("request_id"));
    }

    @Test
    void wrongResponseContentTypeIsGatewayError() throws Exception {
        RESPONSE.set(request -> new StubResponse(200, JSON.writeValueAsString(completed(request)), "text/html", 0));
        assertError(post("{\"query\":\"q\"}"), 502, "UPSTREAM_INVALID_RESPONSE", false);
    }

    @Test
    void responseTimeoutDoesNotRetry() throws Exception {
        RESPONSE.set(request -> new StubResponse(200, JSON.writeValueAsString(completed(request)), "application/json", 900));
        assertError(post("{\"query\":\"q\"}"), 504, "UPSTREAM_TIMEOUT", true);
        assertThat(CALLS.get()).isEqualTo(1);
    }

    @Test
    void missingKeyDisablesExecutionWithoutCallingPython() {
        var properties = new AgentProperties(URI.create("http://127.0.0.1:" + UPSTREAM.getAddress().getPort()),
                "", Duration.ofMillis(250), Duration.ofMillis(250));
        AgentGateway gateway = new AgentGateway(agentRestClient, properties, mapper, responseValidator);
        assertThatThrownBy(() -> gateway.execute(validRequest()))
                .isInstanceOfSatisfying(ApiException.class, exception -> {
                    assertThat(exception.status.value()).isEqualTo(503);
                    assertThat(exception.code).isEqualTo("UPSTREAM_UNAVAILABLE");
                });
        assertThat(CALLS.get()).isZero();
    }

    @Test
    void connectionRefusedIsUnavailable() throws Exception {
        int closedPort;
        try (var socket = new java.net.ServerSocket(0, 0, java.net.InetAddress.getLoopbackAddress())) {
            closedPort = socket.getLocalPort();
        }
        var properties = new AgentProperties(URI.create("http://127.0.0.1:" + closedPort), TEST_KEY,
                Duration.ofMillis(250), Duration.ofMillis(250));
        AgentGateway gateway = new AgentGateway(agentRestClient, properties, mapper, responseValidator);
        assertThatThrownBy(() -> gateway.execute(validRequest()))
                .isInstanceOfSatisfying(ApiException.class, exception -> {
                    assertThat(exception.status.value()).isEqualTo(503);
                    assertThat(exception.retryable).isTrue();
                });
    }

    private QueryRequestValidator.ValidatedRequest validRequest() {
        return new QueryRequestValidator().validate(JSON.readTree("{\"query\":\"q\"}"));
    }

    private URI uri(String path) { return URI.create("http://127.0.0.1:" + port + path); }

    private HttpResponse<String> post(String body) throws Exception {
        return CLIENT.send(HttpRequest.newBuilder(uri("/api/v1/agent/queries"))
                .timeout(Duration.ofSeconds(5)).header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(body)).build(), HttpResponse.BodyHandlers.ofString());
    }

    private static void assertError(HttpResponse<String> response, int status, String code, boolean retryable) {
        assertThat(response.statusCode()).isEqualTo(status);
        JsonNode body = JSON.readTree(response.body());
        assertThat(body.size()).isEqualTo(2);
        assertThat(body.has("request_id")).isTrue();
        assertThat(body.path("error").path("code").stringValue()).isEqualTo(code);
        assertThat(body.path("error").path("retryable").booleanValue()).isEqualTo(retryable);
    }

    private static ObjectNode completed(JsonNode request) {
        ObjectNode body = (ObjectNode) JSON.readTree("""
                {"schema_version":"0.1","request_id":"placeholder","execution_status":"COMPLETED",
                 "generation_status":"NOT_IMPLEMENTED","mode":"stub","error":null,
                 "decomposition":{"schema_version":"0.1","status":"ok","original_query":"q",
                   "sub_queries":[{"id":"q1","question":"q","original_text":"q","intent":"general_knowledge",
                     "grounding":"none","confidence":0.95,"entities":{},"inputs":[],"depends_on":[]}],
                   "clarification":null,"assumptions":[]},
                 "routing":{"schema_version":"0.1","status":"routed","outcomes":[{"id":"q1","intent":"general_knowledge",
                   "status":"general_knowledge","calls":[],"referenced_articles":[],"missing":[],"message":null}],"halted":false,"halt_reason":null}}
                """);
        body.set("request_id", request.get("request_id"));
        return body;
    }

    private static ObjectNode execution(JsonNode request, String kind) {
        ObjectNode body = completed(request);
        ObjectNode decomposition = (ObjectNode) body.get("decomposition");
        ObjectNode routing = (ObjectNode) body.get("routing");
        if (kind.equals("clarify") || kind.equals("failed")) {
            decomposition.put("status", kind).putArray("sub_queries");
            routing.put("status", "not_routed").putArray("outcomes");
            if (kind.equals("clarify")) {
                decomposition.set("clarification", JSON.readTree("{\"reason\":\"ambiguous\",\"question\":\"which?\",\"options\":[\"a\",\"b\"]}"));
            }
        } else {
            ObjectNode outcome = (ObjectNode) routing.path("outcomes").get(0);
            outcome.put("status", kind);
            if (kind.equals("missing_args")) outcome.putArray("missing").add("supplier");
            if (kind.equals("error")) outcome.putArray("calls").add(JSON.readTree("""
                    {"server":"business-mcp","tool":"get_supplier","args":{"supplier":"SUP-0034"},
                     "result":null,"error":"Tool execution failed."}
                    """));
        }
        if (kind.equals("failed") || kind.equals("error")) {
            body.put("execution_status", "FAILED");
            body.putObject("error").put("code", kind.equals("failed") ? "DECOMPOSITION_FAILED" : "ROUTING_FAILED")
                    .put("message", kind.equals("failed") ? "Query decomposition failed." : "Query routing failed.")
                    .put("retryable", false);
        } else body.put("execution_status", "NEEDS_INPUT");
        return body;
    }

    private static ObjectNode httpError(JsonNode request, int status) {
        ObjectNode body = JSON.createObjectNode();
        body.set("request_id", request.get("request_id"));
        body.putObject("error").put("code", errorCode(status)).put("message", "sensitive upstream detail")
                .put("retryable", status != 400);
        return body;
    }

    private static String errorCode(int status) {
        return switch (status) {
            case 400 -> "VALIDATION_ERROR";
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
            JsonNode request = JSON.readTree(exchange.getRequestBody().readAllBytes());
            LAST_REQUEST.set(request);
            StubResponse response = RESPONSE.get().apply(request);
            if (response.delayMillis() > 0) {
                try { Thread.sleep(response.delayMillis()); }
                catch (InterruptedException ignored) { Thread.currentThread().interrupt(); return; }
            }
            exchange.getResponseHeaders().set("Content-Type", response.contentType());
            exchange.getResponseHeaders().set("Location", "/redirect-must-not-be-followed");
            byte[] bytes = response.body().getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(response.status(), response.status() == 204 ? -1 : bytes.length);
            if (response.status() != 204) exchange.getResponseBody().write(bytes);
        }
    }
}
