package com.negopilot.backend.agent;

import java.io.IOException;
import java.io.InputStream;
import java.io.UncheckedIOException;
import java.util.List;
import java.util.UUID;
import java.util.function.Consumer;
import java.util.stream.Stream;
import java.util.stream.StreamSupport;

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
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** Offline replay of shared fixtures. This class never starts Python or calls MCP. */
class LatestRoutingContractTest {
    private static final UUID REQUEST_ID = UUID.fromString("cb0bcb9c-5ea7-4d26-bb54-f4d211867273");
    private static final JsonMapper MAPPER = JsonMapper.builder().build();
    private static final String OFFER_FIXTURE = "05_offer_check.json";
    private final AgentResponseValidator validator = new AgentResponseValidator();

    record FixtureCase(String file, String decompositionStatus, String routingStatus,
                       List<String> outcomeStatuses, List<Integer> referenceCounts,
                       String executionStatus, String errorCode) {
        @Override
        public String toString() {
            return file + " => " + executionStatus;
        }
    }

    // This table records what the checked-in fixtures actually contain. In
    // particular 02, 03 and 08 have successful tool outcomes, not routing errors.
    static Stream<FixtureCase> recordedCases() {
        return Stream.of(
                new FixtureCase("01_law_article.json", "ok", "routed",
                        List.of("ok"), List.of(0), "COMPLETED", null),
                new FixtureCase("02_parallel_suppliers.json", "ok", "routed",
                        List.of("ok", "ok"), List.of(0, 0), "COMPLETED", null),
                new FixtureCase("03_rank_then_law.json", "ok", "routed",
                        List.of("ok", "ok"), List.of(0, 0), "COMPLETED", null),
                new FixtureCase("04_business_wording.json", "ok", "routed",
                        List.of("ok"), List.of(0), "COMPLETED", null),
                new FixtureCase(OFFER_FIXTURE, "ok", "routed",
                        List.of("ok"), List.of(3), "COMPLETED", null),
                new FixtureCase("06_general_knowledge.json", "ok", "routed",
                        List.of("general_knowledge"), List.of(0), "COMPLETED", null),
                new FixtureCase("07_number_needs_grounding.json", "ok", "routed",
                        List.of("ok"), List.of(0), "COMPLETED", null),
                new FixtureCase("08_assumption_instead_of_clarify.json", "ok", "routed",
                        List.of("ok", "ok"), List.of(0, 0), "COMPLETED", null),
                new FixtureCase("09_clarify.json", "clarify", "not_routed",
                        List.of(), List.of(), "NEEDS_INPUT", null),
                new FixtureCase("10_out_of_scope.json", "ok", "routed",
                        List.of("out_of_scope"), List.of(0), "COMPLETED", null),
                new FixtureCase("11_failed.json", "failed", "not_routed",
                        List.of(), List.of(), "FAILED", "DECOMPOSITION_FAILED")
        );
    }

    @ParameterizedTest(name = "{0}")
    @MethodSource("recordedCases")
    void acceptsLatestRecordedRoutingAndItsReferencedDecomposition(FixtureCase expected) {
        ObjectNode response = envelope(expected);
        assertEquals(expected.decompositionStatus(), response.path("decomposition").path("status").stringValue());
        assertEquals("0.1", routing(response).path("schema_version").stringValue());
        assertEquals(expected.routingStatus(), routing(response).path("status").stringValue());
        assertEquals(expected.outcomeStatuses(), nodes(routing(response).path("outcomes"))
                .map(outcome -> outcome.path("status").stringValue()).toList());
        assertEquals(expected.referenceCounts(), nodes(routing(response).path("outcomes"))
                .map(outcome -> outcome.path("referenced_articles").size()).toList());
        for (JsonNode outcome : routing(response).path("outcomes")) {
            assertTrue(outcome.path("referenced_articles").isArray(), "Latest outcomes include referenced_articles");
        }

        JsonNode before = response.deepCopy();
        assertDoesNotThrow(() -> validator.validate(response, REQUEST_ID));
        assertEquals(before, response, "Validation must preserve recorded tool payloads and references");
    }

    @Test
    void offerFixtureContainsRealReferencePayloadsMatchingItsRecordedLawRefs() {
        ObjectNode response = envelope(OFFER_FIXTURE);
        JsonNode lawRefs = outcome(response).path("calls").get(0).path("result").path("law_refs");
        ArrayNode articles = references(response);
        assertEquals(3, articles.size());
        assertEquals(3, lawRefs.size());
        for (int index = 0; index < articles.size(); index++) {
            JsonNode call = articles.get(index);
            assertEquals("law-mcp", call.path("server").stringValue());
            assertEquals("get_article", call.path("tool").stringValue());
            assertEquals(lawRefs.get(index), call.path("args"));
            assertTrue(call.path("result").path("citation").isString());
            assertFalse(call.path("result").path("citation").stringValue().isBlank());
            assertTrue(call.path("error").isNull());
        }
        assertDoesNotThrow(() -> validator.validate(response, REQUEST_ID));
    }

    @Test
    void dependentFixturePreservesSerializedFromAliasAndOutcomeOrder() {
        ObjectNode response = envelope("08_assumption_instead_of_clarify.json");
        JsonNode second = response.path("decomposition").path("sub_queries").get(1);
        JsonNode input = second.path("inputs").get(0);
        assertEquals("q1", input.path("from").stringValue());
        assertFalse(input.has("from_"));
        assertEquals(List.of("q1", "q2"), nodes(routing(response).path("outcomes"))
                .map(item -> item.path("id").stringValue()).toList());
        assertDoesNotThrow(() -> validator.validate(response, REQUEST_ID));
    }

    @ParameterizedTest
    @ValueSource(strings = {"06_general_knowledge.json", "10_out_of_scope.json"})
    void rejectsRetiredNoToolStatusInBothRecordedNoCallCases(String fixture) {
        ObjectNode response = envelope(fixture);
        assertTrue(outcome(response).path("calls").isEmpty());
        outcome(response).put("status", "no_tool");
        assertInvalid(response);
    }

    @ParameterizedTest
    @ValueSource(strings = {"01_law_article.json", "09_clarify.json", "11_failed.json"})
    void routingSchemaVersionIsRequiredEvenWhenNothingWasRouted(String fixture) {
        ObjectNode response = envelope(fixture);
        routing(response).remove("schema_version");
        assertInvalid(response);
    }

    record InvalidCase(String name, Consumer<ObjectNode> mutation) {
        @Override
        public String toString() {
            return name;
        }
    }

    static Stream<InvalidCase> invalidLatestRoutingShapes() {
        return Stream.of(
                new InvalidCase("unsupported routing schema", r -> routing(r).put("schema_version", "0.2")),
                new InvalidCase("numeric routing schema", r -> routing(r).put("schema_version", 0.1)),
                new InvalidCase("null routing schema", r -> routing(r).putNull("schema_version")),
                new InvalidCase("unknown route status", r -> routing(r).put("status", "completed")),
                new InvalidCase("unknown outcome status", r -> outcome(r).put("status", "unknown")),
                new InvalidCase("numeric outcome status", r -> outcome(r).put("status", 1)),
                new InvalidCase("extra routing field", r -> routing(r).put("unexpected", true)),
                new InvalidCase("extra outcome field", r -> outcome(r).put("unexpected", true)),
                new InvalidCase("missing referenced_articles", r -> outcome(r).remove("referenced_articles")),
                new InvalidCase("null referenced_articles", r -> outcome(r).putNull("referenced_articles")),
                new InvalidCase("object referenced_articles", r -> outcome(r).putObject("referenced_articles")),
                new InvalidCase("string reference item", r -> references(r).add("not-a-call")),
                new InvalidCase("null reference item", r -> references(r).addNull()),
                new InvalidCase("missing reference server", r -> reference(r).remove("server")),
                new InvalidCase("missing reference tool", r -> reference(r).remove("tool")),
                new InvalidCase("missing reference args", r -> reference(r).remove("args")),
                new InvalidCase("missing reference result", r -> reference(r).remove("result")),
                new InvalidCase("missing reference error", r -> reference(r).remove("error")),
                new InvalidCase("numeric reference server", r -> reference(r).put("server", 1)),
                new InvalidCase("boolean reference tool", r -> reference(r).put("tool", false)),
                new InvalidCase("array reference args", r -> reference(r).putArray("args")),
                new InvalidCase("boolean reference error", r -> reference(r).put("error", false)),
                new InvalidCase("extra reference field", r -> reference(r).put("unexpected", true))
        );
    }

    @ParameterizedTest(name = "{0}")
    @MethodSource("invalidLatestRoutingShapes")
    void rejectsMalformedLatestRoutingAndReferencedToolCalls(InvalidCase invalid) {
        ObjectNode response = envelope(OFFER_FIXTURE);
        invalid.mutation().accept(response);
        assertInvalid(response);
    }

    @ParameterizedTest
    @ValueSource(strings = {"null", "[]", "{}", "42", "true", "\"recorded text\"",
            "{\"paragraphs\":[{\"text\":\"fixture\"}],\"nested\":{\"value\":null}}"})
    void acceptsArbitraryReferenceResultJsonWithoutRewritingIt(String result) {
        ObjectNode response = envelope(OFFER_FIXTURE);
        reference(response).set("result", MAPPER.readTree(result));
        JsonNode before = response.deepCopy();
        assertDoesNotThrow(() -> validator.validate(response, REQUEST_ID));
        assertEquals(before, response);
    }

    @Test
    void referenceLookupFailureDoesNotChangeTheSuccessfulMainOutcome() {
        ObjectNode response = envelope(OFFER_FIXTURE);
        // Synthetic error mutation: the recorded fixture itself has no reference failure.
        reference(response).putNull("result").put("error", "Reference lookup unavailable");
        assertEquals("ok", outcome(response).path("status").stringValue());
        assertEquals("COMPLETED", response.path("execution_status").stringValue());
        assertTrue(response.path("error").isNull());
        JsonNode before = response.deepCopy();
        assertDoesNotThrow(() -> validator.validate(response, REQUEST_ID));
        assertEquals(before, response);

        response.put("execution_status", "FAILED");
        response.set("error", error("ROUTING_FAILED"));
        assertInvalid(response);
    }

    private void assertInvalid(JsonNode response) {
        IllegalArgumentException exception = assertThrows(IllegalArgumentException.class,
                () -> validator.validate(response, REQUEST_ID));
        assertEquals("Invalid upstream response", exception.getMessage());
    }

    private static ObjectNode envelope(String file) {
        return envelope(recordedCases().filter(item -> item.file().equals(file)).findFirst().orElseThrow());
    }

    private static ObjectNode envelope(FixtureCase expected) {
        JsonNode recorded = readFixture("routing/" + expected.file());
        String decompositionPath = recorded.path("decomposition").stringValue();
        assertEquals("decomposition/" + expected.file(), decompositionPath);
        JsonNode decomposition = readFixture(decompositionPath);
        assertEquals(recorded.path("query"), decomposition.path("query"));

        ObjectNode response = MAPPER.createObjectNode();
        response.put("schema_version", "0.1");
        response.put("request_id", REQUEST_ID.toString());
        response.put("execution_status", expected.executionStatus());
        response.put("generation_status", "NOT_IMPLEMENTED");
        response.put("mode", "live");
        response.set("decomposition", decomposition.path("expected").deepCopy());
        response.set("routing", recorded.path("routing").deepCopy());
        if (expected.errorCode() == null) {
            response.putNull("error");
        } else {
            response.set("error", error(expected.errorCode()));
        }
        return response;
    }

    private static JsonNode readFixture(String relativePath) {
        String resource = "/contracts/" + relativePath;
        try (InputStream input = LatestRoutingContractTest.class.getResourceAsStream(resource)) {
            assertNotNull(input, "Shared fixture is missing from test resources: " + resource);
            return MAPPER.readTree(input);
        } catch (IOException exception) {
            throw new UncheckedIOException(exception);
        }
    }

    private static ObjectNode error(String code) {
        return MAPPER.createObjectNode().put("code", code)
                .put("message", "Recorded execution failed").put("retryable", false);
    }

    private static ObjectNode routing(ObjectNode response) {
        return (ObjectNode) response.get("routing");
    }

    private static ObjectNode outcome(ObjectNode response) {
        return (ObjectNode) routing(response).get("outcomes").get(0);
    }

    private static ArrayNode references(ObjectNode response) {
        return (ArrayNode) outcome(response).get("referenced_articles");
    }

    private static ObjectNode reference(ObjectNode response) {
        return (ObjectNode) references(response).get(0);
    }

    private static Stream<JsonNode> nodes(JsonNode array) {
        return StreamSupport.stream(array.spliterator(), false);
    }
}
