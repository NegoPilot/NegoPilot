package com.negopilot.backend;

import java.util.UUID;
import java.util.function.Consumer;
import java.util.stream.Stream;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.EmptySource;
import org.junit.jupiter.params.provider.MethodSource;
import org.junit.jupiter.params.provider.ValueSource;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

class GenerationResponseValidatorTest {
    private static final UUID REQUEST_ID = UUID.fromString("91fb53c9-f421-4fe9-af39-780defd223c0");
    private static final JsonMapper MAPPER = JsonMapper.builder().build();
    private static final JsonNode CONTEXT = MAPPER.readTree("{\"selected_supplier_ids\":[\"supplier_1\"]}");
    private final GenerationResponseValidator validator = new GenerationResponseValidator();

    @ParameterizedTest
    @ValueSource(strings = {"answer", "refusal", "no_result"})
    void mapsCompletedKindsWithoutApplyingV1RoutingStatus(String kind) {
        ObjectNode response = completed();
        answer(response).put("kind", kind);
        assertValid(response);
    }

    @Test
    void acceptsGroundedClarificationWithoutCitations() {
        ObjectNode response = completed();
        response.put("execution_status", "NEEDS_INPUT");
        answer(response).put("kind", "clarify");
        answer(response).put("text", "어느 기업의 조건을 확인할까요?");
        answer(response).set("options", MAPPER.readTree("[\"기업 A\",\"기업 B\"]"));
        answer(response).putArray("evidence");
        answer(response).putArray("citations");
        answer(response).putNull("verification");
        decomposition(response).put("status", "clarify");
        decomposition(response).putArray("sub_queries");
        decomposition(response).set("clarification", MAPPER.readTree("""
                {"reason":"기업 미지정","question":"어느 기업의 조건을 확인할까요?","options":["기업 A","기업 B"]}
                """));
        notRouted(response);
        assertValid(response);
    }

    @ParameterizedTest
    @ValueSource(strings = {"error", "missing_args"})
    void acceptsPartialRoutingFailureWhenFinalAnswerCompleted(String routingStatus) {
        ObjectNode response = completed();
        ObjectNode secondQuery = (ObjectNode) decomposition(response).get("sub_queries").get(0).deepCopy();
        secondQuery.put("id", "q2");
        ((ArrayNode) decomposition(response).get("sub_queries")).add(secondQuery);
        ObjectNode secondOutcome = (ObjectNode) outcome(response).deepCopy();
        secondOutcome.put("id", "q2").put("status", routingStatus);
        if (routingStatus.equals("error")) {
            ((ObjectNode) secondOutcome.get("calls").get(0)).put("error", "Tool execution failed.");
        } else {
            secondOutcome.putArray("calls");
            ((ArrayNode) secondOutcome.get("missing")).add("supplier_id");
        }
        ((ArrayNode) routing(response).get("outcomes")).add(secondOutcome);
        ((ArrayNode) answer(response).get("notices")).add("일부 결과를 확인하지 못했습니다.");
        assertValid(response);

        response.put("execution_status", routingStatus.equals("error") ? "FAILED" : "NEEDS_INPUT");
        assertInvalid(response);
    }

    @Test
    void acceptsRetainedVerificationErrorsAfterMaskingWithoutMutatingAnswer() {
        ObjectNode response = completed();
        answer(response).put("text", "지원 금액은 [확인 필요]입니다. [1]");
        answer(response).set("verification", MAPPER.readTree("""
                {"attempts":2,"unknown_citations":["E404"],"ungrounded_numbers":["999"],
                 "ungrounded_articles":["제999조"],"masked":true}
                """));
        JsonNode before = response.deepCopy();
        assertValid(response);
        assertEquals(before, response);
    }

    @ParameterizedTest
    @ValueSource(strings = {"failed", "ok"})
    void mapsFailedAnswerToDecompositionOrGenerationFailure(String decompositionStatus) {
        ObjectNode response = failed(decompositionStatus);
        assertValid(response);
        ((ObjectNode) response.get("error")).put("code",
                decompositionStatus.equals("failed") ? "GENERATION_FAILED" : "DECOMPOSITION_FAILED");
        assertInvalid(response);
    }

    @ParameterizedTest
    @ValueSource(strings = {"null", "42", "true", "\"Tool execution failed.\"", "[]",
            "{\"secretLikeText\":\"raw diagnostic\",\"nested\":[null,3.25,{\"body\":\"제4조\"}]}"})
    void preservesAllToolResultsAndGeneratedAnswer(String resultJson) {
        ObjectNode response = completed();
        ((ObjectNode) outcome(response).get("calls").get(0)).set("result", MAPPER.readTree(resultJson));
        ObjectNode reference = (ObjectNode) outcome(response).get("calls").get(0).deepCopy();
        reference.put("error", "Tool execution failed.");
        ((ArrayNode) outcome(response).get("referenced_articles")).add(reference);
        evidence(response).set("meta", MAPPER.readTree("{\"nested\":[false,null,7],\"raw\":\"original\"}"));
        JsonNode before = response.deepCopy();
        JsonNode contextBefore = CONTEXT.deepCopy();
        assertValid(response);
        assertEquals(before, response);
        assertEquals(contextBefore, CONTEXT);
    }

    @Test
    void allowsUncitedEvidenceAndNonConsecutiveCitationNumbers() {
        ObjectNode response = completed();
        citation(response).put("n", 7);
        ObjectNode extraEvidence = (ObjectNode) evidence(response).deepCopy();
        extraEvidence.put("id", "background").put("source", "business").put("citable", false);
        ((ArrayNode) answer(response).get("evidence")).add(extraEvidence);
        assertValid(response);
    }

    @ParameterizedTest
    @ValueSource(ints = {0, -1})
    void acceptsCoreIntegerValuesWithoutInventingMinimums(int value) {
        ObjectNode response = completed();
        citation(response).put("n", value);
        verification(response).put("attempts", value);
        assertValid(response);
    }

    @Test
    void doesNotInventLengthLimitsOrGroundingAndVerificationRelationships() {
        ObjectNode response = completed();
        answer(response).put("grounded", false);
        evidence(response).put("id", "").put("kind", "").put("label", "").put("text", "");
        citation(response).put("evidence_id", "").put("label", "");
        ((ArrayNode) answer(response).get("options")).add("");
        ((ArrayNode) answer(response).get("notices")).add("");
        ((ArrayNode) verification(response).get("unknown_citations")).add("E404");
        citation(response).set("n", MAPPER.readTree("9223372036854775808"));
        verification(response).set("attempts", MAPPER.readTree("9223372036854775808"));
        assertValid(response);
        String originalText = " \n" + "가".repeat(5000) + "\u00a0";
        answer(response).put("text", originalText);
        assertValid(response);
        assertEquals(originalText, answer(response).get("text").stringValue());
    }

    @ParameterizedTest
    @EmptySource
    @ValueSource(strings = {" ", "\t\r\n", "\u00a0", "\u0085", "\u2003", " \u00a0\u0085\u2003\n"})
    void rejectsEmptyOrUnicodeWhitespaceOnlyAnswerText(String text) {
        ObjectNode response = completed();
        answer(response).put("text", text);
        assertInvalid(response);
    }

    @Test
    void acceptsNullableEvidenceAndCitationSourceUrlsAndEmptyContext() {
        ObjectNode response = completed();
        evidence(response).putNull("source_url");
        citation(response).putNull("source_url");
        JsonNode emptyContext = MAPPER.readTree("{\"selected_supplier_ids\":[]}");
        response.set("context", emptyContext);
        assertDoesNotThrow(() -> validator.validate(response, REQUEST_ID, emptyContext));
    }

    @ParameterizedTest
    @ValueSource(strings = {"schema_version", "request_id", "mode", "execution_status", "generation_status",
            "context", "decomposition", "routing", "answer", "error"})
    void rejectsMissingEnvelopeFields(String field) {
        ObjectNode response = completed();
        response.remove(field);
        assertInvalid(response);
    }

    @ParameterizedTest
    @ValueSource(strings = {"kind", "text", "citations", "options", "grounded", "notices", "evidence", "verification"})
    void rejectsMissingAnswerFields(String field) {
        ObjectNode response = completed();
        answer(response).remove(field);
        assertInvalid(response);
    }

    @ParameterizedTest
    @ValueSource(strings = {"id", "sub_query", "source", "kind", "label", "text", "citable", "source_url", "meta"})
    void rejectsMissingEvidenceFields(String field) {
        ObjectNode response = completed();
        evidence(response).remove(field);
        assertInvalid(response);
    }

    @ParameterizedTest
    @ValueSource(strings = {"n", "evidence_id", "label", "source_url"})
    void rejectsMissingCitationFields(String field) {
        ObjectNode response = completed();
        citation(response).remove(field);
        assertInvalid(response);
    }

    @ParameterizedTest
    @ValueSource(strings = {"attempts", "unknown_citations", "ungrounded_numbers", "ungrounded_articles", "masked"})
    void rejectsMissingVerificationFields(String field) {
        ObjectNode response = completed();
        verification(response).remove(field);
        assertInvalid(response);
    }

    @ParameterizedTest
    @MethodSource("invalidMutations")
    void rejectsMalformedOrUnrelatedGenerationData(Consumer<ObjectNode> mutation) {
        ObjectNode response = completed();
        mutation.accept(response);
        assertInvalid(response);
    }

    static Stream<Consumer<ObjectNode>> invalidMutations() {
        return Stream.of(
                r -> r.put("unexpected", true),
                r -> r.put("schema_version", "0.1"),
                r -> r.put("mode", "test"),
                r -> r.put("request_id", UUID.randomUUID().toString()),
                r -> r.put("request_id", "1-1-1-1-1"),
                r -> r.put("execution_status", "FAILED"),
                r -> r.put("generation_status", "NOT_IMPLEMENTED"),
                r -> r.set("error", error("GENERATION_FAILED")),
                r -> r.putNull("context"),
                r -> ((ObjectNode) r.get("context")).put("unexpected", true),
                r -> ((ArrayNode) r.get("context").get("selected_supplier_ids")).add("supplier_2"),
                r -> r.set("context", MAPPER.readTree("{\"selected_supplier_ids\":[\"supplier_2\"]}")),
                r -> decomposition(r).put("schema_version", "0.3"),
                r -> routing(r).remove("schema_version"),
                r -> outcome(r).remove("referenced_articles"),
                r -> outcome(r).put("id", "q2"),
                r -> r.putNull("answer"),
                r -> answer(r).put("unexpected", true),
                r -> answer(r).put("kind", "completed"),
                r -> answer(r).putNull("text"),
                r -> answer(r).put("grounded", "true"),
                r -> answer(r).put("options", "none"),
                r -> ((ArrayNode) answer(r).get("options")).addNull(),
                r -> ((ArrayNode) answer(r).get("notices")).add(1),
                r -> answer(r).putNull("evidence"),
                r -> evidence(r).put("unexpected", true),
                r -> evidence(r).put("sub_query", "q404"),
                r -> evidence(r).put("source", "web"),
                r -> evidence(r).put("id", 1),
                r -> evidence(r).putNull("kind"),
                r -> evidence(r).put("text", 1),
                r -> evidence(r).put("citable", "true"),
                r -> evidence(r).putArray("meta"),
                r -> evidence(r).put("source_url", 1),
                r -> ((ArrayNode) answer(r).get("evidence")).add(evidence(r).deepCopy()),
                r -> answer(r).putNull("citations"),
                r -> citation(r).put("unexpected", true),
                r -> citation(r).put("n", "1"),
                r -> citation(r).put("n", 1.5),
                r -> citation(r).put("evidence_id", "E404"),
                r -> evidence(r).put("citable", false),
                r -> citation(r).put("label", "different law"),
                r -> citation(r).putNull("source_url"),
                r -> citation(r).put("source_url", "https://example.test/another-law"),
                r -> citation(r).put("source_url", 1),
                r -> answer(r).put("verification", false),
                r -> verification(r).put("ok", true),
                r -> verification(r).put("attempts", "1"),
                r -> verification(r).put("attempts", 1.5),
                r -> verification(r).putNull("unknown_citations"),
                r -> ((ArrayNode) verification(r).get("ungrounded_numbers")).add(9),
                r -> ((ArrayNode) verification(r).get("ungrounded_articles")).addNull(),
                r -> verification(r).put("masked", "true"));
    }

    @ParameterizedTest
    @ValueSource(strings = {"n", "evidence_id"})
    void rejectsDuplicateCitationNumberOrEvidenceReference(String duplicateField) {
        ObjectNode response = completed();
        ObjectNode secondEvidence = (ObjectNode) evidence(response).deepCopy();
        secondEvidence.put("id", "E2");
        ((ArrayNode) answer(response).get("evidence")).add(secondEvidence);
        ObjectNode secondCitation = (ObjectNode) citation(response).deepCopy();
        secondCitation.put("n", duplicateField.equals("n") ? 1 : 2);
        secondCitation.put("evidence_id", duplicateField.equals("evidence_id") ? "E1" : "E2");
        ((ArrayNode) answer(response).get("citations")).add(secondCitation);
        assertInvalid(response);
    }

    @ParameterizedTest
    @ValueSource(strings = {"code", "message", "retryable"})
    void rejectsMalformedFailureErrorFields(String field) {
        ObjectNode response = failed("ok");
        ObjectNode error = (ObjectNode) response.get("error");
        if (field.equals("retryable")) error.put(field, true);
        else error.put(field, "raw internal diagnostic");
        assertInvalid(response);
    }

    private void assertValid(ObjectNode response) {
        assertDoesNotThrow(() -> validator.validate(response, REQUEST_ID, CONTEXT));
    }

    private void assertInvalid(ObjectNode response) {
        IllegalArgumentException failure = assertThrows(IllegalArgumentException.class,
                () -> validator.validate(response, REQUEST_ID, CONTEXT));
        assertEquals("Invalid generation response", failure.getMessage());
    }

    private static ObjectNode failed(String decompositionStatus) {
        ObjectNode response = completed();
        response.put("execution_status", "FAILED").put("generation_status", "FAILED");
        answer(response).put("kind", "failed");
        if (decompositionStatus.equals("failed")) {
            decomposition(response).put("status", "failed");
            decomposition(response).putArray("sub_queries");
            answer(response).putArray("evidence");
            answer(response).putArray("citations");
            notRouted(response);
        }
        response.set("error", error(decompositionStatus.equals("failed") ? "DECOMPOSITION_FAILED" : "GENERATION_FAILED"));
        return response;
    }

    private static ObjectNode error(String code) {
        ObjectNode error = MAPPER.createObjectNode();
        error.put("code", code);
        error.put("message", code.equals("DECOMPOSITION_FAILED") ? "Query decomposition failed." : "Answer generation failed.");
        error.put("retryable", false);
        return error;
    }

    private static void notRouted(ObjectNode response) {
        routing(response).put("status", "not_routed");
        routing(response).putArray("outcomes");
    }

    private static ObjectNode completed() {
        return (ObjectNode) MAPPER.readTree("""
                {
                  "schema_version":"0.3","request_id":"91fb53c9-f421-4fe9-af39-780defd223c0","mode":"stub",
                  "execution_status":"COMPLETED","generation_status":"COMPLETED",
                  "context":{"selected_supplier_ids":["supplier_1"]},
                  "decomposition":{"schema_version":"0.1","status":"ok","original_query":"법률 확인",
                    "sub_queries":[{"id":"q1","question":"법률 확인","original_text":"법률 확인","intent":"law_check",
                      "grounding":"required","confidence":0.9,"entities":{},"inputs":[],"depends_on":[]}],
                    "clarification":null,"assumptions":[]},
                  "routing":{"schema_version":"0.1","status":"routed","halted":false,"halt_reason":null,
                    "outcomes":[{"id":"q1","intent":"law_check","status":"ok","missing":[],"message":null,
                      "calls":[{"server":"law-mcp","tool":"get_article","args":{"article":"4"},
                        "result":{"article":"제4조","body":"계약서를 보존한다."},"error":null}],"referenced_articles":[]}]},
                  "answer":{"kind":"answer","text":"계약서를 보존합니다. [1]","citations":[
                      {"n":1,"evidence_id":"E1","label":"법령 제4조","source_url":"https://example.test/law"}],
                    "options":[],"grounded":true,"notices":[],"evidence":[
                      {"id":"E1","sub_query":"q1","source":"law","kind":"article","label":"법령 제4조",
                        "text":"계약서를 보존한다.","citable":true,"source_url":"https://example.test/law","meta":{}}],
                    "verification":{"attempts":1,"unknown_citations":[],"ungrounded_numbers":[],"ungrounded_articles":[],"masked":false}},
                  "error":null
                }
                """);
    }

    private static ObjectNode answer(ObjectNode response) { return (ObjectNode) response.get("answer"); }
    private static ObjectNode evidence(ObjectNode response) { return (ObjectNode) answer(response).get("evidence").get(0); }
    private static ObjectNode citation(ObjectNode response) { return (ObjectNode) answer(response).get("citations").get(0); }
    private static ObjectNode verification(ObjectNode response) { return (ObjectNode) answer(response).get("verification"); }
    private static ObjectNode decomposition(ObjectNode response) { return (ObjectNode) response.get("decomposition"); }
    private static ObjectNode routing(ObjectNode response) { return (ObjectNode) response.get("routing"); }
    private static ObjectNode outcome(ObjectNode response) { return (ObjectNode) routing(response).get("outcomes").get(0); }
}
