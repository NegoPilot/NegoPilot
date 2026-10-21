package com.negopilot.backend;

import java.io.IOException;
import java.util.UUID;
import java.util.function.Consumer;
import java.util.stream.Stream;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.MethodSource;
import org.junit.jupiter.params.provider.ValueSource;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

class SupplierChatResponseValidatorTest {
    private static final JsonMapper JSON = JsonMapper.builder().build();
    private static final JsonNode FIXTURE = fixture();
    private static final UUID REQUEST = UUID.fromString("a45c87a1-e32b-43a7-870b-a12955478df2");
    private static final UUID CONVERSATION = UUID.fromString("9ab19bce-195b-49f3-9dc8-6f049352f5b7");
    private static final String OVERVIEW = "이 회사에 대해 알려줘";
    private final SupplierChatResponseValidator validator = new SupplierChatResponseValidator(FIXTURE);

    @Test
    void acceptsCatalogWithUnspecifiedArrayOrder() {
        ObjectNode body = catalog();
        assertDoesNotThrow(() -> validator.validateCatalog(body));
        ArrayNode regions = (ArrayNode) body.at("/data/regions");
        JsonNode first = regions.remove(0);
        regions.add(first);
        ArrayNode processes = (ArrayNode) body.at("/data/processes");
        first = processes.remove(0);
        processes.add(first);
        assertDoesNotThrow(() -> validator.validateCatalog(body));
    }

    @ParameterizedTest
    @ValueSource(strings = {"duplicate-region", "missing-region", "extra-region", "duplicate-process", "wrong-material", "extra-field"})
    void rejectsIncorrectCatalog(String kind) {
        ObjectNode body = catalog();
        switch (kind) {
            case "duplicate-region" -> ((ArrayNode) body.at("/data/regions")).add("인천");
            case "missing-region" -> ((ArrayNode) body.at("/data/regions")).remove(0);
            case "extra-region" -> ((ArrayNode) body.at("/data/regions")).add("서울");
            case "duplicate-process" -> ((ArrayNode) body.at("/data/processes")).add(body.at("/data/processes/0").deepCopy());
            case "wrong-material" -> ((ArrayNode) body.at("/data/processes/0/materials")).add("unknown");
            default -> body.put("extra", true);
        }
        invalid(() -> validator.validateCatalog(body));
    }

    @Test
    void validatesSearchFiltersGroupingSortingAndLimit() {
        ObjectNode filters = filters("다이캐스팅");
        ObjectNode body = search(20, summary(0, 0, 1), summary(1, 0));
        assertDoesNotThrow(() -> validator.validateSearch(body, filters));
        filters.put("material", "ADC12");
        ObjectNode material = search(20, summary(0, 0), summary(1, 0));
        assertDoesNotThrow(() -> validator.validateSearch(material, filters));
        filters.put("region", "부산");
        assertDoesNotThrow(() -> validator.validateSearch(search(20, summary(1, 0)), filters));
        filters.remove("region");
        filters.put("limit", 1);
        assertDoesNotThrow(() -> validator.validateSearch(search(1, summary(0, 0)), filters));
        assertDoesNotThrow(() -> validator.validateSearch(search(20, summary(2, 0)), filters("프레스")));
        ObjectNode certified = filters("프레스").put("iatf_required", true);
        assertDoesNotThrow(() -> validator.validateSearch(search(20), certified));
        assertDoesNotThrow(() -> validator.validateSearch(search(20), filters("없는 공정")));
    }

    @Test
    void validatesSearchByMinimumMonthlyCapacity() {
        ObjectNode filters = filters("다이캐스팅").put("min_monthly_capacity", 40000);
        assertDoesNotThrow(() -> validator.validateSearch(search(20, summary(0, 0), summary(1, 0)), filters));
        filters.put("min_monthly_capacity", 50000);
        assertDoesNotThrow(() -> validator.validateSearch(search(20, summary(0, 0)), filters));
        invalid(() -> validator.validateSearch(search(20, summary(0, 0), summary(1, 0)), filters));
        invalid(() -> validator.validateSearch(search(20, summary(0, 0, 1)), filters));
        assertDoesNotThrow(() -> validator.validateSearch(search(20), filters.put("min_monthly_capacity", 70000)));
    }

    @ParameterizedTest
    @ValueSource(strings = {"wrong-count", "count-string", "wrong-limit", "unsorted", "missing-company", "duplicate-company",
            "wrong-capability", "extra-capability", "wrong-number", "wrong-provenance", "wrong-type"})
    void rejectsIncorrectSearch(String kind) {
        ObjectNode body = search(20, summary(0, 0), summary(1, 0));
        ArrayNode items = (ArrayNode) body.at("/data/items");
        switch (kind) {
            case "wrong-count" -> ((ObjectNode) body.get("data")).put("returned_count", 1);
            case "count-string" -> ((ObjectNode) body.get("data")).put("returned_count", "2");
            case "wrong-limit" -> ((ObjectNode) body.get("data")).put("limit", 10);
            case "unsorted" -> { JsonNode first = items.remove(0); items.add(first); }
            case "missing-company" -> items.remove(0);
            case "duplicate-company" -> items.set(1, items.get(0).deepCopy());
            case "wrong-capability" -> items.set(0, summary(0, 1));
            case "extra-capability" -> items.set(0, summary(0, 0, 1));
            case "wrong-number" -> ((ObjectNode) items.at("/0/matched_capabilities/0")).put("monthly_capacity", 999);
            case "wrong-provenance" -> ((ObjectNode) items.at("/0/provenance")).put("profile", "LIVE");
            default -> ((ObjectNode) items.get(0)).put("iatf16949", "true");
        }
        invalid(() -> validator.validateSearch(body, filters("다이캐스팅").put("material", "ADC12")));
    }

    @Test
    void validatesEveryDetailAndAllowsEquivalentJsonNumbers() {
        for (JsonNode supplier : FIXTURE.get("suppliers")) {
            assertDoesNotThrow(() -> validator.validateDetail(envelope(supplier.deepCopy()), supplier.get("supplier_id").stringValue()));
        }
        ObjectNode supplier = supplier(0);
        ((ObjectNode) supplier.get("quality")).put("defect_ppm", 420.0);
        assertDoesNotThrow(() -> validator.validateDetail(envelope(supplier), "SUP-0034"));
    }

    @ParameterizedTest
    @ValueSource(strings = {"id", "name", "region", "price", "quality", "financials", "contract", "provenance",
            "missing", "extra", "integer-as-float", "null-missing-data"})
    void rejectsDetailDriftFromFixture(String kind) {
        ObjectNode supplier = supplier(0);
        switch (kind) {
            case "id" -> supplier.put("supplier_id", "SUP-0041");
            case "name" -> supplier.put("name", "다른 기업");
            case "region" -> supplier.put("region", "부산");
            case "price" -> ((ObjectNode) supplier.at("/recent_quotes/0")).put("unit_price_krw", 3801);
            case "quality" -> ((ObjectNode) supplier.get("quality")).put("defect_ppm", 421);
            case "financials" -> ((ObjectNode) supplier.at("/financials/0")).put("revenue_krw", 12000000001L);
            case "contract" -> ((ObjectNode) supplier.get("existing_contract")).put("price_linkage", false);
            case "provenance" -> ((ObjectNode) supplier.get("provenance")).put("operations", "LIVE");
            case "missing" -> supplier.remove("quality");
            case "extra" -> supplier.put("secret", "not allowed");
            case "integer-as-float" -> ((ObjectNode) supplier.at("/capabilities/0")).put("monthly_capacity", 60000.0);
            default -> supplier.putNull("financials");
        }
        invalid(() -> validator.validateDetail(envelope(supplier), "SUP-0034"));
    }

    @ParameterizedTest
    @ValueSource(strings = {"이 회사에 대해 알려줘", "이 회사의 재무 상태를 알려줘", "납기와 품질은 어때?",
            "이 회사의 재무 상태와 납기 지표를 알려줘", "  이 회사에 대해 알려줘  "})
    void acceptsSupportedQueriesAndAlias(String query) {
        ObjectNode body = completed();
        JsonNode before = body.deepCopy();
        assertDoesNotThrow(() -> validator.validateChat(body, REQUEST, CONVERSATION, context("SUP-0034"), query));
        assertEquals(before, body);
    }

    @Test
    void needsInputPrioritizesSupplierSelectionAndKeepsSupportedOptions() {
        ObjectNode missingSupplier = needsInput(true);
        assertDoesNotThrow(() -> validator.validateChat(missingSupplier, REQUEST, CONVERSATION, context(), "unsupported"));
        assertDoesNotThrow(() -> validator.validateChat(missingSupplier, REQUEST, CONVERSATION, context(), OVERVIEW));
        assertDoesNotThrow(() -> validator.validateChat(needsInput(false), REQUEST, CONVERSATION, context("SUP-0034"), "unsupported"));
        invalid(() -> validator.validateChat(needsInput(false), REQUEST, CONVERSATION, context("SUP-0034"), OVERVIEW));
        invalid(() -> validator.validateChat(completed(), REQUEST, CONVERSATION, context("SUP-0034"), "unsupported"));
    }

    @ParameterizedTest
    @MethodSource("badChats")
    void rejectsChatStateAndReferenceViolations(Consumer<ObjectNode> mutate) {
        ObjectNode body = completed();
        mutate.accept(body);
        invalid(() -> validator.validateChat(body, REQUEST, CONVERSATION, context("SUP-0034"), OVERVIEW));
    }

    static Stream<Consumer<ObjectNode>> badChats() {
        return Stream.of(
                b -> b.put("schema_version", "0.1"), b -> b.put("mode", "live"),
                b -> b.remove("clarification"), b -> b.put("extra", true),
                b -> b.put("request_id", UUID.randomUUID().toString()),
                b -> b.put("request_id", REQUEST.toString().toUpperCase()),
                b -> b.put("conversation_id", UUID.randomUUID().toString()),
                b -> b.put("response_id", "1-1-1-1-1"), b -> b.putNull("response_id"),
                b -> b.put("generation_status", "COMPLETED"), b -> b.put("execution_status", "NEEDS_INPUT"),
                b -> b.set("resolved_context", context("SUP-0041")),
                b -> ((ObjectNode) b.get("presentation")).put("status", "NEEDS_INPUT"),
                b -> ((ObjectNode) b.get("presentation")).put("renderer", "model"),
                b -> ((ObjectNode) b.get("presentation")).put("text", "  "),
                b -> ((ObjectNode) b.get("presentation")).put("text", "a".repeat(4001)),
                b -> ((ArrayNode) b.at("/presentation/cards")).removeAll(),
                b -> ((ObjectNode) b.at("/presentation/cards/0")).set("supplier", supplier(1)),
                b -> ((ObjectNode) b.at("/presentation/cards/0/supplier/quality")).put("defect_ppm", 999),
                b -> ((ArrayNode) b.at("/presentation/cards/0/evidence_ids")).add("another"),
                b -> ((ArrayNode) b.at("/presentation/cards/0/evidence_ids")).add("stub-1"),
                b -> ((ArrayNode) b.get("evidence")).removeAll(),
                b -> ((ObjectNode) b.at("/evidence/0")).put("evidence_id", "wrong"),
                b -> ((ObjectNode) b.at("/evidence/0")).put("supplier_id", "SUP-0041"),
                b -> ((ObjectNode) b.at("/evidence/0")).put("source", "live"),
                b -> ((ObjectNode) b.at("/evidence/0")).put("server", "law-mcp"),
                b -> ((ObjectNode) b.at("/evidence/0")).put("tool", "search_suppliers"),
                b -> ((ObjectNode) b.at("/evidence/0")).put("executed", true),
                b -> ((ObjectNode) b.at("/evidence/0")).put("executed", "false"),
                b -> ((ArrayNode) b.get("citations")).add("not allowed"),
                b -> b.set("clarification", JSON.createObjectNode())
        );
    }

    @ParameterizedTest
    @ValueSource(strings = {"wrong-missing", "wrong-question", "wrong-options", "duplicate-options", "card", "null-clarification"})
    void rejectsMalformedNeedsInput(String kind) {
        ObjectNode body = needsInput(true);
        ObjectNode clarification = (ObjectNode) body.get("clarification");
        switch (kind) {
            case "wrong-missing" -> clarification.putArray("missing_fields").add("query");
            case "wrong-question" -> clarification.put("question", "different question");
            case "wrong-options" -> ((ArrayNode) clarification.get("options")).remove(0);
            case "duplicate-options" -> ((ArrayNode) clarification.get("options")).set(1, clarification.get("options").get(0));
            case "card" -> ((ArrayNode) body.at("/presentation/cards")).add(completed().at("/presentation/cards/0"));
            default -> body.putNull("clarification");
        }
        invalid(() -> validator.validateChat(body, REQUEST, CONVERSATION, context(), OVERVIEW));
    }

    private static ObjectNode completed() {
        ObjectNode body = JSON.createObjectNode().put("schema_version", "0.2").put("mode", "stub")
                .put("request_id", REQUEST.toString()).put("conversation_id", CONVERSATION.toString())
                .put("response_id", UUID.randomUUID().toString()).put("execution_status", "COMPLETED")
                .put("generation_status", "NOT_IMPLEMENTED").putNull("clarification");
        body.set("resolved_context", context("SUP-0034"));
        ObjectNode presentation = body.putObject("presentation").put("status", "READY").put("renderer", "template")
                .put("text", "API 연결 검증용 가상 기업입니다.");
        ObjectNode card = presentation.putArray("cards").addObject().put("type", "supplier_detail");
        card.set("supplier", supplier(0));
        card.putArray("evidence_ids").add("stub-1");
        body.putArray("citations");
        body.putArray("evidence").addObject().put("evidence_id", "stub-1").put("server", "business-mcp")
                .put("tool", "get_supplier").put("supplier_id", "SUP-0034").put("source", "stub").put("executed", false);
        return body;
    }

    private static ObjectNode needsInput(boolean missingSupplier) {
        ObjectNode body = completed().put("execution_status", "NEEDS_INPUT");
        if (missingSupplier) body.set("resolved_context", context());
        ObjectNode presentation = (ObjectNode) body.get("presentation");
        presentation.put("status", "NEEDS_INPUT").put("text", "지원 질문이나 기업을 선택해 주세요.").putArray("cards");
        body.putArray("evidence");
        ObjectNode clarification = body.putObject("clarification").put("question", presentation.get("text").stringValue());
        clarification.putArray("missing_fields").add(missingSupplier ? "context.selected_supplier_ids" : "query");
        clarification.set("options", FIXTURE.get("supported_queries").deepCopy());
        return body;
    }

    private static ObjectNode catalog() {
        return (ObjectNode) JSON.readTree("""
                {"schema_version":"0.2","mode":"stub","data":{"processes":[
                  {"process":"다이캐스팅","materials":["ADC12","ALDC6"]},
                  {"process":"프레스","materials":["SPCC"]}],"regions":["인천","부산","대구"]}}
                """);
    }

    private static ObjectNode filters(String process) {
        return JSON.createObjectNode().put("process", process).put("iatf_required", false).put("limit", 20);
    }

    private static ObjectNode search(int limit, JsonNode... items) {
        ObjectNode data = JSON.createObjectNode().put("returned_count", items.length).put("limit", limit);
        ArrayNode array = data.putArray("items");
        for (JsonNode item : items) array.add(item);
        return envelope(data);
    }

    private static ObjectNode summary(int index, int... capabilities) {
        ObjectNode supplier = supplier(index);
        ObjectNode summary = JSON.createObjectNode();
        for (String field : new String[]{"supplier_id", "name", "region", "iatf16949", "provenance"}) summary.set(field, supplier.get(field));
        ArrayNode matched = summary.putArray("matched_capabilities");
        for (int capability : capabilities) matched.add(supplier.get("capabilities").get(capability));
        return summary;
    }

    private static ObjectNode envelope(JsonNode data) {
        ObjectNode body = JSON.createObjectNode().put("schema_version", "0.2").put("mode", "stub");
        body.set("data", data);
        return body;
    }

    private static ObjectNode context(String... ids) {
        ObjectNode context = JSON.createObjectNode();
        ArrayNode selected = context.putArray("selected_supplier_ids");
        for (String id : ids) selected.add(id);
        return context;
    }

    private static ObjectNode supplier(int index) { return (ObjectNode) FIXTURE.get("suppliers").get(index).deepCopy(); }

    private static JsonNode fixture() {
        try (var stream = SupplierChatResponseValidatorTest.class.getResourceAsStream("/fixtures/supplier-chat.json")) {
            if (stream == null) throw new IllegalStateException("Missing supplier chat fixture");
            return JSON.readTree(stream);
        } catch (IOException exception) {
            throw new IllegalStateException("Cannot load supplier chat fixture", exception);
        }
    }

    private static void invalid(org.junit.jupiter.api.function.Executable action) {
        assertEquals("Invalid supplier chat response", assertThrows(IllegalArgumentException.class, action).getMessage());
    }
}
