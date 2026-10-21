package com.negopilot.backend;

import java.util.UUID;
import java.util.stream.Stream;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.MethodSource;
import org.junit.jupiter.params.provider.ValueSource;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class GenerationRequestValidatorTest {
    private static final JsonMapper JSON = JsonMapper.builder().build();
    private final GenerationRequestValidator validator = new GenerationRequestValidator();

    @Test
    void queryPreservesOriginalTextAndAddsIndependentDefaults() {
        ObjectNode original = JSON.createObjectNode().put("query", " \u00a0이 회사와 다른 업체를 비교해줘\u0085 ");
        var result = validator.validateQuery(original);
        assertThat(result.body().get("query")).isEqualTo(original.get("query"));
        assertThat(result.body().path("request_id").stringValue()).isEqualTo(result.requestId().toString());
        assertThat(result.body().path("history").isArray()).isTrue();
        assertThat(result.body().path("history").isEmpty()).isTrue();
        assertThat(result.body().path("context")).isEqualTo(JSON.readTree("{\"selected_supplier_ids\":[]}"));
        assertThat(original.propertyNames()).containsExactly("query");
        assertThat(validator.validateQuery(original).requestId()).isNotEqualTo(result.requestId());
    }

    @Test
    void queryPreservesHistoryAndUnknownSupplierIdWithoutChangingTheInput() {
        ObjectNode original = (ObjectNode) JSON.readTree("""
                {"request_id":"91FB53C9-F421-4FE9-AF39-780DEFD223C0","query":"질문 원문 ",
                 "history":[{"role":"assistant","content":"  이전 답변  "},{"role":"user","content":"이전 질문"}],
                 "context":{"selected_supplier_ids":["Unknown_Company-42"]}}
                """);
        var result = validator.validateQuery(original);
        assertThat(result.requestId()).isEqualTo(UUID.fromString("91fb53c9-f421-4fe9-af39-780defd223c0"));
        assertThat(result.body().get("history")).isEqualTo(original.get("history"));
        assertThat(result.body().get("context")).isEqualTo(original.get("context"));
        original.putArray("history");
        ((ObjectNode) original.get("context")).putArray("selected_supplier_ids");
        assertThat(result.body().get("history").size()).isEqualTo(2);
        assertThat(result.body().path("context").path("selected_supplier_ids").get(0).stringValue())
                .isEqualTo("Unknown_Company-42");
    }

    @ParameterizedTest
    @ValueSource(strings = {"91fb53c9f4214fe9af39780defd223c0", "{91fb53c9-f421-4fe9-af39-780defd223c0}",
            "urn:uuid:91fb53c9-f421-4fe9-af39-780defd223c0"})
    void queryNormalizesSupportedUuidForms(String id) {
        var result = validator.validateQuery(JSON.createObjectNode().put("query", "q").put("request_id", id));
        assertThat(result.body().get("request_id").stringValue()).isEqualTo("91fb53c9-f421-4fe9-af39-780defd223c0");
    }

    @Test
    void queryAndHistoryUseCodePointLimitsWithoutTrimming() {
        String maximum = "😀".repeat(4000);
        ObjectNode body = JSON.createObjectNode().put("query", maximum);
        var history = body.putArray("history");
        for (int index = 0; index < 20; index++) {
            history.addObject().put("role", index % 2 == 0 ? "user" : "assistant").put("content", maximum);
        }
        assertThat(validator.validateQuery(body).body().get("history")).isEqualTo(history);
        body.put("query", " " + maximum);
        invalid(() -> validator.validateQuery(body));
        body.put("query", maximum);
        ((ObjectNode) history.get(0)).put("content", maximum + "😀");
        invalid(() -> validator.validateQuery(body));
        ((ObjectNode) history.get(0)).put("content", maximum);
        history.addObject().put("role", "user").put("content", "extra");
        invalid(() -> validator.validateQuery(body));
    }

    static Stream<String> invalidQueries() {
        return Stream.of("null", "[]", "{}", "{\"query\":null}", "{\"query\":false}", "{\"query\":\"\"}",
                "{\"query\":\" \\t\\n\\u0085\\u00a0\"}", "{\"query\":\"q\",\"extra\":1}",
                "{\"query\":\"q\",\"request_id\":null}", "{\"query\":\"q\",\"request_id\":1}",
                "{\"query\":\"q\",\"request_id\":\"1-1-1-1-1\"}",
                "{\"query\":\"q\",\"history\":null}", "{\"query\":\"q\",\"history\":{}}",
                "{\"query\":\"q\",\"history\":[null]}", "{\"query\":\"q\",\"history\":[{}]}",
                "{\"query\":\"q\",\"history\":[{\"role\":\"system\",\"content\":\"q\"}]}",
                "{\"query\":\"q\",\"history\":[{\"role\":\"user\",\"content\":null}]}",
                "{\"query\":\"q\",\"history\":[{\"role\":\"user\",\"content\":\" \\u00a0\"}]}",
                "{\"query\":\"q\",\"history\":[{\"role\":\"assistant\",\"content\":\"a\",\"extra\":1}]}");
    }

    @ParameterizedTest
    @MethodSource("invalidQueries")
    void rejectsInvalidDirectQuery(String input) {
        invalid(() -> validator.validateQuery(JSON.readTree(input)));
    }

    @Test
    void preservesAValidRequestIdOnValidationErrors() {
        UUID id = UUID.randomUUID();
        ObjectNode body = JSON.createObjectNode().put("request_id", id.toString()).put("query", "q").putNull("context");
        assertThatThrownBy(() -> validator.validateQuery(body)).isInstanceOfSatisfying(ApiException.class, error -> {
            assertThat(error.code).isEqualTo("VALIDATION_ERROR");
            assertThat(error.requestId).isEqualTo(id);
        });
    }

    @Test
    void conversationRequestsCopyContextAndOnlyMessagesTrimQueries() {
        ObjectNode body = (ObjectNode) JSON.readTree("{\"context\":{\"selected_supplier_ids\":[\"UNKNOWN-42\"]}}");
        ObjectNode selected = validator.create(body);
        body.put("query", " \u00a0질문\u0085 ");
        var input = validator.message(body);
        assertThat(input.query()).isEqualTo("질문");
        assertThat(input.context()).isEqualTo(selected);
        ((ObjectNode) body.get("context")).putArray("selected_supplier_ids");
        assertThat(input.context()).isEqualTo(selected);
        assertThat(selected.path("selected_supplier_ids").get(0).stringValue()).isEqualTo("UNKNOWN-42");
        assertThat(validator.message(JSON.createObjectNode().put("query", "q")).context()).isNull();
        assertThat(validator.create(JSON.readTree("{\"context\":{\"selected_supplier_ids\":[]}}")))
                .isEqualTo(JSON.readTree("{\"selected_supplier_ids\":[]}"));
    }

    @Test
    void messageLimitsCodePointsAfterTrimming() {
        ObjectNode body = JSON.createObjectNode().put("query", " \u00a0" + "😀".repeat(4000) + "\u0085 ");
        assertThat(validator.message(body).query()).isEqualTo("😀".repeat(4000));
        body.put("query", "😀".repeat(4001));
        invalid(() -> validator.message(body));
    }

    static Stream<String> invalidContexts() {
        return Stream.of("null", "[]", "{}", "{\"selected_supplier_ids\":null}",
                "{\"selected_supplier_ids\":\"id\"}", "{\"selected_supplier_ids\":[null]}",
                "{\"selected_supplier_ids\":[1]}", "{\"selected_supplier_ids\":[\"\"]}",
                "{\"selected_supplier_ids\":[\" \" ]}", "{\"selected_supplier_ids\":[\"회사\"]}",
                "{\"selected_supplier_ids\":[\"a/b\"]}", "{\"selected_supplier_ids\":[\"a.b\"]}",
                "{\"selected_supplier_ids\":[\"id\",\"id\"]}", "{\"selected_supplier_ids\":[\"a\",\"b\"]}",
                "{\"selected_supplier_ids\":[],\"facts\":{}}");
    }

    @ParameterizedTest
    @MethodSource("invalidContexts")
    void rejectsInvalidContextOnAllRequests(String context) {
        ObjectNode body = JSON.createObjectNode();
        body.set("context", JSON.readTree(context));
        invalid(() -> validator.create(body));
        body.put("query", "q");
        invalid(() -> validator.message(body));
        invalid(() -> validator.validateQuery(body));
    }

    @Test
    void contextAcceptsOnlyOneIdWithAtMostOneHundredAsciiCharacters() {
        ObjectNode body = JSON.createObjectNode();
        body.putObject("context").putArray("selected_supplier_ids").add("X".repeat(100));
        assertThat(validator.create(body).path("selected_supplier_ids").get(0).stringValue()).hasSize(100);
        ((ObjectNode) body.get("context")).putArray("selected_supplier_ids").add("X".repeat(101));
        invalid(() -> validator.create(body));
        body.put("query", "q");
        invalid(() -> validator.message(body));
        invalid(() -> validator.validateQuery(body));
    }

    @ParameterizedTest
    @ValueSource(strings = {"null", "[]", "{}", "{\"context\":{\"selected_supplier_ids\":[]},\"extra\":1}"})
    void rejectsInvalidCreateBody(String input) {
        invalid(() -> validator.create(JSON.readTree(input)));
    }

    @ParameterizedTest
    @ValueSource(strings = {"null", "[]", "{}", "{\"query\":null}", "{\"query\":1}", "{\"query\":\"\"}",
            "{\"query\":\" \\t\\u0085\\u00a0\"}", "{\"query\":\"q\",\"history\":[]}",
            "{\"query\":\"q\",\"request_id\":\"91fb53c9-f421-4fe9-af39-780defd223c0\"}"})
    void rejectsInvalidConversationMessage(String input) {
        invalid(() -> validator.message(JSON.readTree(input)));
    }

    @Test
    void missingBodiesAreValidationErrors() {
        invalid(() -> validator.validateQuery(null));
        invalid(() -> validator.create(null));
        invalid(() -> validator.message(null));
    }

    @Test
    void conversationIdPreservesCaseForExactLookup() {
        String id = "91FB53C9-F421-4FE9-AF39-780DEFD223C0";
        assertThat(validator.conversationId(id)).isEqualTo(id);
        assertThat(validator.conversationId(id.toLowerCase())).isEqualTo(id.toLowerCase());
    }

    @ParameterizedTest
    @ValueSource(strings = {"", "invalid", "1-1-1-1-1", "91fb53c9f4214fe9af39780defd223c0",
            "{91fb53c9-f421-4fe9-af39-780defd223c0}", "urn:uuid:91fb53c9-f421-4fe9-af39-780defd223c0",
            " 91fb53c9-f421-4fe9-af39-780defd223c0 "})
    void rejectsNonstandardConversationIds(String input) {
        invalid(() -> validator.conversationId(input));
    }

    @Test
    void rejectsMissingConversationId() {
        invalid(() -> validator.conversationId(null));
    }

    private static void invalid(Runnable action) {
        assertThatThrownBy(action::run).isInstanceOfSatisfying(ApiException.class, error -> {
            assertThat(error.status.value()).isEqualTo(400);
            assertThat(error.code).isEqualTo("VALIDATION_ERROR");
            assertThat(error.retryable).isFalse();
        });
    }
}
