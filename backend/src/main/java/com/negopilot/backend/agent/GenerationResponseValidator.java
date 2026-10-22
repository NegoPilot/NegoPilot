package com.negopilot.backend.agent;

import java.math.BigInteger;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;

/** Validates the full-graph HTTP envelope without changing the generated Answer. */
@Component
public final class GenerationResponseValidator {
    public void validate(JsonNode response, UUID expectedRequestId, JsonNode expectedContext) {
        object(response, "schema_version", "request_id", "mode", "execution_status", "generation_status",
                "context", "decomposition", "routing", "answer", "error");
        literal(response.get("schema_version"), "0.3");
        oneOf(response.get("mode"), Set.of("live", "stub"));
        String requestId = text(response.get("request_id"));
        require(requestId.matches("[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"));
        require(UUID.fromString(requestId).equals(expectedRequestId));
        context(response.get("context"));
        context(expectedContext);
        require(response.get("context").equals(expectedContext));

        JsonNode decomposition = response.get("decomposition");
        JsonNode routing = response.get("routing");
        try {
            DecompositionRoutingValidator.validateDecomposition(decomposition);
            DecompositionRoutingValidator.validateRouting(routing, decomposition);
        } catch (IllegalArgumentException ignored) {
            throw invalid();
        }
        Set<String> subQueryIds = new HashSet<>();
        for (JsonNode subQuery : decomposition.get("sub_queries")) subQueryIds.add(subQuery.get("id").stringValue());
        String kind = answer(response.get("answer"), subQueryIds);

        // Generation has its own status mapping. Partial Routing errors can still produce an Answer.
        if (kind.equals("failed")) {
            literal(response.get("execution_status"), "FAILED");
            literal(response.get("generation_status"), "FAILED");
            JsonNode error = response.get("error");
            object(error, "code", "message", "retryable");
            boolean decompositionFailed = decomposition.get("status").stringValue().equals("failed");
            literal(error.get("code"), decompositionFailed ? "DECOMPOSITION_FAILED" : "GENERATION_FAILED");
            literal(error.get("message"), decompositionFailed ? "Query decomposition failed." : "Answer generation failed.");
            require(error.get("retryable").isBoolean() && !error.get("retryable").booleanValue());
        } else {
            literal(response.get("execution_status"), kind.equals("clarify") ? "NEEDS_INPUT" : "COMPLETED");
            literal(response.get("generation_status"), "COMPLETED");
            require(response.get("error").isNull());
        }
    }

    private static String answer(JsonNode answer, Set<String> subQueryIds) {
        objectWithOptional(answer, "suggested_questions", "kind", "text", "citations", "options",
                "grounded", "notices", "evidence", "verification");
        String kind = oneOf(answer.get("kind"), Set.of("answer", "clarify", "refusal", "no_result", "failed"));
        String answerText = text(answer.get("text"));
        require(answerText.codePoints().anyMatch(cp ->
                !Character.isWhitespace(cp) && !Character.isSpaceChar(cp) && cp != 0x85));
        strings(answer.get("options"));
        if (answer.has("suggested_questions")) suggestedQuestions(answer.get("suggested_questions"));
        require(answer.get("grounded").isBoolean());
        strings(answer.get("notices"));
        array(answer.get("evidence"));
        Map<String, JsonNode> evidenceById = new HashMap<>();
        for (JsonNode evidence : answer.get("evidence")) {
            object(evidence, "id", "sub_query", "source", "kind", "label", "text", "citable", "source_url", "meta");
            String id = text(evidence.get("id"));
            require(evidenceById.put(id, evidence) == null);
            require(subQueryIds.contains(text(evidence.get("sub_query"))));
            oneOf(evidence.get("source"), Set.of("law", "business"));
            text(evidence.get("kind"));
            text(evidence.get("label"));
            text(evidence.get("text"));
            require(evidence.get("citable").isBoolean());
            nullableText(evidence.get("source_url"));
            require(evidence.get("meta").isObject());
        }
        array(answer.get("citations"));
        Set<BigInteger> numbers = new HashSet<>();
        Set<String> citedIds = new HashSet<>();
        for (JsonNode citation : answer.get("citations")) {
            object(citation, "n", "evidence_id", "label", "source_url");
            require(citation.get("n").isIntegralNumber());
            require(numbers.add(citation.get("n").bigIntegerValue()));
            String evidenceId = text(citation.get("evidence_id"));
            require(citedIds.add(evidenceId));
            JsonNode evidence = evidenceById.get(evidenceId);
            require(evidence != null && evidence.get("citable").booleanValue());
            require(text(citation.get("label")).equals(evidence.get("label").stringValue()));
            nullableText(citation.get("source_url"));
            require(citation.get("source_url").equals(evidence.get("source_url")));
        }
        JsonNode verification = answer.get("verification");
        if (!verification.isNull()) {
            object(verification, "attempts", "unknown_citations", "ungrounded_numbers", "ungrounded_articles", "masked");
            require(verification.get("attempts").isIntegralNumber());
            strings(verification.get("unknown_citations"));
            strings(verification.get("ungrounded_numbers"));
            strings(verification.get("ungrounded_articles"));
            require(verification.get("masked").isBoolean());
        }
        return kind;
    }

    private static void suggestedQuestions(JsonNode node) {
        array(node);
        Set<String> seen = new HashSet<>();
        for (JsonNode item : node) {
            String question = text(item);
            require(!question.isEmpty() && question.codePointCount(0, question.length()) <= 120);
            require(!whitespace(question.codePointAt(0)) && !whitespace(question.codePointBefore(question.length())));
            require(seen.add(question));
        }
    }

    private static boolean whitespace(int codePoint) {
        // Match Python's suggestion normalization plus the browser's BOM trimming.
        return Character.isWhitespace(codePoint) || Character.isSpaceChar(codePoint)
                || codePoint == 0x85 || codePoint == 0xfeff;
    }

    private static void objectWithOptional(JsonNode node, String optional, String... fields) {
        require(node != null && node.isObject());
        require(node.size() == fields.length + (node.has(optional) ? 1 : 0));
        for (String field : fields) require(node.has(field));
    }

    private static void context(JsonNode context) {
        object(context, "selected_supplier_ids");
        JsonNode selected = context.get("selected_supplier_ids");
        array(selected);
        require(selected.size() <= 1);
        for (JsonNode id : selected) require(text(id).matches("[A-Za-z0-9_-]{1,100}"));
    }

    private static void object(JsonNode node, String... fields) {
        require(node != null && node.isObject() && node.size() == fields.length);
        for (String field : fields) require(node.has(field));
    }

    private static void array(JsonNode node) { require(node != null && node.isArray()); }

    private static void strings(JsonNode node) {
        array(node);
        for (JsonNode value : node) text(value);
    }

    private static String text(JsonNode node) {
        require(node != null && node.isString());
        return node.stringValue();
    }

    private static void nullableText(JsonNode node) { require(node != null && (node.isNull() || node.isString())); }

    private static void literal(JsonNode node, String expected) { require(text(node).equals(expected)); }

    private static String oneOf(JsonNode node, Set<String> allowed) {
        String value = text(node);
        require(allowed.contains(value));
        return value;
    }

    private static void require(boolean condition) { if (!condition) throw invalid(); }

    private static IllegalArgumentException invalid() { return new IllegalArgumentException("Invalid generation response"); }
}
