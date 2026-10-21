package com.negopilot.backend;

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

class SupplierChatRequestValidatorTest {
    private static final JsonMapper JSON = JsonMapper.builder().build();
    private final SupplierChatRequestValidator validator = new SupplierChatRequestValidator();

    @Test
    void searchTrimsFiltersAndAppliesTypedDefaults() {
        ObjectNode result = validator.search(Map.of("process", new String[]{" \t다이캐스팅\u00a0"}));
        assertThat(result).isEqualTo(JSON.readTree("{\"process\":\"다이캐스팅\",\"iatf_required\":false,\"limit\":20}"));
    }

    @Test
    void searchAcceptsOptionalFiltersAndMaximumLimit() {
        ObjectNode result = validator.search(Map.of("process", new String[]{"다이캐스팅"},
                "material", new String[]{" ADC12 "}, "region", new String[]{"\u0085인천\u0085"},
                "iatf_required", new String[]{"true"}, "limit", new String[]{"100"}));
        assertThat(result).isEqualTo(JSON.readTree("""
                {"process":"다이캐스팅","material":"ADC12","region":"인천","iatf_required":true,"limit":100}
                """));
    }

    @Test
    void searchAcceptsMinimumMonthlyCapacityRange() {
        ObjectNode result = validator.search(Map.of("process", new String[]{"다이캐스팅"},
                "min_monthly_capacity", new String[]{"50000"}));
        assertThat(result).isEqualTo(JSON.readTree("""
                {"process":"다이캐스팅","iatf_required":false,"min_monthly_capacity":50000,"limit":20}
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

    static Stream<Map<String, String[]>> invalidSearches() {
        return Stream.of(
                Map.<String, String[]>of(),
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

    @Test
    void createPreservesSupplierIdCaseAndCopiesContext() {
        ObjectNode input = (ObjectNode) JSON.readTree("{\"context\":{\"selected_supplier_ids\":[\"sup-0034\"]}}");
        ObjectNode context = validator.create(input);
        assertThat(context.path("selected_supplier_ids").get(0).stringValue()).isEqualTo("sup-0034");
        ((ObjectNode) input.path("context")).putArray("selected_supplier_ids");
        assertThat(context.path("selected_supplier_ids").size()).isEqualTo(1);
    }

    @Test
    void createAcceptsUnselectedSupplierContext() {
        assertThat(validator.create(JSON.readTree("{\"context\":{\"selected_supplier_ids\":[]}}")))
                .isEqualTo(JSON.readTree("{\"selected_supplier_ids\":[]}"));
    }

    @Test
    void contextLimitsSupplierIdToOneHundredAsciiCharacters() {
        ObjectNode body = JSON.createObjectNode();
        body.putObject("context").putArray("selected_supplier_ids").add("a".repeat(100));
        assertThat(validator.create(body).path("selected_supplier_ids").get(0).stringValue()).isEqualTo("a".repeat(100));
        ((ObjectNode) body.get("context")).putArray("selected_supplier_ids").add("a".repeat(101));
        assertInvalid(() -> validator.create(body));
        body.put("query", "q");
        assertInvalid(() -> validator.message(body));
        body.remove("query");
        ((ObjectNode) body.get("context")).putArray("selected_supplier_ids").add(" " + "a".repeat(100));
        assertInvalid(() -> validator.create(body));
    }

    @ParameterizedTest
    @ValueSource(strings = {"a", "0", "_", "-", "SUP-0034", "sup_0034-UNKNOWN"})
    void supplierIdsPreserveCaseAndAllowUnknownValidIds(String input) {
        assertThat(validator.supplierId(input)).isEqualTo(input);
        ObjectNode body = JSON.createObjectNode();
        body.putObject("context").putArray("selected_supplier_ids").add(input);
        assertThat(validator.create(body).path("selected_supplier_ids").get(0).stringValue()).isEqualTo(input);
        body.put("query", "q");
        assertThat(validator.message(body).context().path("selected_supplier_ids").get(0).stringValue()).isEqualTo(input);
    }

    @Test
    void supplierPathIdLimitsAsciiLengthWithoutTrimming() {
        assertThat(validator.supplierId("a".repeat(100))).isEqualTo("a".repeat(100));
        assertInvalid(() -> validator.supplierId("a".repeat(101)));
        assertInvalid(() -> validator.supplierId(" " + "a".repeat(100)));
    }

    @ParameterizedTest
    @ValueSource(strings = {"", " ", "\t\n", "\u00a0\u0085", "foo/bar", ".", "..", "한글", "😀",
            "SUP.0034", "SUP-0034 ", " SUP-0034", "SUP%2F0034", "SUP+0034"})
    void rejectsInvalidSupplierIdsAtEveryBoundary(String input) {
        assertInvalid(() -> validator.supplierId(input));
        ObjectNode body = JSON.createObjectNode();
        body.putObject("context").putArray("selected_supplier_ids").add(input);
        assertInvalid(() -> validator.create(body));
        body.put("query", "q");
        assertInvalid(() -> validator.message(body));
    }

    @Test
    void rejectsNullSupplierPathId() {
        assertInvalid(() -> validator.supplierId(null));
    }

    @ParameterizedTest
    @ValueSource(strings = {"null", "[]", "{}", "{\"context\":null}", "{\"context\":[]}", "{\"context\":{}}",
            "{\"context\":{\"selected_supplier_ids\":null}}", "{\"context\":{\"selected_supplier_ids\":\"SUP-0034\"}}",
            "{\"context\":{\"selected_supplier_ids\":[\"SUP-0034\",\"SUP-0041\"]}}",
            "{\"context\":{\"selected_supplier_ids\":[\"SUP-0034\",\"SUP-0034\"]}}",
            "{\"context\":{\"selected_supplier_ids\":[\"\"]}}", "{\"context\":{\"selected_supplier_ids\":[\" \\u00a0\"]}}",
            "{\"context\":{\"selected_supplier_ids\":[12]}}", "{\"context\":{\"selected_supplier_ids\":[null]}}",
            "{\"context\":{\"selected_supplier_ids\":[],\"extra\":1}}", "{\"context\":{\"selected_supplier_ids\":[]},\"extra\":1}"})
    void rejectsInvalidCreateBody(String input) {
        assertInvalid(() -> validator.create(JSON.readTree(input)));
    }

    @Test
    void messageTrimsQueryAndPreservesContextOmission() {
        var message = validator.message(JSON.readTree("{\"query\":\" \\u00a0이 회사에 대해 알려줘\\u0085 \"}"));
        assertThat(message.query()).isEqualTo("이 회사에 대해 알려줘");
        assertThat(message.context()).isNull();
    }

    @Test
    void messageCopiesExplicitContext() {
        ObjectNode body = (ObjectNode) JSON.readTree("{\"query\":\"q\",\"context\":{\"selected_supplier_ids\":[\"SUP-0034\"]}}");
        var message = validator.message(body);
        assertThat(message.context()).isEqualTo(body.get("context"));
        ((ObjectNode) body.get("context")).putArray("selected_supplier_ids");
        assertThat(message.context().path("selected_supplier_ids").size()).isEqualTo(1);
    }

    @Test
    void messageCountsUnicodeCodePointsAfterTrimming() {
        var message = validator.message(JSON.createObjectNode().put("query", " " + "😀".repeat(4000) + " "));
        assertThat(message.query()).isEqualTo("😀".repeat(4000));
        assertInvalid(() -> validator.message(JSON.createObjectNode().put("query", "😀".repeat(4001))));
    }

    @ParameterizedTest
    @ValueSource(strings = {"null", "[]", "{}", "{\"query\":null}", "{\"query\":42}", "{\"query\":\"\"}",
            "{\"query\":\" \\t\\n\\u00a0\\u0085\"}", "{\"query\":\"q\",\"history\":[]}",
            "{\"query\":\"q\",\"request_id\":\"91fb53c9-f421-4fe9-af39-780defd223c0\"}",
            "{\"query\":\"q\",\"context\":null}", "{\"query\":\"q\",\"context\":{}}",
            "{\"query\":\"q\",\"context\":{\"selected_supplier_ids\":[\"SUP-0034\",\"SUP-0041\"]}}"})
    void rejectsInvalidMessageBody(String input) {
        assertInvalid(() -> validator.message(JSON.readTree(input)));
    }

    @Test
    void rejectsMissingBodies() {
        assertInvalid(() -> validator.create(null));
        assertInvalid(() -> validator.message(null));
    }

    @Test
    void acceptsCanonicalConversationId() {
        String id = "91fb53c9-f421-4fe9-af39-780defd223c0";
        assertThat(validator.conversationId(id)).isEqualTo(id);
    }

    @Test
    void preservesConversationIdCaseForExactLookup() {
        String id = "91FB53C9-F421-4FE9-AF39-780DEFD223C0";
        assertThat(validator.conversationId(id)).isEqualTo(id);
    }

    @ParameterizedTest
    @ValueSource(strings = {"", "not-a-uuid", "1-1-1-1-1",
            "91fb53c9f4214fe9af39780defd223c0", "{91fb53c9-f421-4fe9-af39-780defd223c0}",
            "urn:uuid:91fb53c9-f421-4fe9-af39-780defd223c0", " 91fb53c9-f421-4fe9-af39-780defd223c0 "})
    void rejectsNoncanonicalConversationIds(String input) {
        assertInvalid(() -> validator.conversationId(input));
    }

    @Test
    void rejectsNullConversationId() {
        assertInvalid(() -> validator.conversationId(null));
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
