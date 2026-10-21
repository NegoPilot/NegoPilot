package com.negopilot.backend;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.URLDecoder;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.Function;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

import static org.assertj.core.api.Assertions.assertThat;

@org.springframework.test.context.ActiveProfiles("legacy-test")
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class SupplierChatHttpIntegrationTest {
    private static final JsonMapper JSON = JsonMapper.builder().build();
    private static final JsonNode FIXTURE;
    private static final String SERVER_KEY = "supplier-chat-server-test-key";
    private static final String QUERY = "이 회사에 대해 알려줘";
    private static final HttpServer UPSTREAM;
    private static final ExecutorService EXECUTOR = Executors.newCachedThreadPool();
    private static final HttpClient CLIENT = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(2)).build();
    private static final List<StubRequest> REQUESTS = new CopyOnWriteArrayList<>();
    private static final AtomicReference<Function<StubRequest, StubResponse>> CHAT = new AtomicReference<>();
    private static final AtomicReference<Function<StubRequest, StubResponse>> LOOKUP = new AtomicReference<>();

    static {
        try (var source = SupplierChatHttpIntegrationTest.class.getResourceAsStream("/fixtures/supplier-chat.json")) {
            if (source == null) throw new IllegalStateException("Missing shared supplier fixture");
            FIXTURE = JSON.readTree(source);
            UPSTREAM = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            UPSTREAM.setExecutor(EXECUTOR);
            UPSTREAM.createContext("/", SupplierChatHttpIntegrationTest::serve);
            UPSTREAM.start();
        } catch (IOException failure) {
            throw new ExceptionInInitializerError(failure);
        }
    }

    @LocalServerPort int port;

    @DynamicPropertySource
    static void properties(DynamicPropertyRegistry registry) {
        registry.add("agent.base-url", () -> "http://127.0.0.1:" + UPSTREAM.getAddress().getPort());
        registry.add("agent.internal-api-key", () -> SERVER_KEY);
        registry.add("agent.connect-timeout", () -> "500ms");
        registry.add("agent.response-timeout", () -> "2s");
        registry.add("agent.execution-mode", () -> "stub");
    }

    @BeforeEach
    void reset() {
        REQUESTS.clear();
        CHAT.set(request -> StubResponse.ok(chatResponse(request.body())));
        LOOKUP.set(SupplierChatHttpIntegrationTest::lookupResponse);
    }

    @AfterAll
    static void close() {
        UPSTREAM.stop(0);
        EXECUTOR.shutdownNow();
        CLIENT.close();
    }

    @Test
    void proxiesCatalogSearchAndDetailUsingOnlyServerKey() throws Exception {
        var catalog = get("/api/v1/business/catalog");
        assertThat(catalog.statusCode()).isEqualTo(200);
        assertThat(body(catalog)).isEqualTo(envelope(catalog()));

        var search = get("/api/v1/suppliers?process=" + encode(" 다이캐스팅 ") + "&material=ADC12&limit=1&iatf_required=true");
        assertThat(search.statusCode()).isEqualTo(200);
        JsonNode data = body(search).path("data");
        assertThat(data.path("returned_count").intValue()).isEqualTo(1);
        assertThat(data.path("limit").intValue()).isEqualTo(1);
        assertThat(data.path("items").get(0).path("supplier_id").stringValue()).isEqualTo("SUP-0034");
        assertThat(data.path("items").get(0).path("matched_capabilities").size()).isEqualTo(1);
        assertThat(REQUESTS.get(1).parameters().get("process")).isEqualTo("다이캐스팅");

        var detail = get("/api/v1/suppliers/SUP-0052");
        assertThat(detail.statusCode()).isEqualTo(200);
        assertThat(body(detail).path("data")).isEqualTo(supplier("SUP-0052"));
        assertThat(body(detail).path("data").path("quality").path("defect_ppm").isNull()).isTrue();
        assertThat(REQUESTS).allSatisfy(request -> assertThat(request.key()).isEqualTo(SERVER_KEY));
        assertThat(catalog.body() + search.body() + detail.body()).doesNotContain(SERVER_KEY, "browser-supplied-key");
    }

    @Test
    void searchGroupsCapabilitiesAndReturnsEmptyForUnknownValidFilter() throws Exception {
        var all = get("/api/v1/suppliers?process=" + encode("다이캐스팅"));
        assertThat(all.statusCode()).isEqualTo(200);
        assertThat(body(all).path("data").path("items").size()).isEqualTo(2);
        assertThat(body(all).path("data").path("items").get(0).path("matched_capabilities").size()).isEqualTo(2);
        var empty = get("/api/v1/suppliers?process=" + encode("존재하지않는공정"));
        assertThat(empty.statusCode()).isEqualTo(200);
        assertThat(body(empty).path("data").path("returned_count").intValue()).isZero();
        var noncertified = get("/api/v1/suppliers?process=" + encode("프레스") + "&iatf_required=false");
        assertThat(body(noncertified).path("data").path("items").size()).isEqualTo(1);
        var iso = get("/api/v1/suppliers?process=" + encode("다이캐스팅") + "&iso14001_required=true");
        assertThat(iso.statusCode()).isEqualTo(200);
        assertThat(body(iso).path("data").path("items").size()).isEqualTo(1);
        assertThat(body(iso).path("data").path("items").get(0).path("supplier_id").stringValue()).isEqualTo("SUP-0034");
        assertThat(REQUESTS.getLast().parameters().get("iso14001_required")).isEqualTo("true");
    }

    @Test
    void searchFiltersByMinimumMonthlyCapacity() throws Exception {
        var response = get("/api/v1/suppliers?process=" + encode("다이캐스팅") + "&min_monthly_capacity=50000");
        assertThat(response.statusCode()).as(response.body()).isEqualTo(200);
        JsonNode items = body(response).path("data").path("items");
        assertThat(items.size()).isEqualTo(1);
        assertThat(items.get(0).path("supplier_id").stringValue()).isEqualTo("SUP-0034");
        assertThat(items.get(0).path("matched_capabilities").size()).isEqualTo(1);
        assertThat(REQUESTS.get(0).parameters().get("min_monthly_capacity")).isEqualTo("50000");
    }

    @Test
    void preservesLiteralSpecialCharactersInSearchParameter() throws Exception {
        String process = "A+B% &?";
        var response = get("/api/v1/suppliers?process=" + encode(process));
        assertThat(response.statusCode()).as(response.body()).isEqualTo(200);
        assertThat(REQUESTS).hasSize(1);
        assertThat(REQUESTS.get(0).parameters().get("process")).isEqualTo(process);
        assertThat(body(response).path("data").path("items").size()).isZero();
        assertThat(body(response).path("data").path("returned_count").intValue()).isZero();
    }

    @ParameterizedTest
    @ValueSource(strings = {"", "?process=", "?process=x&process=y", "?process=x&extra=1", "?process=x&limit=0",
            "?process=x&limit=101", "?process=x&iatf_required=1", "?process=x&region=",
            "?process=x&min_monthly_capacity=0", "?process=x&min_monthly_capacity=abc"})
    void invalidSearchIsRejectedWithoutCallingPython(String query) throws Exception {
        assertError(get("/api/v1/suppliers" + query), 400, "VALIDATION_ERROR", false);
        assertThat(REQUESTS).isEmpty();
    }

    @Test
    void createsReadsAndUpdatesConversationWithValidatedSupplierContext() throws Exception {
        JsonNode created = create("SUP-0034");
        String id = created.path("conversation_id").stringValue();
        assertThat(UUID.fromString(id).toString()).isEqualTo(id);
        assertThat(created.path("schema_version").stringValue()).isEqualTo("0.2");
        assertThat(created.path("mode").stringValue()).isEqualTo("stub");
        assertThat(created.path("storage").stringValue()).isEqualTo("memory");
        assertThat(created.path("messages").size()).isZero();
        assertThat(REQUESTS.get(0).path()).isEqualTo("/internal/v1/suppliers/SUP-0034");
        assertThat(body(get("/api/v1/conversations/" + id))).isEqualTo(created);

        var first = message(id, "  " + QUERY + "  ", null);
        assertThat(first.statusCode()).isEqualTo(200);
        JsonNode sent = chatRequests().get(0).body();
        assertThat(sent.path("query").stringValue()).isEqualTo(QUERY);
        assertThat(sent.path("context")).isEqualTo(context("SUP-0034"));
        assertThat(sent.path("history").size()).isZero();
        assertThat(UUID.fromString(sent.path("request_id").stringValue())).isNotNull();
        assertThat(sent.path("conversation_id").stringValue()).isEqualTo(id);
        assertStoredPair(body(get("/api/v1/conversations/" + id)), QUERY, body(first));

        var second = message(id, "납기와 품질은 어때?", context("SUP-0041"));
        assertThat(second.statusCode()).isEqualTo(200);
        JsonNode secondSent = chatRequests().get(1).body();
        assertThat(secondSent.path("history").size()).isEqualTo(2);
        assertThat(secondSent.path("history").get(0).path("content").stringValue()).isEqualTo(QUERY);
        assertThat(secondSent.path("history").get(1).path("role").stringValue()).isEqualTo("assistant");
        assertThat(secondSent.path("context")).isEqualTo(context("SUP-0041"));
        assertThat(secondSent.path("request_id")).isNotEqualTo(sent.path("request_id"));
        JsonNode snapshot = body(get("/api/v1/conversations/" + id));
        assertThat(snapshot.path("context")).isEqualTo(context("SUP-0041"));
        assertThat(snapshot.path("messages").size()).isEqualTo(4);
    }

    @Test
    void needsInputIsStoredWithoutInventingSelectedSupplier() throws Exception {
        String id = create(null).path("conversation_id").stringValue();
        var result = message(id, QUERY, null);
        assertThat(result.statusCode()).isEqualTo(200);
        JsonNode response = body(result);
        assertThat(response.path("execution_status").stringValue()).isEqualTo("NEEDS_INPUT");
        assertThat(response.path("clarification").path("missing_fields").get(0).stringValue())
                .isEqualTo("context.selected_supplier_ids");
        assertThat(response.path("resolved_context")).isEqualTo(context(null));
        assertStoredPair(body(get("/api/v1/conversations/" + id)), QUERY, response);
    }

    @Test
    void sendsOnlyLatestTwentyPriorMessagesAndStoresAtMostOneHundred() throws Exception {
        String id = create("SUP-0034").path("conversation_id").stringValue();
        Set<String> requestIds = new LinkedHashSet<>();
        for (int turn = 0; turn < 51; turn++) {
            String query = "unsupported question " + turn;
            var result = message(id, query, null);
            assertThat(result.statusCode()).isEqualTo(200);
            JsonNode sent = chatRequests().get(turn).body();
            assertThat(sent.path("history").size()).isEqualTo(Math.min(turn * 2, 20));
            for (JsonNode entry : sent.path("history")) {
                assertThat(entry.size()).isEqualTo(2);
                assertThat(entry.path("content").stringValue()).isNotEqualTo(query);
            }
            requestIds.add(sent.path("request_id").stringValue());
        }
        assertThat(requestIds).hasSize(51);
        JsonNode lastHistory = chatRequests().get(50).body().path("history");
        assertThat(lastHistory.get(0).path("content").stringValue()).isEqualTo("unsupported question 40");
        JsonNode messages = body(get("/api/v1/conversations/" + id)).path("messages");
        assertThat(messages.size()).isEqualTo(100);
        assertThat(messages.get(0).path("content").stringValue()).isEqualTo("unsupported question 1");
        assertThat(messages.get(98).path("content").stringValue()).isEqualTo("unsupported question 50");
        for (int index = 0; index < messages.size(); index += 2) {
            assertThat(messages.get(index).path("role").stringValue()).isEqualTo("user");
            assertThat(messages.get(index + 1).path("role").stringValue()).isEqualTo("assistant");
            assertThat(messages.get(index + 1).path("message_id"))
                    .isEqualTo(messages.get(index + 1).path("response").path("response_id"));
        }
    }

    @Test
    void serializesOneConversationWhileAnotherConversationContinues() throws Exception {
        String firstId = create("SUP-0034").path("conversation_id").stringValue();
        String otherId = create("SUP-0041").path("conversation_id").stringValue();
        CountDownLatch entered = new CountDownLatch(1);
        CountDownLatch release = new CountDownLatch(1);
        AtomicInteger firstConversationCalls = new AtomicInteger();
        CHAT.set(request -> {
            if (request.body().path("conversation_id").stringValue().equals(firstId)
                    && firstConversationCalls.incrementAndGet() == 1) {
                entered.countDown();
                try {
                    if (!release.await(2, TimeUnit.SECONDS)) throw new IllegalStateException("Test release timed out");
                } catch (InterruptedException failure) {
                    Thread.currentThread().interrupt();
                    throw new IllegalStateException(failure);
                }
            }
            return StubResponse.ok(chatResponse(request.body()));
        });
        CompletableFuture<HttpResponse<String>> first = postAsync(messagesPath(firstId), messageBody(QUERY, null));
        assertThat(entered.await(1, TimeUnit.SECONDS)).isTrue();
        CompletableFuture<HttpResponse<String>> second = postAsync(messagesPath(firstId), messageBody("납기와 품질은 어때?", null));
        CompletableFuture<HttpResponse<String>> independent = postAsync(messagesPath(otherId), messageBody(QUERY, null));
        try {
            assertThat(independent.get(1, TimeUnit.SECONDS).statusCode()).isEqualTo(200);
            assertThat(firstConversationCalls.get()).isEqualTo(1);
        } finally {
            release.countDown();
        }
        assertThat(first.get(2, TimeUnit.SECONDS).statusCode()).isEqualTo(200);
        assertThat(second.get(2, TimeUnit.SECONDS).statusCode()).isEqualTo(200);
        List<StubRequest> sameConversation = chatRequests().stream()
                .filter(request -> request.body().path("conversation_id").stringValue().equals(firstId)).toList();
        assertThat(sameConversation).hasSize(2);
        assertThat(sameConversation.get(1).body().path("history").size()).isEqualTo(2);
        assertThat(body(get("/api/v1/conversations/" + firstId)).path("messages").size()).isEqualTo(4);
        assertThat(body(get("/api/v1/conversations/" + otherId)).path("messages").size()).isEqualTo(2);
    }

    @ParameterizedTest
    @ValueSource(ints = {400, 401, 404, 503, 504, 500})
    void upstreamHttpFailureDoesNotStoreMessagesOrChangedContext(int status) throws Exception {
        String id = create("SUP-0034").path("conversation_id").stringValue();
        assertThat(message(id, QUERY, null).statusCode()).isEqualTo(200);
        JsonNode before = body(get("/api/v1/conversations/" + id));
        int callsBefore = chatRequests().size();
        String code = switch (status) {
            case 400 -> "VALIDATION_ERROR";
            case 404 -> "SUPPLIER_NOT_FOUND";
            case 503 -> "DEMO_MODE_REQUIRED";
            case 504 -> "UPSTREAM_TIMEOUT";
            case 401 -> "UNAUTHORIZED";
            default -> "INTERNAL_ERROR";
        };
        CHAT.set(request -> error(status, request.body().path("request_id"), code, status == 504));
        int expectedStatus = status == 401 || status == 500 ? 502 : status;
        var result = message(id, QUERY, context("SUP-0041"));
        assertError(result, expectedStatus, expectedStatus == 502 ? "UPSTREAM_INVALID_RESPONSE" : code, status == 504);
        assertThat(result.body()).doesNotContain("sensitive private upstream detail");
        assertThat(body(get("/api/v1/conversations/" + id))).isEqualTo(before);
        assertThat(chatRequests()).hasSize(callsBefore + 1);
    }

    @ParameterizedTest
    @ValueSource(strings = {"request-id", "conversation-id", "response-id", "context", "status", "generation", "evidence-link",
            "evidence-supplier", "executed", "fixture-value", "clarification", "extra", "json"})
    void invalidChatResponseIs502AndPreservesEntireConversation(String kind) throws Exception {
        String id = create("SUP-0034").path("conversation_id").stringValue();
        JsonNode before = body(get("/api/v1/conversations/" + id));
        CHAT.set(request -> {
            ObjectNode result = chatResponse(request.body());
            ObjectNode evidence = (ObjectNode) result.path("evidence").get(0);
            ObjectNode card = (ObjectNode) result.path("presentation").path("cards").get(0);
            switch (kind) {
                case "request-id" -> result.put("request_id", UUID.randomUUID().toString());
                case "conversation-id" -> result.put("conversation_id", UUID.randomUUID().toString());
                case "response-id" -> result.put("response_id", "invalid");
                case "context" -> result.set("resolved_context", context("SUP-0034"));
                case "status" -> result.put("execution_status", "NEEDS_INPUT");
                case "generation" -> result.put("generation_status", "COMPLETED");
                case "evidence-link" -> card.putArray("evidence_ids").add("missing");
                case "evidence-supplier" -> evidence.put("supplier_id", "SUP-0034");
                case "executed" -> evidence.put("executed", true);
                case "fixture-value" -> ((ObjectNode) card.path("supplier").path("quality")).put("defect_ppm", 1);
                case "clarification" -> result.putObject("clarification");
                case "extra" -> result.put("debug", "private");
                default -> { return new StubResponse(200, "{", "application/json"); }
            }
            return StubResponse.ok(result);
        });
        assertError(message(id, QUERY, context("SUP-0041")), 502, "UPSTREAM_INVALID_RESPONSE", false);
        assertThat(body(get("/api/v1/conversations/" + id))).isEqualTo(before);
    }

    @Test
    void actualResponseTimeoutDoesNotStoreMessagesOrContext() throws Exception {
        String id = create("SUP-0034").path("conversation_id").stringValue();
        JsonNode before = body(get("/api/v1/conversations/" + id));
        CHAT.set(request -> {
            try { Thread.sleep(2300); }
            catch (InterruptedException failure) { Thread.currentThread().interrupt(); }
            return StubResponse.ok(chatResponse(request.body()));
        });
        assertError(message(id, QUERY, context("SUP-0041")), 504, "UPSTREAM_TIMEOUT", true);
        assertThat(chatRequests()).hasSize(1);
        assertThat(body(get("/api/v1/conversations/" + id))).isEqualTo(before);
    }

    @Test
    void rejectsMissingSuppliersAndConversations() throws Exception {
        assertError(get("/api/v1/suppliers/SUP-NONE"), 404, "SUPPLIER_NOT_FOUND", false);
        assertError(post("/api/v1/conversations", JSON.createObjectNode().set("context", context("SUP-NONE"))),
                404, "SUPPLIER_NOT_FOUND", false);
        String missing = UUID.randomUUID().toString();
        assertError(get("/api/v1/conversations/" + missing), 404, "CONVERSATION_NOT_FOUND", false);
        assertError(message(missing, QUERY, null), 404, "CONVERSATION_NOT_FOUND", false);
        assertError(get("/api/v1/conversations/not-a-uuid"), 400, "VALIDATION_ERROR", false);
        String id = create("SUP-0034").path("conversation_id").stringValue();
        JsonNode before = body(get("/api/v1/conversations/" + id));
        assertError(message(id, QUERY, context("SUP-NONE")), 404, "SUPPLIER_NOT_FOUND", false);
        assertThat(body(get("/api/v1/conversations/" + id))).isEqualTo(before);
        assertThat(chatRequests()).isEmpty();
    }

    @ParameterizedTest
    @ValueSource(strings = {"foo/bar", "SUP 0034", " ", "기업-0034"})
    void rejectsInvalidSupplierIdsInCreateAndMessageContextWithoutUpstream(String supplierId) throws Exception {
        assertError(post("/api/v1/conversations", JSON.createObjectNode().set("context", context(supplierId))),
                400, "VALIDATION_ERROR", false);
        assertThat(REQUESTS).isEmpty();

        JsonNode before = create(null);
        String id = before.path("conversation_id").stringValue();
        assertError(message(id, QUERY, context(supplierId)), 400, "VALIDATION_ERROR", false);
        assertThat(REQUESTS).isEmpty();
        assertThat(body(get("/api/v1/conversations/" + id))).isEqualTo(before);
    }

    @ParameterizedTest
    @ValueSource(strings = {"SUP 0034", " ", "기업-0034"})
    void rejectsEncodedSpaceOrUnicodeSupplierDetailIdWithoutUpstream(String supplierId) throws Exception {
        String encodedPathSegment = encode(supplierId).replace("+", "%20");
        assertError(get("/api/v1/suppliers/" + encodedPathSegment), 400, "VALIDATION_ERROR", false);
        assertThat(REQUESTS).isEmpty();
    }

    @ParameterizedTest
    @ValueSource(strings = {"{}", "{", "null", "{\"query\":\"q\",\"history\":[]}",
            "{\"query\":\"q\",\"request_id\":\"fake\"}", "{\"query\":\"q\",\"context\":null}",
            "{\"query\":\"q\",\"context\":{\"selected_supplier_ids\":[\"SUP-0034\",\"SUP-0041\"]}}"})
    void rejectsInvalidMessageBodiesBeforeUpstream(String raw) throws Exception {
        String id = create(null).path("conversation_id").stringValue();
        int before = REQUESTS.size();
        assertError(send("POST", messagesPath(id), raw), 400, "VALIDATION_ERROR", false);
        assertThat(REQUESTS).hasSize(before);
        assertThat(body(get("/api/v1/conversations/" + id)).path("messages").size()).isZero();
    }

    @Test
    void rejectsMalformedLookupAndFailedContextValidationWithoutCallingChat() throws Exception {
        String id = create("SUP-0034").path("conversation_id").stringValue();
        JsonNode before = body(get("/api/v1/conversations/" + id));
        LOOKUP.set(request -> StubResponse.ok(envelope(supplier("SUP-0034"))));
        assertError(message(id, QUERY, context("SUP-0041")), 502, "UPSTREAM_INVALID_RESPONSE", false);
        assertThat(chatRequests()).isEmpty();
        assertThat(body(get("/api/v1/conversations/" + id))).isEqualTo(before);
        LOOKUP.set(request -> new StubResponse(200, "{}", "text/html"));
        assertError(get("/api/v1/business/catalog"), 502, "UPSTREAM_INVALID_RESPONSE", false);
    }

    private JsonNode create(String supplierId) throws Exception {
        var response = post("/api/v1/conversations", JSON.createObjectNode().set("context", context(supplierId)));
        assertThat(response.statusCode()).as(response.body()).isEqualTo(201);
        return body(response);
    }

    private HttpResponse<String> message(String id, String query, ObjectNode context) throws Exception {
        return post(messagesPath(id), messageBody(query, context));
    }

    private static String messagesPath(String id) { return "/api/v1/conversations/" + id + "/messages"; }

    private static ObjectNode messageBody(String query, ObjectNode context) {
        ObjectNode result = JSON.createObjectNode().put("query", query);
        if (context != null) result.set("context", context);
        return result;
    }

    private HttpResponse<String> get(String path) throws Exception { return send("GET", path, null); }
    private HttpResponse<String> post(String path, JsonNode body) throws Exception { return send("POST", path, JSON.writeValueAsString(body)); }
    private HttpResponse<String> send(String method, String path, String body) throws Exception {
        return CLIENT.send(request(method, path, body), HttpResponse.BodyHandlers.ofString());
    }
    private CompletableFuture<HttpResponse<String>> postAsync(String path, JsonNode body) {
        return CLIENT.sendAsync(request("POST", path, JSON.writeValueAsString(body)), HttpResponse.BodyHandlers.ofString());
    }
    private HttpRequest request(String method, String path, String body) {
        return HttpRequest.newBuilder(URI.create("http://127.0.0.1:" + port + path)).timeout(Duration.ofSeconds(8))
                .header("Content-Type", "application/json").header("X-Internal-Api-Key", "browser-supplied-key")
                .method(method, body == null ? HttpRequest.BodyPublishers.noBody() : HttpRequest.BodyPublishers.ofString(body)).build();
    }
    private static JsonNode body(HttpResponse<String> response) { return JSON.readTree(response.body()); }
    private static String encode(String text) { return URLEncoder.encode(text, StandardCharsets.UTF_8); }

    private static void assertError(HttpResponse<String> response, int status, String code, boolean retryable) {
        assertThat(response.statusCode()).as(response.body()).isEqualTo(status);
        JsonNode body = body(response);
        assertThat(body.path("error").path("code").stringValue()).isEqualTo(code);
        assertThat(body.path("error").path("retryable").booleanValue()).isEqualTo(retryable);
        assertThat(body.has("request_id")).isTrue();
    }

    private static void assertStoredPair(JsonNode snapshot, String query, JsonNode response) {
        JsonNode messages = snapshot.path("messages");
        assertThat(messages.size()).isEqualTo(2);
        assertThat(messages.get(0).path("role").stringValue()).isEqualTo("user");
        assertThat(messages.get(0).path("content").stringValue()).isEqualTo(query);
        assertThat(messages.get(0).path("response").isNull()).isTrue();
        assertThat(UUID.fromString(messages.get(0).path("message_id").stringValue())).isNotNull();
        assertThat(messages.get(1).path("role").stringValue()).isEqualTo("assistant");
        assertThat(messages.get(1).path("content")).isEqualTo(response.path("presentation").path("text"));
        assertThat(messages.get(1).path("message_id")).isEqualTo(response.path("response_id"));
        assertThat(messages.get(1).path("response")).isEqualTo(response);
    }

    private static List<StubRequest> chatRequests() {
        return REQUESTS.stream().filter(request -> request.path().equals("/internal/v1/chat/responses")).toList();
    }

    private static ObjectNode context(String supplierId) {
        ObjectNode result = JSON.createObjectNode();
        ArrayNode selected = result.putArray("selected_supplier_ids");
        if (supplierId != null) selected.add(supplierId);
        return result;
    }

    private static JsonNode supplier(String id) {
        for (JsonNode supplier : FIXTURE.path("suppliers")) {
            if (supplier.path("supplier_id").stringValue().equals(id)) return supplier.deepCopy();
        }
        return null;
    }

    private static ObjectNode envelope(JsonNode data) {
        return JSON.createObjectNode().put("schema_version", "0.2").put("mode", "stub").set("data", data);
    }

    private static ObjectNode catalog() {
        Map<String, Set<String>> processes = new LinkedHashMap<>();
        Set<String> regions = new LinkedHashSet<>();
        for (JsonNode supplier : FIXTURE.path("suppliers")) {
            regions.add(supplier.path("region").stringValue());
            for (JsonNode capability : supplier.path("capabilities")) {
                processes.computeIfAbsent(capability.path("process").stringValue(), ignored -> new LinkedHashSet<>())
                        .add(capability.path("material").stringValue());
            }
        }
        ObjectNode data = JSON.createObjectNode();
        ArrayNode values = data.putArray("processes");
        processes.forEach((process, materials) -> {
            ArrayNode items = values.addObject().put("process", process).putArray("materials");
            materials.forEach(items::add);
        });
        ArrayNode regionValues = data.putArray("regions");
        regions.forEach(regionValues::add);
        return data;
    }

    private static StubResponse lookupResponse(StubRequest request) {
        if (request.path().equals("/internal/v1/business/catalog")) return StubResponse.ok(envelope(catalog()));
        if (request.path().equals("/internal/v1/suppliers")) {
            Map<String, String> filters = request.parameters();
            List<JsonNode> suppliers = new ArrayList<>();
            FIXTURE.path("suppliers").forEach(suppliers::add);
            suppliers.sort(Comparator.comparing(supplier -> supplier.path("supplier_id").stringValue()));
            int limit = Integer.parseInt(filters.getOrDefault("limit", "20"));
            ObjectNode data = JSON.createObjectNode().put("limit", limit);
            ArrayNode items = data.putArray("items");
            for (JsonNode supplier : suppliers) {
                if (filters.containsKey("region") && !supplier.path("region").stringValue().equals(filters.get("region"))) continue;
                if ("true".equals(filters.get("iatf_required")) && !supplier.path("iatf16949").booleanValue()) continue;
                if ("true".equals(filters.get("iso14001_required")) && !supplier.path("iso14001").booleanValue()) continue;
                ArrayNode capabilities = JSON.createArrayNode();
                for (JsonNode capability : supplier.path("capabilities")) {
                    if (capability.path("process").stringValue().equals(filters.get("process"))
                            && (!filters.containsKey("material") || capability.path("material").stringValue().equals(filters.get("material")))
                            && capability.path("monthly_capacity").intValue() >= Integer.parseInt(filters.getOrDefault("min_monthly_capacity", "0"))) {
                        capabilities.add(capability.deepCopy());
                    }
                }
                if (!capabilities.isEmpty() && items.size() < limit) {
                    ObjectNode summary = JSON.createObjectNode();
                    for (String field : List.of("supplier_id", "name", "region", "iatf16949", "iso14001", "provenance")) summary.set(field, supplier.get(field).deepCopy());
                    summary.set("matched_capabilities", capabilities);
                    items.add(summary);
                }
            }
            data.put("returned_count", items.size());
            return StubResponse.ok(envelope(data));
        }
        JsonNode detail = supplier(request.path().substring(request.path().lastIndexOf('/') + 1));
        return detail == null ? error(404, null, "SUPPLIER_NOT_FOUND", false) : StubResponse.ok(envelope(detail));
    }

    private static ObjectNode chatResponse(JsonNode request) {
        ObjectNode response = JSON.createObjectNode().put("schema_version", "0.2").put("mode", "stub")
                .put("response_id", UUID.randomUUID().toString()).put("generation_status", "NOT_IMPLEMENTED");
        response.set("request_id", request.get("request_id"));
        response.set("conversation_id", request.get("conversation_id"));
        response.set("resolved_context", request.get("context").deepCopy());
        response.putArray("citations");
        ArrayNode evidence = response.putArray("evidence");
        ObjectNode presentation = response.putObject("presentation").put("renderer", "template");
        ArrayNode cards = presentation.putArray("cards");
        boolean missingSupplier = request.path("context").path("selected_supplier_ids").isEmpty();
        String query = request.path("query").stringValue();
        boolean supported = query.equals("이 회사의 재무 상태와 납기 지표를 알려줘");
        for (JsonNode supportedQuery : FIXTURE.path("supported_queries")) supported |= supportedQuery.stringValue().equals(query);
        if (missingSupplier || !supported) {
            String question = missingSupplier ? "질문할 기업을 선택해 주세요." : "지원 질문을 선택해 주세요.";
            response.put("execution_status", "NEEDS_INPUT");
            presentation.put("status", "NEEDS_INPUT").put("text", question);
            ObjectNode clarification = response.putObject("clarification").put("question", question);
            clarification.putArray("missing_fields").add(missingSupplier ? "context.selected_supplier_ids" : "query");
            clarification.set("options", FIXTURE.get("supported_queries").deepCopy());
        } else {
            String supplierId = request.path("context").path("selected_supplier_ids").get(0).stringValue();
            JsonNode detail = supplier(supplierId);
            String evidenceId = "evidence-" + supplierId;
            response.put("execution_status", "COMPLETED").putNull("clarification");
            presentation.put("status", "READY").put("text", detail.path("name").stringValue() + "은 API 연결 검증용 가상 기업입니다.");
            ObjectNode card = cards.addObject().put("type", "supplier_detail");
            card.set("supplier", detail);
            card.putArray("evidence_ids").add(evidenceId);
            evidence.addObject().put("evidence_id", evidenceId).put("server", "business-mcp").put("tool", "get_supplier")
                    .put("supplier_id", supplierId).put("source", "stub").put("executed", false);
        }
        return response;
    }

    private static StubResponse error(int status, JsonNode requestId, String code, boolean retryable) {
        ObjectNode body = JSON.createObjectNode();
        if (requestId == null) body.putNull("request_id"); else body.set("request_id", requestId);
        body.putObject("error").put("code", code).put("message", "sensitive private upstream detail").put("retryable", retryable);
        return new StubResponse(status, JSON.writeValueAsString(body), "application/json");
    }

    private record StubRequest(String method, String path, Map<String, String> parameters, JsonNode body, String key) {}
    private record StubResponse(int status, String body, String contentType) {
        static StubResponse ok(JsonNode body) { return new StubResponse(200, JSON.writeValueAsString(body), "application/json"); }
    }

    private static void serve(HttpExchange exchange) throws IOException {
        try (exchange) {
            Map<String, String> parameters = new LinkedHashMap<>();
            if (exchange.getRequestURI().getRawQuery() != null) {
                for (String pair : exchange.getRequestURI().getRawQuery().split("&")) {
                    String[] parts = pair.split("=", 2);
                    parameters.put(URLDecoder.decode(parts[0], StandardCharsets.UTF_8),
                            parts.length == 1 ? "" : URLDecoder.decode(parts[1], StandardCharsets.UTF_8));
                }
            }
            byte[] bytes = exchange.getRequestBody().readAllBytes();
            StubRequest request = new StubRequest(exchange.getRequestMethod(), exchange.getRequestURI().getPath(), parameters,
                    bytes.length == 0 ? null : JSON.readTree(bytes), exchange.getRequestHeaders().getFirst("X-Internal-Api-Key"));
            REQUESTS.add(request);
            StubResponse response = request.path().equals("/internal/v1/chat/responses") ? CHAT.get().apply(request) : LOOKUP.get().apply(request);
            byte[] result = response.body().getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().set("Content-Type", response.contentType());
            exchange.sendResponseHeaders(response.status(), result.length);
            exchange.getResponseBody().write(result);
        }
    }
}
