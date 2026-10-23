package com.negopilot.backend.agent;

import java.util.HashSet;
import java.util.Set;

import tools.jackson.databind.JsonNode;

/** Validates decomposition and routing results without duplicating the Python intent registry. */
final class DecompositionRoutingValidator {
    private static final Set<String> OUTCOME_STATUSES = Set.of(
            "ok", "general_knowledge", "out_of_scope", "unsupported", "missing_args", "empty", "skipped", "error");
    private static final Set<String> PUBLIC_TOOL_ERROR_CODES = Set.of(
            "RFQ_NOT_FOUND", "SUPPLIER_NOT_FOUND", "SUPPLIER_AMBIGUOUS", "VALUE_OUT_OF_RANGE");

    private DecompositionRoutingValidator() {}

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
            for (String field : subQuery.get("entities").propertyNames()) {
                JsonNode entity = subQuery.get("entities").get(field);
                if ((field.equals("supplier_size") || field.equals("buyer_size")) && !entity.isNull()) {
                    validateCompanySize(entity);
                } else {
                    require(entity.isNull() || entity.isString() || entity.isBoolean() || entity.isNumber());
                }
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

    private static void validateCompanySize(JsonNode size) {
        require(size.isObject());
        Set<String> texts = Set.of("ksic_code", "evidence_reference");
        Set<String> numbers = Set.of("average_revenue_eok", "assets_eok", "related_aggregate_revenue_eok");
        Set<String> booleans = Set.of("independence_met", "has_related_enterprises", "related_calculation_verified",
                "seat_manufacturing", "transition_reviewed", "deemed_sme");
        for (String field : size.propertyNames()) {
            JsonNode value = size.get(field);
            require(texts.contains(field) || numbers.contains(field) || booleans.contains(field));
            if (value.isNull()) {
                require(!field.equals("related_calculation_verified") && !field.equals("transition_reviewed"));
            } else if (texts.contains(field)) {
                require(value.isString());
            } else if (numbers.contains(field)) {
                require(value.isNumber() && Double.isFinite(value.doubleValue()) && value.doubleValue() >= 0);
            } else {
                require(value.isBoolean());
            }
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
            // Older Agent responses and saved conversations have no error_code field.
            if (call.has("error_code")) {
                object(call, "server", "tool", "args", "result", "error", "error_code");
            } else {
                object(call, "server", "tool", "args", "result", "error");
            }
            text(call.get("server"));
            text(call.get("tool"));
            require(call.get("args").isObject());
            nullableText(call.get("error"));
            if (call.has("error_code") && !call.get("error_code").isNull()) {
                oneOf(call.get("error_code"), PUBLIC_TOOL_ERROR_CODES);
                require(!call.get("error").isNull());
            }
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
