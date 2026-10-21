package com.negopilot.backend;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

import static org.assertj.core.api.Assertions.assertThatCode;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class SupplierLookupResponseValidatorTest {
    private static final JsonMapper JSON = JsonMapper.builder().build();
    private final SupplierLookupResponseValidator validator = new SupplierLookupResponseValidator();

    @Test
    void acceptsManualSupplierWithoutQualityOrFinancials() {
        assertThatCode(() -> validator.validateDetail(detail(), "SUP-10000")).doesNotThrowAnyException();
    }

    @Test
    void requiresFinancialSummaryFromActualFinancialRows() {
        ObjectNode response = detail();
        var financials = ((ObjectNode) response.get("data")).putArray("financials");
        financials.addObject().put("year", 2024).put("revenue_krw", 500_000_000_001L)
                .put("operating_profit_krw", -1).putNull("debt_ratio").put("source", "DART");
        financials.addObject().put("year", 2025).put("revenue_krw", 100_000_000_001L)
                .put("operating_profit_krw", 1).put("debt_ratio", 2.5).put("source", "MOCK");
        ((ObjectNode) response.at("/data/provenance")).put("financials", "MIXED").put("operations", "MIXED");
        assertThatCode(() -> validator.validateDetail(response, "SUP-10000")).doesNotThrowAnyException();
        ((ObjectNode) response.at("/data/provenance")).put("financials", "DART");
        assertThatThrownBy(() -> validator.validateDetail(response, "SUP-10000"))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @ParameterizedTest
    @ValueSource(strings = {"DART", "MOCK", "MIXED", "MANUAL"})
    void emptyFinancialsRequireUnknown(String summary) {
        ObjectNode response = detail();
        ((ObjectNode) response.at("/data/provenance")).put("financials", summary);
        assertThatThrownBy(() -> validator.validateDetail(response, "SUP-10000"))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @ParameterizedTest
    @ValueSource(strings = {"SUP-1", "A-1", "SUP-1234567890123456789"})
    void rejectsMalformedIdEvenWhenItMatchesRequestedId(String id) {
        ObjectNode response = detail();
        ((ObjectNode) response.get("data")).put("supplier_id", id);
        assertThatThrownBy(() -> validator.validateDetail(response, id))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void validatesManualSearchSourceAndCanonicalIds() {
        ObjectNode response = JSON.createObjectNode().put("schema_version", "0.3").put("mode", "live");
        ObjectNode data = response.putObject("data").put("returned_count", 1).put("limit", 20);
        ObjectNode item = data.putArray("items").addObject().put("supplier_id", "SUP-10000")
                .put("name", "수동기업").put("region", "부산").put("iatf16949", false).put("iso14001", false);
        item.putArray("matched_capabilities").addObject().put("process", "프레스")
                .put("material", "SPCC").put("monthly_capacity", 1000);
        item.putObject("provenance").put("profile", "MANUAL").put("financials", "UNKNOWN").put("operations", "MANUAL");
        ObjectNode filters = JSON.createObjectNode().put("process", "프레스");
        assertThatCode(() -> validator.validateSearch(response, filters)).doesNotThrowAnyException();
        item.put("supplier_id", "1");
        assertThatThrownBy(() -> validator.validateSearch(response, filters))
                .isInstanceOf(IllegalArgumentException.class);
    }

    private static ObjectNode detail() {
        return (ObjectNode) JSON.readTree("""
                {"schema_version":"0.3","mode":"live","data":{
                  "supplier_id":"SUP-10000","name":"수동기업","region":"부산",
                  "iatf16949":false,"iso14001":false,
                  "capabilities":[{"process":"프레스","material":"SPCC","monthly_capacity":1000}],
                  "quality":{"defect_ppm":null,"on_time_delivery_rate":null},
                  "financials":[],"recent_quotes":[],"existing_contract":null,
                  "provenance":{"profile":"MANUAL","financials":"UNKNOWN","operations":"MANUAL"}
                }}
                """);
    }
}
