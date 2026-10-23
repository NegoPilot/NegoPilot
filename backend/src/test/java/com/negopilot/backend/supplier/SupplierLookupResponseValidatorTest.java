package com.negopilot.backend.supplier;

import java.util.Map;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

import static org.assertj.core.api.Assertions.assertThatCode;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class SupplierLookupResponseValidatorTest {
    private static final JsonMapper JSON = JsonMapper.builder().build();
    private final SupplierLookupResponseValidator validator = new SupplierLookupResponseValidator();
    private final SupplierLookupRequestValidator requests = new SupplierLookupRequestValidator();

    @Test
    void acceptsManualSupplierWithoutCapabilitiesQualityOrFinancials() {
        assertThatCode(() -> validator.validateDetail(detail(), "SUP-10000")).doesNotThrowAnyException();
        assertThatCode(() -> validator.validateSearch(search(), requests.search(Map.of()))).doesNotThrowAnyException();
    }

    @Test
    void acceptsSupplierFoundedBefore1900InSearchAndDetail() {
        ObjectNode search = search();
        ((ObjectNode) search.at("/data/items/0")).put("founded_year", 1899);
        assertThatCode(() -> validator.validateSearch(search, requests.search(Map.of()))).doesNotThrowAnyException();
        ObjectNode detail = detail();
        ((ObjectNode) detail.get("data")).put("founded_year", 1899);
        assertThatCode(() -> validator.validateDetail(detail, "SUP-10000")).doesNotThrowAnyException();
    }

    @Test
    void requiresFinancialSummaryFromActualFinancialRows() {
        ObjectNode response = detail();
        var financials = ((ObjectNode) response.get("data")).putArray("financials");
        financials.add(financial(2024, "DART"));
        financials.add(financial(2025, "MOCK"));
        ((ObjectNode) response.at("/data/provenance")).put("financials", "MIXED").put("operations", "MIXED");
        assertThatCode(() -> validator.validateDetail(response, "SUP-10000")).doesNotThrowAnyException();
        ((ObjectNode) response.at("/data/provenance")).put("financials", "DART");
        assertThatThrownBy(() -> validator.validateDetail(response, "SUP-10000")).isInstanceOf(IllegalArgumentException.class);
    }

    @ParameterizedTest
    @ValueSource(strings = {"DART", "MOCK", "MIXED", "MANUAL"})
    void emptyFinancialsRequireUnknown(String summary) {
        ObjectNode response = detail();
        ((ObjectNode) response.at("/data/provenance")).put("financials", summary);
        assertThatThrownBy(() -> validator.validateDetail(response, "SUP-10000")).isInstanceOf(IllegalArgumentException.class);
    }

    @ParameterizedTest
    @ValueSource(strings = {"SUP-1", "A-1", "SUP-1234567890123456789"})
    void rejectsMalformedIdEvenWhenItMatchesRequestedId(String id) {
        ObjectNode response = detail();
        ((ObjectNode) response.get("data")).put("supplier_id", id);
        assertThatThrownBy(() -> validator.validateDetail(response, id)).isInstanceOf(IllegalArgumentException.class);
    }

    @ParameterizedTest
    @ValueSource(strings = {"page", "size", "total", "total_pages", "returned_count", "sort", "direction"})
    void rejectsInconsistentPaginationOrSortIntent(String field) {
        ObjectNode response = search();
        var data = (ObjectNode) response.get("data");
        if (field.equals("sort")) data.put(field, "region");
        else if (field.equals("direction")) data.put(field, "desc");
        else data.put(field, 100);
        assertInvalidSearch(response, Map.of());
    }

    @Test
    void acceptsOutOfRangePageOnlyWithEmptyItemsAndAccurateTotal() {
        ObjectNode response = search();
        var data = (ObjectNode) response.get("data");
        data.put("page", 2).put("total", 1).put("returned_count", 0).putArray("items");
        assertThatCode(() -> validator.validateSearch(response, requests.search(Map.of("page", new String[]{"2"}))))
                .doesNotThrowAnyException();
    }

    @Test
    void validatesLiteralCaseInsensitiveQueryAndOptionalCapabilityFilters() {
        ObjectNode response = search();
        var item = (ObjectNode) response.at("/data/items/0");
        item.put("name", "Alpha%_정밀");
        assertThatCode(() -> validator.validateSearch(response, requests.search(Map.of("q", new String[]{"ALPHA%_"}))))
                .doesNotThrowAnyException();
        assertInvalidSearch(response, Map.of("q", new String[]{"AlphaX"}));
        assertInvalidSearch(response, Map.of("process", new String[]{"프레스"}));
        item.putArray("matched_capabilities").addObject().put("process", "프레스").put("material", "SPCC").put("monthly_capacity", 1000);
        item.put("max_monthly_capacity", 1000);
        assertThatCode(() -> validator.validateSearch(response, requests.search(Map.of("material", new String[]{"SPCC"}))))
                .doesNotThrowAnyException();
        assertInvalidSearch(response, Map.of("min_monthly_capacity", new String[]{"1001"}));
        assertInvalidSearch(response, Map.of("material", new String[]{"ADC12"}));
    }

    @Test
    void checksNameOrderCaseInsensitiveTiesAndDuplicateIds() {
        ObjectNode response = search();
        var first = (ObjectNode) response.at("/data/items/0");
        first.put("name", "ABC");
        ObjectNode second = first.deepCopy().put("supplier_id", "SUP-10001").put("name", "abc");
        ((ArrayNode) response.at("/data/items")).add(second);
        ((ObjectNode) response.get("data")).put("total", 2).put("returned_count", 2);
        assertThatCode(() -> validator.validateSearch(response, requests.search(Map.of()))).doesNotThrowAnyException();
        second.put("supplier_id", "SUP-10000");
        assertInvalidSearch(response, Map.of());
        second.put("supplier_id", "SUP-10001").put("name", "AAA");
        assertInvalidSearch(response, Map.of());
    }

    @Test
    void nullsStayLastEvenWhenNumericSortIsDescending() {
        ObjectNode response = search();
        var data = (ObjectNode) response.get("data");
        data.put("sort", "founded_year").put("direction", "desc").put("total", 2).put("returned_count", 2);
        var first = (ObjectNode) response.at("/data/items/0");
        first.put("founded_year", 2001);
        ((ArrayNode) response.at("/data/items")).add(first.deepCopy().put("supplier_id", "SUP-10001").putNull("founded_year"));
        var filters = Map.of("sort", new String[]{"founded_year"}, "direction", new String[]{"desc"});
        assertThatCode(() -> validator.validateSearch(response, requests.search(filters))).doesNotThrowAnyException();
        var items = (ArrayNode) response.at("/data/items");
        items.add(items.remove(0));
        assertInvalidSearch(response, filters);
    }

    @ParameterizedTest
    @ValueSource(strings = {"quality-rate", "contract-ratio", "timestamp", "capacity", "quote-date"})
    void rejectsMalformedExpandedFields(String field) {
        ObjectNode response = search();
        var item = (ObjectNode) response.at("/data/items/0");
        switch (field) {
            case "quality-rate" -> ((ObjectNode) item.get("quality")).put("on_time_delivery_rate", 101);
            case "contract-ratio" -> item.putObject("existing_contract").put("payment_days", 60).put("price_linkage", true)
                    .put("raw_material_ratio", 40).put("auto_renewal", false);
            case "timestamp" -> item.put("updated_at", "2026-13-01");
            case "capacity" -> item.put("max_monthly_capacity", 20);
            default -> item.set("latest_quote", quote(1).put("quoted_at", "2026-02-30"));
        }
        assertInvalidSearch(response, Map.of());
    }

    @Test
    void quotePagesValidateTargetFiltersAndStableDateOrder() {
        var response = JSON.createObjectNode().put("schema_version", "0.4").put("mode", "live");
        var data = response.putObject("data").put("supplier_id", "SUP-10000").put("page", 0).put("size", 20)
                .put("total", 2).put("total_pages", 1).put("returned_count", 2).put("sort", "quoted_at").put("direction", "desc");
        data.putArray("items").add(quote(2)).add(quote(1));
        var filters = requests.quotes(Map.of("material", new String[]{"SPCC"}));
        assertThatCode(() -> validator.validateQuotes(response, "SUP-10000", filters)).doesNotThrowAnyException();
        assertThatThrownBy(() -> validator.validateQuotes(response, "SUP-10001", filters)).isInstanceOf(IllegalArgumentException.class);
        ((ObjectNode) response.at("/data/items/0")).put("material", "OTHER");
        assertThatThrownBy(() -> validator.validateQuotes(response, "SUP-10000", filters)).isInstanceOf(IllegalArgumentException.class);
        ((ObjectNode) response.at("/data/items/0")).put("material", "SPCC").put("quote_id", 1);
        assertThatThrownBy(() -> validator.validateQuotes(response, "SUP-10000", filters)).isInstanceOf(IllegalArgumentException.class);
    }

    private void assertInvalidSearch(ObjectNode response, Map<String, String[]> filters) {
        assertThatThrownBy(() -> validator.validateSearch(response, requests.search(filters))).isInstanceOf(IllegalArgumentException.class);
    }

    static ObjectNode quote(int id) {
        return JSON.createObjectNode().put("quote_id", id).put("process", "프레스").put("material", "SPCC")
                .put("unit_price_krw", 1200).put("quoted_at", "2026-10-20");
    }

    static ObjectNode financial(int year, String source) {
        return JSON.createObjectNode().put("year", year).put("fs_div", "CFS").put("revenue_krw", 500_000_000_001L)
                .put("operating_profit_krw", -1).putNull("debt_ratio").put("source", source).putNull("net_income_krw")
                .putNull("total_assets_krw").putNull("total_liabilities_krw").putNull("total_equity_krw");
    }

    static ObjectNode detail() {
        return (ObjectNode) JSON.readTree("""
                {"schema_version":"0.4","mode":"live","data":{
                  "supplier_id":"SUP-10000","name":"수동기업","region":"부산","iatf16949":false,"iso14001":false,
                  "founded_year":null,"employees":null,"corp_code":null,"stock_code":null,"induty_code":null,
                  "biz_no_masked":null,"created_at":null,"updated_at":null,"capabilities":[],
                  "quality":{"defect_ppm":null,"on_time_delivery_rate":null,"audit_grade":null,"years_dealing":null},
                  "financials":[],"recent_quotes":[],"existing_contract":null,
                  "provenance":{"profile":"MANUAL","financials":"UNKNOWN","operations":"MANUAL"}
                }}
                """);
    }

    static ObjectNode search() {
        ObjectNode response = JSON.createObjectNode().put("schema_version", "0.4").put("mode", "live");
        var data = response.putObject("data").put("page", 0).put("size", 20).put("returned_count", 1)
                .put("total", 1).put("total_pages", 1).put("sort", "name").put("direction", "asc");
        ObjectNode item = ((ObjectNode) detail().get("data")).deepCopy();
        for (String key : new String[]{"financials", "recent_quotes", "capabilities", "corp_code", "stock_code",
                "induty_code", "biz_no_masked", "created_at"}) item.remove(key);
        item.putNull("latest_quote").putNull("max_monthly_capacity").putArray("matched_capabilities");
        data.putArray("items").add(item);
        return response;
    }
}
