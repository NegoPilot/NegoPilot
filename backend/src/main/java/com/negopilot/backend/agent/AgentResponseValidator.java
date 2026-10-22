package com.negopilot.backend.agent;

import java.util.HashSet;
import java.util.Set;
import java.util.UUID;

import tools.jackson.databind.JsonNode;

/** Validates the HTTP boundary without duplicating the Python intent registry. */
public final class AgentResponseValidator {
    private static final Set<String> OUTCOME_STATUSES = Set.of(
            "ok", "general_knowledge", "out_of_scope", "unsupported", "missing_args", "empty", "skipped", "error");

    public void validate(JsonNode response, UUID expectedRequestId) {
        object(response, "schema_version", "request_id", "execution_status", "generation_status",
                "mode", "decomposition", "routing", "error");
        equalsText(response.get("schema_version"), "0.1");
        equalsText(response.get("generation_status"), "NOT_IMPLEMENTED");
        oneOf(response.get("mode"), Set.of("live", "stub"));
        oneOf(response.get("execution_status"), Set.of("COMPLETED", "NEEDS_INPUT", "FAILED"));
        String requestId = text(response.get("request_id"));
        require(requestId.matches("[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"));
        require(UUID.fromString(requestId).equals(expectedRequestId));

        JsonNode decomposition = response.get("decomposition");
        validateDecomposition(decomposition);
        JsonNode routing = response.get("routing");
        validateRouting(routing, decomposition);

        String expectedStatus;
        String expectedError = null;
        String decompositionStatus = text(decomposition.get("status"));
        if (decompositionStatus.equals("failed")) {
            expectedStatus = "FAILED";
            expectedError = "DECOMPOSITION_FAILED";
        } else if (decompositionStatus.equals("clarify")) {
            expectedStatus = "NEEDS_INPUT";
        } else if (hasOutcome(routing, "error")) {
            expectedStatus = "FAILED";
            expectedError = "ROUTING_FAILED";
        } else if (hasOutcome(routing, "missing_args")) {
            expectedStatus = "NEEDS_INPUT";
        } else {
            expectedStatus = "COMPLETED";
        }
        equalsText(response.get("execution_status"), expectedStatus);
        JsonNode error = response.get("error");
        if (expectedError == null) {
            require(error.isNull());
        } else {
            object(error, "code", "message", "retryable");
            equalsText(error.get("code"), expectedError);
            nonemptyText(error.get("message"));
            require(error.get("retryable").isBoolean() && !error.get("retryable").booleanValue());
        }
    }

    static void validateDecomposition(JsonNode decomposition) {
        object(decomposition, "schema_version", "status", "original_query", "sub_queries",
                "clarification", "assumptions");
        equalsText(decomposition.get("schema_version"), "0.1");
        String status = oneOf(decomposition.get("status"), Set.of("ok", "clarify", "failed"));
        nonemptyText(decomposition.get("original_query"));
        strings(decomposition.get("assumptions"));
        JsonNode subQueries = decomposition.get("sub_queries");
        array(subQueries);
        require(subQueries.size() <= 5);
        JsonNode clarification = decomposition.get("clarification");
        if (status.equals("ok")) {
            require(!subQueries.isEmpty() && clarification.isNull());
        } else if (status.equals("clarify")) {
            require(subQueries.isEmpty());
            object(clarification, "reason", "question", "options");
            nonemptyText(clarification.get("reason"));
            nonemptyText(clarification.get("question"));
            strings(clarification.get("options"));
            require(clarification.get("options").size() >= 2 && clarification.get("options").size() <= 4);
        } else {
            require(subQueries.isEmpty() && clarification.isNull());
        }

        Set<String> seen = new HashSet<>();
        for (JsonNode subQuery : subQueries) {
            object(subQuery, "id", "question", "original_text", "intent", "grounding", "confidence",
                    "entities", "inputs", "depends_on");
            String id = text(subQuery.get("id"));
            require(id.matches("q[0-9]+") && !seen.contains(id));
            nonemptyText(subQuery.get("question"));
            nonemptyText(subQuery.get("original_text"));
            nonemptyText(subQuery.get("intent"));
            oneOf(subQuery.get("grounding"), Set.of("required", "none"));
            JsonNode confidence = subQuery.get("confidence");
            require(confidence.isNumber());
            require(Double.isFinite(confidence.doubleValue())
                    && confidence.doubleValue() >= 0 && confidence.doubleValue() <= 1);
            require(subQuery.get("entities").isObject());
            for (JsonNode entity : subQuery.get("entities")) {
                require(entity.isNull() || entity.isString() || entity.isBoolean() || entity.isNumber());
            }
            JsonNode dependencies = subQuery.get("depends_on");
            strings(dependencies);
            Set<String> dependencyIds = new HashSet<>();
            for (JsonNode dependency : dependencies) {
                require(seen.contains(dependency.stringValue()));
                dependencyIds.add(dependency.stringValue());
            }
            array(subQuery.get("inputs"));
            for (JsonNode input : subQuery.get("inputs")) {
                object(input, "slot", "from", "select");
                nonemptyText(input.get("slot"));
                require(dependencyIds.contains(text(input.get("from"))));
                require(text(input.get("select")).matches("top1|all|top\\([1-9][0-9]*\\)|by_name\\(\"[^\"]+\"\\)"));
            }
            seen.add(id);
        }
    }

    static void validateRouting(JsonNode routing, JsonNode decomposition) {
        object(routing, "schema_version", "status", "outcomes", "halted", "halt_reason");
        equalsText(routing.get("schema_version"), "0.1");
        String status = oneOf(routing.get("status"), Set.of("routed", "not_routed"));
        JsonNode outcomes = routing.get("outcomes");
        array(outcomes);
        require(routing.get("halted").isBoolean());
        nullableText(routing.get("halt_reason"));
        boolean halted = routing.get("halted").booleanValue();
        require(halted ? !routing.get("halt_reason").isNull() : routing.get("halt_reason").isNull());
        JsonNode subQueries = decomposition.get("sub_queries");
        if (!text(decomposition.get("status")).equals("ok")) {
            require(status.equals("not_routed") && outcomes.isEmpty() && !halted);
            return;
        }
        require(status.equals("routed") && outcomes.size() == subQueries.size());
        for (int index = 0; index < outcomes.size(); index++) {
            JsonNode outcome = outcomes.get(index);
            object(outcome, "id", "intent", "status", "calls", "referenced_articles", "missing", "message");
            equalsText(outcome.get("id"), text(subQueries.get(index).get("id")));
            equalsText(outcome.get("intent"), text(subQueries.get(index).get("intent")));
            String outcomeStatus = oneOf(outcome.get("status"), OUTCOME_STATUSES);
            strings(outcome.get("missing"));
            require(outcomeStatus.equals("missing_args") == !outcome.get("missing").isEmpty());
            nullableText(outcome.get("message"));
            toolCalls(outcome.get("calls"));
            if (outcomeStatus.equals("ok") || outcomeStatus.equals("error")) require(!outcome.get("calls").isEmpty());
            // Reference lookup failures remain auxiliary data and do not change the outcome status.
            toolCalls(outcome.get("referenced_articles"));
        }
        require(!halted || hasOutcome(routing, "empty"));
    }

    private static void toolCalls(JsonNode calls) {
        array(calls);
        for (JsonNode call : calls) {
            object(call, "server", "tool", "args", "result", "error");
            text(call.get("server"));
            text(call.get("tool"));
            require(call.get("args").isObject());
            nullableText(call.get("error"));
            // Tool result values are intentionally arbitrary JSON.
        }
    }

    private static boolean hasOutcome(JsonNode routing, String status) {
        for (JsonNode outcome : routing.get("outcomes")) {
            if (text(outcome.get("status")).equals(status)) {
                return true;
            }
        }
        return false;
    }

    private static void object(JsonNode node, String... fields) {
        require(node != null && node.isObject() && node.size() == fields.length);
        for (String field : fields) {
            require(node.has(field));
        }
    }

    private static void array(JsonNode node) {
        require(node != null && node.isArray());
    }

    private static void strings(JsonNode node) {
        array(node);
        for (JsonNode item : node) {
            text(item);
        }
    }

    private static String text(JsonNode node) {
        require(node != null && node.isString());
        return node.stringValue();
    }

    private static void nonemptyText(JsonNode node) {
        require(!text(node).isEmpty());
    }

    private static void nullableText(JsonNode node) {
        require(node != null && (node.isNull() || node.isString()));
    }

    private static void equalsText(JsonNode node, String expected) {
        require(text(node).equals(expected));
    }

    private static String oneOf(JsonNode node, Set<String> allowed) {
        String value = text(node);
        require(allowed.contains(value));
        return value;
    }

    private static void require(boolean condition) {
        if (!condition) {
            throw new IllegalArgumentException("Invalid upstream response");
        }
    }
}
