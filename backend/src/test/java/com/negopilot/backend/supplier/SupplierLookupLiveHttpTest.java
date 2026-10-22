package com.negopilot.backend.supplier;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.Consumer;
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

/** v2 live lookups: Python answers from business-mcp, so Spring checks shape and filters instead of a fixture. */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@org.springframework.test.context.ActiveProfiles("legacy-test")
class SupplierLookupLiveHttpTest {
    private static final JsonMapper JSON = JsonMapper.builder().build();
    private static final String SERVER_KEY = "supplier-lookup-live-test-key";
    private static final HttpServer UPSTREAM;
    private static final ExecutorService EXECUTOR = Executors.newCachedThreadPool();
    private static final HttpClient CLIENT = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(2)).build();
    private static final List<String> PATHS = new CopyOnWriteArrayList<>();
    private static final AtomicReference<Consumer<ObjectNode>> CORRUPT = new AtomicReference<>();
    private static final AtomicReference<Integer> STATUS = new AtomicReference<>();

    static {
        try {
            UPSTREAM = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            UPSTREAM.setExecutor(EXECUTOR);
            UPSTREAM.createContext("/", SupplierLookupLiveHttpTest::serve);
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
        registry.add("agent.execution-mode", () -> "live");
    }

    @BeforeEach
    void reset() {
        PATHS.clear();
        CORRUPT.set(body -> {});
        STATUS.set(200);
    }

    @AfterAll
    static void close() {
        UPSTREAM.stop(0);
        EXECUTOR.shutdownNow();
        CLIENT.close();
    }

    @Test
    void proxiesLiveCatalogSearchAndDetail() throws Exception {
        var catalog = get("/api/v2/business/catalog");
        assertThat(catalog.statusCode()).as(catalog.body()).isEqualTo(200);
        assertThat(body(catalog)).isEqualTo(catalog());

        var search = get("/api/v2/suppliers?process=" + encode("다이캐스팅") + "&region=" + encode("경남"));
        assertThat(search.statusCode()).as(search.body()).isEqualTo(200);
        assertThat(body(search).path("mode").stringValue()).isEqualTo("live");
        assertThat(body(search).path("data").path("returned_count").intValue()).isEqualTo(2);

        var detail = get("/api/v2/suppliers/SUP-0001");
        assertThat(detail.statusCode()).as(detail.body()).isEqualTo(200);
        assertThat(body(detail).path("data").path("provenance").path("profile").stringValue()).isEqualTo("DART");
        assertThat(PATHS).containsExactly("/internal/v2/business/catalog", "/internal/v2/suppliers",
                "/internal/v2/suppliers/SUP-0001");
    }

    @ParameterizedTest
    @ValueSource(strings = {"stub-mode", "stub-provenance", "unsorted", "wrong-region", "wrong-process",
            "over-limit-count"})
    void rejectsLiveSearchThatDoesNotMatchContractOrFilters(String kind) throws Exception {
        CORRUPT.set(body -> {
            ArrayNode items = (ArrayNode) body.at("/data/items");
            switch (kind) {
                case "stub-mode" -> body.put("mode", "stub");
                case "stub-provenance" -> ((ObjectNode) items.get(0).get("provenance")).put("profile", "STUB");
                case "unsorted" -> items.add(items.remove(0));
                case "wrong-region" -> ((ObjectNode) items.get(0)).put("region", "울산");
                case "wrong-process" -> ((ObjectNode) items.get(0).at("/matched_capabilities/0")).put("process", "프레스");
                default -> ((ObjectNode) body.get("data")).put("returned_count", 3);
            }
        });
        var response = get("/api/v2/suppliers?process=" + encode("다이캐스팅") + "&region=" + encode("경남"));
        assertError(response, 502, "UPSTREAM_INVALID_RESPONSE", false);
    }

    @Test
    void rejectsLiveSearchItemWithoutRequiredIso14001() throws Exception {
        var response = get("/api/v2/suppliers?process=" + encode("다이캐스팅") + "&iso14001_required=true");
        assertError(response, 502, "UPSTREAM_INVALID_RESPONSE", false);
        CORRUPT.set(body -> ((ArrayNode) body.at("/data/items")).remove(1));
        CORRUPT.set(CORRUPT.get().andThen(body -> ((ObjectNode) body.get("data")).put("returned_count", 1)));
        var filtered = get("/api/v2/suppliers?process=" + encode("다이캐스팅") + "&iso14001_required=true");
        assertThat(filtered.statusCode()).as(filtered.body()).isEqualTo(200);
    }

    @ParameterizedTest
    @ValueSource(strings = {"other-supplier", "stub-financials", "extra-field"})
    void rejectsLiveDetailThatDoesNotMatchContract(String kind) throws Exception {
        CORRUPT.set(body -> {
            ObjectNode data = (ObjectNode) body.get("data");
            switch (kind) {
                case "other-supplier" -> data.put("supplier_id", "SUP-0002");
                case "stub-financials" -> ((ObjectNode) data.at("/financials/0")).put("source", "STUB");
                default -> data.put("extra", true);
            }
        });
        assertError(get("/api/v2/suppliers/SUP-0001"), 502, "UPSTREAM_INVALID_RESPONSE", false);
    }

    @Test
    void businessDataOutageIsRetryable() throws Exception {
        STATUS.set(503);
        assertError(get("/api/v2/business/catalog"), 503, "UPSTREAM_UNAVAILABLE", true);
    }

    @Test
    void readsManualSupplierWithLongIdAndMissingOptionalData() throws Exception {
        CORRUPT.set(body -> {
            ObjectNode data = (ObjectNode) body.get("data");
            data.put("supplier_id", "SUP-10000");
            data.putObject("quality").putNull("defect_ppm").putNull("on_time_delivery_rate");
            data.putArray("financials");
            data.putArray("recent_quotes");
            data.putNull("existing_contract");
            data.putObject("provenance").put("profile", "MANUAL")
                    .put("financials", "UNKNOWN").put("operations", "MANUAL");
        });
        var response = get("/api/v2/suppliers/SUP-10000");
        assertThat(response.statusCode()).as(response.body()).isEqualTo(200);
        assertThat(body(response).at("/data/supplier_id").stringValue()).isEqualTo("SUP-10000");
        assertThat(PATHS).containsExactly("/internal/v2/suppliers/SUP-10000");
    }

    @ParameterizedTest
    @ValueSource(strings = {"SUP-1", "sup-0001", "SUP-0001extra", "SUP-1234567890123456789"})
    void rejectsInvalidLiveSupplierIdsBeforeCallingPython(String id) throws Exception {
        assertError(get("/api/v2/suppliers/" + id), 400, "VALIDATION_ERROR", false);
        assertThat(PATHS).isEmpty();
    }

    @Test
    void v1LookupsStayStubOnlyWithoutCallingPython() throws Exception {
        assertError(get("/api/v1/business/catalog"), 503, "DEMO_MODE_REQUIRED", false);
        assertError(get("/api/v1/suppliers/SUP-0001"), 503, "DEMO_MODE_REQUIRED", false);
        assertThat(PATHS).isEmpty();
    }

    @Test
    void rejectsV1StyleEnvelope() throws Exception {
        CORRUPT.set(body -> body.put("schema_version", "0.2"));
        assertError(get("/api/v2/business/catalog"), 502, "UPSTREAM_INVALID_RESPONSE", false);
    }

    private static ObjectNode envelope() {
        return JSON.createObjectNode().put("schema_version", "0.3").put("mode", "live");
    }

    private static ObjectNode catalog() {
        ObjectNode body = envelope();
        ObjectNode data = body.putObject("data");
        data.putArray("processes").addObject().put("process", "다이캐스팅").putArray("materials").add("A380").add("ADC12");
        data.putArray("regions").add("경남").add("울산");
        return body;
    }

    private static ObjectNode capability(ArrayNode array, String material, int capacity) {
        return array.addObject().put("process", "다이캐스팅").put("material", material).put("monthly_capacity", capacity);
    }

    private static ObjectNode provenance(ObjectNode parent, String source) {
        return parent.putObject("provenance").put("profile", source).put("financials", source).put("operations", "MOCK");
    }

    private static ObjectNode search() {
        ObjectNode body = envelope();
        ObjectNode data = body.putObject("data").put("returned_count", 2).put("limit", 20);
        ArrayNode items = data.putArray("items");
        ObjectNode first = items.addObject().put("supplier_id", "SUP-0001").put("name", "공시기업").put("region", "경남")
                .put("iatf16949", true).put("iso14001", true);
        ArrayNode firstCapabilities = first.putArray("matched_capabilities");
        capability(firstCapabilities, "A380", 8000);
        capability(firstCapabilities, "ADC12", 20000);
        provenance(first, "DART");
        ObjectNode second = items.addObject().put("supplier_id", "SUP-0040").put("name", "가상기업").put("region", "경남")
                .put("iatf16949", false).put("iso14001", false);
        capability(second.putArray("matched_capabilities"), "ADC12", 5000);
        provenance(second, "MOCK");
        return body;
    }

    private static ObjectNode detail() {
        ObjectNode body = envelope();
        ObjectNode data = body.putObject("data").put("supplier_id", "SUP-0001").put("name", "공시기업")
                .put("region", "경남").put("iatf16949", true).put("iso14001", true);
        capability(data.putArray("capabilities"), "ADC12", 20000);
        data.putObject("quality").put("defect_ppm", 120).put("on_time_delivery_rate", 95.7);
        data.putArray("financials").addObject().put("year", 2024).put("revenue_krw", 500_000_000_000L)
                .put("operating_profit_krw", -2_000_000_000L).putNull("debt_ratio").put("source", "DART");
        data.putArray("recent_quotes").addObject().put("process", "다이캐스팅").put("material", "ADC12")
                .put("unit_price_krw", 4200).put("quoted_at", "2026-03-02");
        data.putObject("existing_contract").put("payment_days", 60).put("price_linkage", false);
        provenance(data, "DART");
        return body;
    }

    private static void serve(HttpExchange exchange) throws IOException {
        try (exchange) {
            String path = exchange.getRequestURI().getPath();
            PATHS.add(path);
            ObjectNode body;
            if (STATUS.get() != 200) {
                body = JSON.createObjectNode().putNull("request_id");
                body.putObject("error").put("code", "UPSTREAM_UNAVAILABLE").put("message", "private db detail")
                        .put("retryable", true);
            } else {
                body = switch (path) {
                    case "/internal/v2/business/catalog" -> catalog();
                    case "/internal/v2/suppliers" -> search();
                    default -> detail();
                };
                CORRUPT.get().accept(body);
            }
            byte[] result = JSON.writeValueAsBytes(body);
            exchange.getResponseHeaders().set("Content-Type", "application/json");
            exchange.sendResponseHeaders(STATUS.get(), result.length);
            exchange.getResponseBody().write(result);
        }
    }

    private HttpResponse<String> get(String path) throws Exception {
        return CLIENT.send(HttpRequest.newBuilder(URI.create("http://127.0.0.1:" + port + path))
                .timeout(Duration.ofSeconds(8)).GET().build(), HttpResponse.BodyHandlers.ofString());
    }

    private static JsonNode body(HttpResponse<String> response) { return JSON.readTree(response.body()); }
    private static String encode(String text) { return URLEncoder.encode(text, StandardCharsets.UTF_8); }

    private static void assertError(HttpResponse<String> response, int status, String code, boolean retryable) {
        assertThat(response.statusCode()).as(response.body()).isEqualTo(status);
        JsonNode error = body(response).path("error");
        assertThat(error.path("code").stringValue()).isEqualTo(code);
        assertThat(error.path("retryable").booleanValue()).isEqualTo(retryable);
        assertThat(response.body()).doesNotContain("private db detail", SERVER_KEY);
    }
}
