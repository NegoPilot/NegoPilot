package com.negopilot.backend;

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

class AgentResponseValidatorTest {
    private static final UUID REQUEST_ID = UUID.fromString("91fb53c9-f421-4fe9-af39-780defd223c0");
    private static final JsonMapper MAPPER = JsonMapper.builder().build();
    private final AgentResponseValidator validator = new AgentResponseValidator();

    @ParameterizedTest
    @ValueSource(strings = {"ok", "general_knowledge", "out_of_scope", "unsupported", "skipped", "empty"})
    void acceptsCompletedDetailedRoutingStatuses(String status) {
        ObjectNode response = completed();
        outcome(response).put("status", status);
        if (status.equals("ok")) {
            ((ArrayNode) outcome(response).get("calls")).add(toolCall(null));
        }
        if (status.equals("out_of_scope")) {
            subQuery(response).put("intent", "out_of_scope");
            outcome(response).put("intent", "out_of_scope");
        }
        if (status.equals("empty")) {
            routing(response).put("halted", true).put("halt_reason", "No matching result");
        }
        assertDoesNotThrow(() -> validator.validate(response, REQUEST_ID));
    }

    @Test
    void acceptsClarification() {
        assertDoesNotThrow(() -> validator.validate(clarify(), REQUEST_ID));
    }

    @Test
    void acceptsDecompositionFailure() {
        assertDoesNotThrow(() -> validator.validate(failed(), REQUEST_ID));
    }

    @ParameterizedTest
    @ValueSource(strings = {"DECOMPOSITION_FAILED", "ROUTING_FAILED"})
    void rejectsRetryableExecutionFailures(String code) {
        ObjectNode response = code.equals("DECOMPOSITION_FAILED") ? failed() : completed();
        if (code.equals("ROUTING_FAILED")) {
            response.put("execution_status", "FAILED");
            outcome(response).put("status", "error");
            ((ArrayNode) outcome(response).get("calls")).add(toolCall("Tool execution failed."));
            response.set("error", error(code));
        }
        ((ObjectNode) response.get("error")).put("retryable", true);
        assertInvalid(response);
    }

    @ParameterizedTest
    @ValueSource(strings = {"COMPLETED", "FAILED"})
    void rejectsClarificationWithWrongExecutionStatus(String status) {
        ObjectNode response = clarify();
        response.put("execution_status", status);
        assertInvalid(response);
    }

    @Test
    void rejectsClarificationWithoutDetails() {
        ObjectNode response = clarify();
        decomposition(response).putNull("clarification");
        assertInvalid(response);
    }

    @Test
    void acceptsMissingArguments() {
        ObjectNode response = completed();
        response.put("execution_status", "NEEDS_INPUT");
        outcome(response).put("status", "missing_args");
        ((ArrayNode) outcome(response).get("missing")).add("supplier");
        assertDoesNotThrow(() -> validator.validate(response, REQUEST_ID));
    }

    @Test
    void routingErrorTakesPrecedenceOverMissingArguments() {
        ObjectNode response = completed();
        ObjectNode secondSubQuery = (ObjectNode) subQuery(response).deepCopy();
        secondSubQuery.put("id", "q2");
        ((ArrayNode) decomposition(response).get("sub_queries")).add(secondSubQuery);
        ObjectNode secondOutcome = (ObjectNode) outcome(response).deepCopy();
        secondOutcome.put("id", "q2").put("status", "error");
        ((ArrayNode) secondOutcome.get("calls")).add(toolCall("Tool execution failed."));
        ((ArrayNode) routing(response).get("outcomes")).add(secondOutcome);
        outcome(response).put("status", "missing_args");
        ((ArrayNode) outcome(response).get("missing")).add("supplier");
        response.put("execution_status", "FAILED");
        response.set("error", error("ROUTING_FAILED"));
        assertDoesNotThrow(() -> validator.validate(response, REQUEST_ID));
        response.put("execution_status", "NEEDS_INPUT").putNull("error");
        assertInvalid(response);
    }

    @ParameterizedTest
    @ValueSource(strings = {"null", "[]", "{}", "42", "true", "\"some text\"",
            "{\"articles\":[{\"body\":\"fixture\"}],\"nested\":{\"value\":null}}"})
    void preservesArbitraryToolResultJson(String result) {
        ObjectNode response = completed();
        outcome(response).put("status", "ok");
        ObjectNode call = (ObjectNode) MAPPER.readTree("""
                {"server":"law-mcp","tool":"get_article","args":{},"result":null,"error":null}
                """);
        call.set("result", MAPPER.readTree(result));
        ((ArrayNode) outcome(response).get("calls")).add(call);
        JsonNode before = response.deepCopy();
        assertDoesNotThrow(() -> validator.validate(response, REQUEST_ID));
        assertEquals(before, response);
    }

    @ParameterizedTest
    @ValueSource(strings = {"schema_version", "request_id", "execution_status", "generation_status",
            "mode", "decomposition", "routing", "error"})
    void rejectsMissingEnvelopeFields(String name) {
        ObjectNode response = completed();
        response.remove(name);
        assertInvalid(response);
    }

    @ParameterizedTest
    @MethodSource("invalidMutations")
    void rejectsInvalidStructureAndState(Consumer<ObjectNode> mutation) {
        ObjectNode response = completed();
        mutation.accept(response);
        assertInvalid(response);
    }

    static Stream<Consumer<ObjectNode>> invalidMutations() {
        return Stream.of(
                r -> r.put("unexpected", true),
                r -> r.put("schema_version", "0.2"),
                r -> r.put("schema_version", 0.1),
                r -> r.put("generation_status", "COMPLETED"),
                r -> r.put("mode", "test"),
                r -> r.put("request_id", UUID.randomUUID().toString()),
                r -> r.put("request_id", "1-1-1-1-1"),
                r -> r.putNull("request_id"),
                r -> r.put("execution_status", "FAILED"),
                r -> r.set("error", error("ROUTING_FAILED")),
                r -> decomposition(r).remove("clarification"),
                r -> decomposition(r).put("status", "clarify"),
                r -> decomposition(r).put("status", "failed"),
                r -> decomposition(r).put("schema_version", "9.9"),
                r -> decomposition(r).put("original_query", ""),
                r -> decomposition(r).put("assumptions", "none"),
                r -> ((ArrayNode) decomposition(r).get("sub_queries")).removeAll(),
                r -> ((ArrayNode) decomposition(r).get("sub_queries")).add(subQuery(r).deepCopy()),
                r -> subQuery(r).put("id", "invalid"),
                r -> subQuery(r).put("confidence", "0.9"),
                r -> subQuery(r).put("confidence", 1.1),
                r -> subQuery(r).put("grounding", "maybe"),
                r -> subQuery(r).put("entities", "bad"),
                r -> ((ObjectNode) subQuery(r).get("entities")).putObject("nested"),
                r -> ((ArrayNode) subQuery(r).get("depends_on")).add("q1"),
                r -> subQuery(r).put("inputs", "bad"),
                r -> ((ArrayNode) subQuery(r).get("inputs")).add(MAPPER.readTree(
                        "{\"slot\":\"supplier\",\"from_\":\"q0\",\"select\":\"top1\"}")),
                r -> routing(r).put("status", "not_routed"),
                r -> routing(r).remove("schema_version"),
                r -> routing(r).put("schema_version", "0.2"),
                r -> routing(r).put("halted", "false"),
                r -> routing(r).put("halted", true),
                r -> routing(r).put("halt_reason", "unexpected reason"),
                r -> ((ArrayNode) routing(r).get("outcomes")).removeAll(),
                r -> outcome(r).put("id", "q2"),
                r -> outcome(r).put("intent", "other_intent"),
                r -> outcome(r).put("status", "unknown"),
                r -> outcome(r).put("status", "no_tool"),
                r -> outcome(r).put("status", "missing_args"),
                r -> outcome(r).put("status", "error"),
                r -> outcome(r).put("status", "ok"),
                r -> outcome(r).put("calls", "none"),
                r -> outcome(r).remove("referenced_articles"),
                r -> outcome(r).put("referenced_articles", "none"),
                r -> outcome(r).put("missing", false),
                r -> ((ArrayNode) outcome(r).get("missing")).add("supplier"),
                r -> outcome(r).put("message", 12),
                r -> ((ArrayNode) outcome(r).get("calls")).add(MAPPER.readTree(
                        "{\"server\":\"law\",\"tool\":\"get\",\"args\":[],\"result\":null,\"error\":null}")),
                r -> ((ArrayNode) outcome(r).get("calls")).add(MAPPER.readTree(
                        "{\"server\":\"law\",\"tool\":\"get\",\"args\":{},\"error\":null}"))
        );
    }

    @Test
    void rejectsMalformedClarificationAndFailure() {
        ObjectNode clarification = clarify();
        ((ArrayNode) decomposition(clarification).get("clarification").get("options")).removeAll();
        assertInvalid(clarification);
        ObjectNode failure = failed();
        failure.set("error", error("ROUTING_FAILED"));
        assertInvalid(failure);
        failure = failed();
        ((ObjectNode) failure.get("error")).put("retryable", "false");
        assertInvalid(failure);
        failure = failed();
        routing(failure).put("status", "routed");
        assertInvalid(failure);
    }

    @Test
    void acceptsDependencyInputAliasesAndRejectsForwardReferences() {
        ObjectNode response = completed();
        ObjectNode second = (ObjectNode) subQuery(response).deepCopy();
        second.put("id", "q2");
        ((ArrayNode) second.get("depends_on")).add("q1");
        ((ArrayNode) second.get("inputs")).add(MAPPER.readTree(
                "{\"slot\":\"supplier\",\"from\":\"q1\",\"select\":\"top(2)\"}"));
        ((ArrayNode) decomposition(response).get("sub_queries")).add(second);
        ObjectNode secondOutcome = (ObjectNode) outcome(response).deepCopy();
        secondOutcome.put("id", "q2");
        ((ArrayNode) routing(response).get("outcomes")).add(secondOutcome);
        assertDoesNotThrow(() -> validator.validate(response, REQUEST_ID));
        ((ArrayNode) second.get("depends_on")).add("q3");
        assertInvalid(response);
    }

    private void assertInvalid(JsonNode response) {
        IllegalArgumentException exception = assertThrows(IllegalArgumentException.class,
                () -> validator.validate(response, REQUEST_ID));
        assertEquals("Invalid upstream response", exception.getMessage());
    }

    private static ObjectNode completed() {
        return (ObjectNode) MAPPER.readTree("""
                {
                  "schema_version":"0.1",
                  "request_id":"91fb53c9-f421-4fe9-af39-780defd223c0",
                  "execution_status":"COMPLETED",
                  "generation_status":"NOT_IMPLEMENTED",
                  "mode":"stub",
                  "decomposition":{
                    "schema_version":"0.1","status":"ok","original_query":"question",
                    "sub_queries":[{
                      "id":"q1","question":"question","original_text":"question",
                      "intent":"general_knowledge","grounding":"none","confidence":0.9,
                      "entities":{},"inputs":[],"depends_on":[]
                    }],
                    "clarification":null,"assumptions":[]
                  },
                  "routing":{"schema_version":"0.1",
                    "status":"routed","outcomes":[{
                      "id":"q1","intent":"general_knowledge","status":"general_knowledge",
                      "calls":[],"referenced_articles":[],"missing":[],"message":null
                    }],"halted":false,"halt_reason":null
                  },
                  "error":null
                }
                """);
    }

    private static ObjectNode clarify() {
        ObjectNode response = completed();
        response.put("execution_status", "NEEDS_INPUT");
        decomposition(response).put("status", "clarify");
        ((ArrayNode) decomposition(response).get("sub_queries")).removeAll();
        decomposition(response).set("clarification", MAPPER.readTree(
                "{\"reason\":\"Ambiguous\",\"question\":\"Which supplier?\",\"options\":[\"A\",\"B\"]}"));
        routing(response).put("status", "not_routed");
        ((ArrayNode) routing(response).get("outcomes")).removeAll();
        return response;
    }

    private static ObjectNode failed() {
        ObjectNode response = clarify();
        response.put("execution_status", "FAILED");
        decomposition(response).put("status", "failed").putNull("clarification");
        response.set("error", error("DECOMPOSITION_FAILED"));
        return response;
    }

    private static ObjectNode error(String code) {
        return MAPPER.createObjectNode().put("code", code).put("message", "Execution failed")
                .put("retryable", false);
    }

    private static ObjectNode toolCall(String error) {
        ObjectNode call = (ObjectNode) MAPPER.readTree("""
                {"server":"law-mcp","tool":"get_article","args":{},"result":null,"error":null}
                """);
        if (error != null) call.put("error", error);
        return call;
    }

    private static ObjectNode decomposition(ObjectNode response) {
        return (ObjectNode) response.get("decomposition");
    }

    private static ObjectNode routing(ObjectNode response) {
        return (ObjectNode) response.get("routing");
    }

    private static ObjectNode subQuery(ObjectNode response) {
        return (ObjectNode) decomposition(response).get("sub_queries").get(0);
    }

    private static ObjectNode outcome(ObjectNode response) {
        return (ObjectNode) routing(response).get("outcomes").get(0);
    }
}
