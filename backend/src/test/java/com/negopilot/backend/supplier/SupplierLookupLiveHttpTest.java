package com.negopilot.backend.supplier;

import com.negopilot.backend.support.HttpSliceTestConfiguration;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.URLEncoder;
import java.net.URLDecoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.HashMap;
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
@SpringBootTest(classes = {HttpSliceTestConfiguration.class, SupplierLookupController.class, SupplierLookupGateway.class,
        SupplierGatewayClient.class, SupplierLookupRequestValidator.class, SupplierLookupResponseValidator.class},
        webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class SupplierLookupLiveHttpTest {
    private static final JsonMapper JSON = JsonMapper.builder().build();
    private static final String SERVER_KEY = "supplier-lookup-live-test-key";
    private static final HttpServer UPSTREAM;
    private static final ExecutorService EXECUTOR = Executors.newCachedThreadPool();
    private static final HttpClient CLIENT = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(2)).build();
    private static final List<Map<String, String>> QUERIES = new CopyOnWriteArrayList<>();
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
        QUERIES.clear();
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
        var catalog = get("/api/business/catalog");
        assertThat(catalog.statusCode()).as(catalog.body()).isEqualTo(200);
        assertThat(body(catalog)).isEqualTo(catalog());

        var search = get("/api/suppliers?process=" + encode("다이캐스팅") + "&region=" + encode("경남"));
        assertThat(search.statusCode()).as(search.body()).isEqualTo(200);
        assertThat(body(search).path("mode").stringValue()).isEqualTo("live");
        assertThat(body(search).path("data").path("returned_count").intValue()).isEqualTo(2);

        var detail = get("/api/suppliers/SUP-0001");
        assertThat(detail.statusCode()).as(detail.body()).isEqualTo(200);
        assertThat(body(detail).path("data").path("provenance").path("profile").stringValue()).isEqualTo("DART");
        assertThat(PATHS).containsExactly("/internal/business/catalog", "/internal/suppliers",
                "/internal/suppliers/SUP-0001");
    }

    @ParameterizedTest
    @ValueSource(strings = {"stub-mode", "stub-provenance", "unsorted", "wrong-region", "wrong-process",
            "over-limit-count", "wrong-page", "wrong-total", "wrong-sort", "duplicate-id"})
    void rejectsLiveSearchThatDoesNotMatchContractOrFilters(String kind) throws Exception {
        CORRUPT.set(body -> {
            ArrayNode items = (ArrayNode) body.at("/data/items");
            switch (kind) {
                case "stub-mode" -> body.put("mode", "stub");
                case "stub-provenance" -> ((ObjectNode) items.get(0).get("provenance")).put("profile", "STUB");
                case "unsorted" -> items.add(items.remove(0));
                case "wrong-region" -> ((ObjectNode) items.get(0)).put("region", "울산");
                case "wrong-process" -> ((ObjectNode) items.get(0).at("/matched_capabilities/0")).put("process", "프레스");
                case "wrong-page" -> ((ObjectNode) body.get("data")).put("page", 1);
                case "wrong-total" -> ((ObjectNode) body.get("data")).put("total", 21);
                case "wrong-sort" -> ((ObjectNode) body.get("data")).put("sort", "region");
                case "duplicate-id" -> ((ObjectNode) items.get(1)).put("supplier_id", items.get(0).get("supplier_id").stringValue());
                default -> ((ObjectNode) body.get("data")).put("returned_count", 3);
            }
        });
        var response = get("/api/suppliers?process=" + encode("다이캐스팅") + "&region=" + encode("경남"));
        assertError(response, 502, "UPSTREAM_INVALID_RESPONSE", false);
    }

    @Test
    void rejectsLiveSearchItemWithoutRequiredIso14001() throws Exception {
        var response = get("/api/suppliers?process=" + encode("다이캐스팅") + "&iso14001_required=true");
        assertError(response, 502, "UPSTREAM_INVALID_RESPONSE", false);
        CORRUPT.set(body -> ((ArrayNode) body.at("/data/items")).remove(1));
        CORRUPT.set(CORRUPT.get().andThen(body -> ((ObjectNode) body.get("data")).put("returned_count", 1).put("total", 1)));
        var filtered = get("/api/suppliers?process=" + encode("다이캐스팅") + "&iso14001_required=true");
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
        assertError(get("/api/suppliers/SUP-0001"), 502, "UPSTREAM_INVALID_RESPONSE", false);
    }

    @Test
    void businessDataOutageIsRetryable() throws Exception {
        STATUS.set(503);
        assertError(get("/api/business/catalog"), 503, "UPSTREAM_UNAVAILABLE", true);
    }

    @Test
    void readsManualSupplierWithLongIdAndMissingOptionalData() throws Exception {
        CORRUPT.set(body -> {
            ObjectNode data = (ObjectNode) body.get("data");
            data.put("supplier_id", "SUP-10000");
            data.putObject("quality").putNull("defect_ppm").putNull("on_time_delivery_rate").putNull("audit_grade").putNull("years_dealing");
            data.putArray("financials");
            data.putArray("recent_quotes");
            data.putNull("existing_contract");
            data.putObject("provenance").put("profile", "MANUAL")
                    .put("financials", "UNKNOWN").put("operations", "MANUAL");
        });
        var response = get("/api/suppliers/SUP-10000");
        assertThat(response.statusCode()).as(response.body()).isEqualTo(200);
        assertThat(body(response).at("/data/supplier_id").stringValue()).isEqualTo("SUP-10000");
        assertThat(PATHS).containsExactly("/internal/suppliers/SUP-10000");
    }

    @ParameterizedTest
    @ValueSource(strings = {"SUP-1", "sup-0001", "SUP-0001extra", "SUP-1234567890123456789"})
    void rejectsInvalidLiveSupplierIdsBeforeCallingPython(String id) throws Exception {
        assertError(get("/api/suppliers/" + id), 400, "VALIDATION_ERROR", false);
        assertThat(PATHS).isEmpty();
    }

    @Test
    void rejectsV1StyleEnvelope() throws Exception {
        CORRUPT.set(body -> body.put("schema_version", "0.2"));
        assertError(get("/api/business/catalog"), 502, "UPSTREAM_INVALID_RESPONSE", false);
    }

    @Test
    void forwardsAllSuppliersMaterialOnlyQueryAndLegacySizeWithoutRequiringProcess() throws Exception {
        assertThat(get("/api/suppliers").statusCode()).isEqualTo(200);
        assertThat(QUERIES.get(0)).containsEntry("page", "0").containsEntry("size", "20")
                .containsEntry("sort", "name").containsEntry("direction", "asc").doesNotContainKey("process");
        assertThat(get("/api/suppliers?material=ADC12&limit=20").statusCode()).isEqualTo(502);
        // A380 in the upstream fixture must still be rejected when material alone is requested.
        assertThat(QUERIES.get(1)).containsEntry("material", "ADC12").containsEntry("size", "20").doesNotContainKeys("process", "limit");
        CORRUPT.set(body -> {
            var items = (ArrayNode) body.at("/data/items");
            ((ObjectNode) items.get(0)).put("name", "A%_정밀");
            ((ObjectNode) items.get(1)).put("name", "B%_정밀");
        });
        assertThat(get("/api/suppliers?q=" + encode(" %_ ")).statusCode()).isEqualTo(200);
        assertThat(QUERIES.get(2)).containsEntry("q", "%_");
    }

    @Test
    void forwardsRequestedPageSortAndIndependentMaterialFilter() throws Exception {
        CORRUPT.set(body -> {
            var data = (ObjectNode) body.get("data");
            var items = (ArrayNode) data.get("items");
            items.remove(1);
            ((ArrayNode) items.get(0).get("matched_capabilities")).remove(0);
            data.put("page", 1).put("size", 1).put("returned_count", 1).put("total_pages", 2).put("sort", "region").put("direction", "desc");
        });
        assertThat(get("/api/suppliers?material=ADC12&page=1&size=1&sort=region&direction=desc").statusCode()).isEqualTo(200);
        assertThat(QUERIES.get(0)).containsEntry("page", "1").containsEntry("size", "1")
                .containsEntry("material", "ADC12").containsEntry("sort", "region").containsEntry("direction", "desc");
    }

    @ParameterizedTest
    @ValueSource(strings = {"page=-1", "size=101", "limit=20&page=0", "limit=20&size=20", "sort=unknown",
            "sort=latest_quote_price", "q=a&q=b"})
    void rejectsAmbiguousOrInvalidPageRequestsBeforePython(String query) throws Exception {
        assertError(get("/api/suppliers?" + query), 400, "VALIDATION_ERROR", false);
        assertThat(PATHS).isEmpty();
    }

    @Test
    void quoteEndpointForwardsTargetAndFiltersAndRejectsOtherSupplier() throws Exception {
        var response = get("/api/suppliers/SUP-0001/quotes?material=SPCC");
        assertThat(response.statusCode()).as(response.body()).isEqualTo(200);
        assertThat(PATHS).containsExactly("/internal/suppliers/SUP-0001/quotes");
        assertThat(QUERIES.get(0)).containsEntry("material", "SPCC").containsEntry("sort", "quoted_at")
                .containsEntry("direction", "desc").containsEntry("page", "0").containsEntry("size", "20");
        CORRUPT.set(body -> ((ObjectNode) body.get("data")).put("supplier_id", "SUP-0002"));
        assertError(get("/api/suppliers/SUP-0001/quotes"), 502, "UPSTREAM_INVALID_RESPONSE", false);
    }

    private static ObjectNode envelope() {
        return JSON.createObjectNode().put("schema_version", "0.4").put("mode", "live");
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
        ObjectNode data = body.putObject("data").put("returned_count", 2).put("page", 0).put("size", 20)
                .put("total", 2).put("total_pages", 1).put("sort", "name").put("direction", "asc");
        ArrayNode items = data.putArray("items");
        ObjectNode first = items.addObject().put("supplier_id", "SUP-0001").put("name", "가공시기업").put("region", "경남")
                .put("iatf16949", true).put("iso14001", true);
        ArrayNode firstCapabilities = first.putArray("matched_capabilities");
        capability(firstCapabilities, "A380", 8000);
        capability(firstCapabilities, "ADC12", 20000);
        provenance(first, "DART");
        expandedSummary(first, 20000);
        ObjectNode second = items.addObject().put("supplier_id", "SUP-0040").put("name", "나가상기업").put("region", "경남")
                .put("iatf16949", false).put("iso14001", false);
        capability(second.putArray("matched_capabilities"), "ADC12", 5000);
        provenance(second, "MOCK");
        expandedSummary(second, 5000);
        return body;
    }

    private static ObjectNode detail() {
        ObjectNode body = envelope();
        ObjectNode data = body.putObject("data").put("supplier_id", "SUP-0001").put("name", "가공시기업")
                .put("region", "경남").put("iatf16949", true).put("iso14001", true);
        capability(data.putArray("capabilities"), "ADC12", 20000);
        data.putObject("quality").put("defect_ppm", 120).put("on_time_delivery_rate", 95.7).put("audit_grade", "A").put("years_dealing", 3);
        data.putArray("financials").add(SupplierLookupResponseValidatorTest.financial(2024, "DART"));
        data.putArray("recent_quotes").addObject().put("quote_id", 1).put("process", "다이캐스팅").put("material", "ADC12")
                .put("unit_price_krw", 4200).put("quoted_at", "2026-03-02");
        data.putObject("existing_contract").put("payment_days", 60).put("price_linkage", false).put("raw_material_ratio", 0.4).put("auto_renewal", false);
        data.putNull("founded_year").putNull("employees").putNull("corp_code").putNull("stock_code")
                .putNull("induty_code").putNull("biz_no_masked").putNull("created_at").putNull("updated_at");
        provenance(data, "DART");
        return body;
    }

    private static void expandedSummary(ObjectNode item, int capacity) {
        item.putNull("founded_year").putNull("employees").putNull("updated_at").putNull("latest_quote")
                .putNull("existing_contract").put("max_monthly_capacity", capacity);
        item.putObject("quality").putNull("defect_ppm").putNull("on_time_delivery_rate").putNull("audit_grade").putNull("years_dealing");
    }

    private static ObjectNode quotes() {
        ObjectNode body = envelope();
        var data = body.putObject("data").put("supplier_id", "SUP-0001").put("page", 0).put("size", 20)
                .put("total", 2).put("total_pages", 1).put("returned_count", 2).put("sort", "quoted_at").put("direction", "desc");
        data.putArray("items").add(SupplierLookupResponseValidatorTest.quote(2)).add(SupplierLookupResponseValidatorTest.quote(1));
        return body;
    }

    private static Map<String, String> query(URI uri) {
        Map<String, String> result = new HashMap<>();
        if (uri.getRawQuery() != null) for (String pair : uri.getRawQuery().split("&")) {
            String[] parts = pair.split("=", 2);
            result.put(URLDecoder.decode(parts[0], StandardCharsets.UTF_8), URLDecoder.decode(parts[1], StandardCharsets.UTF_8));
        }
        return result;
    }

    private static void serve(HttpExchange exchange) throws IOException {
        try (exchange) {
            String path = exchange.getRequestURI().getPath();
            PATHS.add(path);
            QUERIES.add(query(exchange.getRequestURI()));
            ObjectNode body;
            if (STATUS.get() != 200) {
                body = JSON.createObjectNode().putNull("request_id");
                body.putObject("error").put("code", "UPSTREAM_UNAVAILABLE").put("message", "private db detail")
                        .put("retryable", true);
            } else {
                body = switch (path) {
                    case "/internal/business/catalog" -> catalog();
                    case "/internal/suppliers" -> search();
                    case "/internal/suppliers/SUP-0001/quotes" -> quotes();
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
