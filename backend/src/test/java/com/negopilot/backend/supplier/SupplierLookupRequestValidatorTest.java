package com.negopilot.backend.supplier;

import com.negopilot.backend.common.api.ApiException;

import java.util.HashMap;
import java.util.Map;
import java.util.stream.Stream;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.MethodSource;
import org.junit.jupiter.params.provider.ValueSource;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class SupplierLookupRequestValidatorTest {
    private static final JsonMapper JSON = JsonMapper.builder().build();
    private final SupplierLookupRequestValidator validator = new SupplierLookupRequestValidator();

    @Test
    void searchTrimsFiltersAndAppliesTypedDefaults() {
        ObjectNode result = validator.search(Map.of("process", new String[]{" \t다이캐스팅\u00a0"}));
        assertThat(result).isEqualTo(JSON.readTree("{\"process\":\"다이캐스팅\",\"iatf_required\":false,\"iso14001_required\":false,\"page\":0,\"size\":20,\"sort\":\"name\",\"direction\":\"asc\"}"));
    }

    @Test
    void searchAcceptsOptionalFiltersAndMaximumLimit() {
        ObjectNode result = validator.search(Map.of("process", new String[]{"다이캐스팅"},
                "material", new String[]{" ADC12 "}, "region", new String[]{"\u0085인천\u0085"},
                "iatf_required", new String[]{"true"}, "limit", new String[]{"100"}));
        assertThat(result).isEqualTo(JSON.readTree("""
                {"process":"다이캐스팅","material":"ADC12","region":"인천","iatf_required":true,"iso14001_required":false,"page":0,"size":100,"sort":"name","direction":"asc"}
                """));
    }

    @Test
    void searchAcceptsMinimumMonthlyCapacityRange() {
        ObjectNode result = validator.search(Map.of("process", new String[]{"다이캐스팅"},
                "min_monthly_capacity", new String[]{"50000"}));
        assertThat(result).isEqualTo(JSON.readTree("""
                {"process":"다이캐스팅","iatf_required":false,"iso14001_required":false,"min_monthly_capacity":50000,"page":0,"size":20,"sort":"name","direction":"asc"}
                """));
        assertThat(validator.search(Map.of("process", new String[]{"a"}, "min_monthly_capacity", new String[]{"1"}))
                .path("min_monthly_capacity").intValue()).isEqualTo(1);
        assertThat(validator.search(Map.of("process", new String[]{"a"}, "min_monthly_capacity", new String[]{"10000000"}))
                .path("min_monthly_capacity").intValue()).isEqualTo(10_000_000);
    }

    @Test
    void searchCountsUnicodeCodePointsAfterTrimming() {
        ObjectNode result = validator.search(Map.of("process", new String[]{" " + "😀".repeat(100) + " "}));
        assertThat(result.path("process").stringValue()).isEqualTo("😀".repeat(100));
        assertInvalid(() -> validator.search(Map.of("process", new String[]{"😀".repeat(101)})));
    }

    @Test
    void allSuppliersAndMaterialOnlyAndBlankQueryAreAccepted() {
        var all = validator.search(Map.of());
        assertThat(all.path("page").intValue()).isZero();
        assertThat(all.path("size").intValue()).isEqualTo(20);
        assertThat(all.path("sort").stringValue()).isEqualTo("name");
        assertThat(validator.search(Map.of("material", new String[]{" AL6061 "})).path("material").stringValue()).isEqualTo("AL6061");
        assertThat(validator.search(Map.of("q", new String[]{" \u00a0"}))).isEqualTo(all);
        assertThat(validator.search(Map.of("q", new String[]{" %_SUP-01 "})).path("q").stringValue()).isEqualTo("%_SUP-01");
    }

    @Test
    void paginationAndPriceSortingRequireAnUnambiguousRequest() {
        var result = validator.search(Map.of("page", new String[]{"1"}, "size", new String[]{"25"},
                "sort", new String[]{"latest_quote_price"}, "direction", new String[]{"desc"},
                "process", new String[]{"CNC가공"}, "material", new String[]{"AL6061"}));
        assertThat(result.path("page").intValue()).isEqualTo(1);
        assertThat(result.path("size").intValue()).isEqualTo(25);
        assertInvalid(() -> validator.search(Map.of("limit", new String[]{"20"}, "page", new String[]{"0"})));
        assertInvalid(() -> validator.search(Map.of("limit", new String[]{"20"}, "size", new String[]{"20"})));
        assertInvalid(() -> validator.search(Map.of("sort", new String[]{"latest_quote_price"})));
        assertInvalid(() -> validator.search(Map.of("sort", new String[]{"latest_quote_price"}, "process", new String[]{"CNC가공"})));
    }

    @Test
    void quotePagingUsesDateDescendingAndSupportsIndependentFilters() {
        assertThat(validator.quotes(Map.of())).isEqualTo(JSON.readTree("{\"page\":0,\"size\":20,\"sort\":\"quoted_at\",\"direction\":\"desc\"}"));
        assertThat(validator.quotes(Map.of("material", new String[]{"AL6061"})).path("material").stringValue()).isEqualTo("AL6061");
        assertInvalid(() -> validator.quotes(Map.of("q", new String[]{"ABC"})));
        assertInvalid(() -> validator.quotes(Map.of("limit", new String[]{"20"})));
        assertInvalid(() -> validator.quotes(Map.of("sort", new String[]{"unit_price_krw"})));
        assertThat(validator.quotes(Map.of("sort", new String[]{"unit_price_krw"},
                "process", new String[]{"CNC가공"}, "material", new String[]{"AL6061"})).path("sort").stringValue()).isEqualTo("unit_price_krw");
    }

    static Stream<Map<String, String[]>> invalidSearches() {
        return Stream.of(
                Map.of("q", new String[]{"x".repeat(101)}),
                Map.of("page", new String[]{"1000001"}),
                Map.of("page", new String[]{"-1"}),
                Map.of("page", new String[]{"1.0"}),
                Map.of("size", new String[]{"0"}),
                Map.of("size", new String[]{"101"}),
                Map.of("sort", new String[]{"name; DROP TABLE suppliers"}),
                Map.of("direction", new String[]{"ASC"}),
                Map.of("process", new String[]{""}),
                Map.of("process", new String[]{"\t \u00a0\u0085"}),
                Map.of("process", new String[]{"a", "b"}),
                Map.of("process", new String[]{}),
                Map.of("process", new String[]{null}),
                Map.of("process", new String[]{"a"}, "material", new String[]{""}),
                Map.of("process", new String[]{"a"}, "material", new String[]{"a", "b"}),
                Map.of("process", new String[]{"a"}, "region", new String[]{" "}),
                Map.of("process", new String[]{"a"}, "region", new String[]{"a", "b"}),
                Map.of("process", new String[]{"a"}, "region", new String[]{"r".repeat(101)}),
                Map.of("process", new String[]{"a"}, "iatf_required", new String[]{"True"}),
                Map.of("process", new String[]{"a"}, "iatf_required", new String[]{"1"}),
                Map.of("process", new String[]{"a"}, "iatf_required", new String[]{" true "}),
                Map.of("process", new String[]{"a"}, "iatf_required", new String[]{"false", "false"}),
                Map.of("process", new String[]{"a"}, "iso14001_required", new String[]{"True"}),
                Map.of("process", new String[]{"a"}, "iso14001_required", new String[]{"1"}),
                Map.of("process", new String[]{"a"}, "limit", new String[]{"0"}),
                Map.of("process", new String[]{"a"}, "limit", new String[]{"101"}),
                Map.of("process", new String[]{"a"}, "limit", new String[]{"-1"}),
                Map.of("process", new String[]{"a"}, "limit", new String[]{"1.0"}),
                Map.of("process", new String[]{"a"}, "limit", new String[]{"abc"}),
                Map.of("process", new String[]{"a"}, "limit", new String[]{"99999999999999999"}),
                Map.of("process", new String[]{"a"}, "limit", new String[]{"1", "2"}),
                Map.of("process", new String[]{"a"}, "min_monthly_capacity", new String[]{"0"}),
                Map.of("process", new String[]{"a"}, "min_monthly_capacity", new String[]{"10000001"}),
                Map.of("process", new String[]{"a"}, "min_monthly_capacity", new String[]{"-1"}),
                Map.of("process", new String[]{"a"}, "min_monthly_capacity", new String[]{"1.5"}),
                Map.of("process", new String[]{"a"}, "min_monthly_capacity", new String[]{""}),
                Map.of("process", new String[]{"a"}, "min_monthly_capacity", new String[]{"1", "2"}),
                Map.of("process", new String[]{"a"}, "extra", new String[]{"x"})
        );
    }

    @ParameterizedTest
    @MethodSource("invalidSearches")
    void rejectsInvalidSearchParameters(Map<String, String[]> input) {
        assertInvalid(() -> validator.search(input));
    }

    @Test
    void rejectsNullSearchMapOrValue() {
        assertInvalid(() -> validator.search(null));
        Map<String, String[]> input = new HashMap<>();
        input.put("process", null);
        assertInvalid(() -> validator.search(input));
    }

    @ParameterizedTest
    @ValueSource(strings = {"SUP-0001", "SUP-10000", "SUP-123456789012345678"})
    void supplierIdAcceptsLookupFormat(String input) {
        assertThat(validator.supplierId(input)).isEqualTo(input);
    }

    @ParameterizedTest
    @ValueSource(strings = {"", "SUP-001", "SUP-1234567890123456789", "sup-0001", "SUP-0001 ", " SUP-0001",
            "SUP_0001", "0001", "대성정밀"})
    void supplierIdRejectsOtherFormats(String input) {
        assertInvalid(() -> validator.supplierId(input));
    }

    @Test
    void rejectsNullSupplierId() {
        assertInvalid(() -> validator.supplierId(null));
    }

    private static void assertInvalid(Runnable action) {
        assertThatThrownBy(action::run).isInstanceOfSatisfying(ApiException.class, failure -> {
            assertThat(failure.status.value()).isEqualTo(400);
            assertThat(failure.code).isEqualTo("VALIDATION_ERROR");
            assertThat(failure.retryable).isFalse();
            assertThat(failure.requestId).isNull();
        });
    }
}
