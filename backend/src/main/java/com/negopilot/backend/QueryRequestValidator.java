package com.negopilot.backend;

import java.util.Set;
import java.util.UUID;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ObjectNode;

@Component
final class QueryRequestValidator {
    record ValidatedRequest(UUID requestId, ObjectNode body) {}

    ValidatedRequest validate(JsonNode body) {
        UUID id = parseId(body == null ? null : body.get("request_id"));
        if (body == null || !body.isObject() || !hasOnly(body, Set.of("request_id", "query", "history"))
                || (body.has("request_id") && id == null) || !validText(body.get("query"))) {
            throw ApiException.validation(id);
        }
        JsonNode history = body.get("history");
        if (history != null) {
            if (!history.isArray() || history.size() > 20) throw ApiException.validation(id);
            for (JsonNode item : history) {
                if (!item.isObject() || !hasOnly(item, Set.of("role", "content"))
                        || !item.path("role").isString()
                        || !Set.of("user", "assistant").contains(item.path("role").stringValue())
                        || !validText(item.get("content"))) {
                    throw ApiException.validation(id);
                }
            }
        }
        if (id == null) id = UUID.randomUUID();
        ObjectNode normalized = ((ObjectNode) body).deepCopy();
        normalized.put("request_id", id.toString());
        if (history == null) normalized.putArray("history");
        return new ValidatedRequest(id, normalized);
    }

    static UUID parseId(JsonNode node) {
        if (node == null || !node.isString()) return null;
        String value = node.stringValue();
        // Match the UUID textual forms accepted by Python's Pydantic UUID field.
        if (value.matches("[0-9a-fA-F]{32}")) {
            value = value.substring(0, 8) + "-" + value.substring(8, 12) + "-" + value.substring(12, 16)
                    + "-" + value.substring(16, 20) + "-" + value.substring(20);
        } else if (value.startsWith("urn:uuid:")) {
            value = value.substring(9);
        } else if (value.startsWith("{") && value.endsWith("}")) {
            value = value.substring(1, value.length() - 1);
        }
        if (!value.matches("[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")) return null;
        return UUID.fromString(value);
    }

    private static boolean hasOnly(JsonNode node, Set<String> allowed) {
        return node.propertyNames().stream().allMatch(allowed::contains);
    }

    private static boolean validText(JsonNode node) {
        if (node == null || !node.isString()) return false;
        String value = node.stringValue();
        int length = value.codePointCount(0, value.length());
        return length >= 1 && length <= 4000
                && value.codePoints().anyMatch(cp -> !Character.isWhitespace(cp) && !Character.isSpaceChar(cp) && cp != 0x85);
    }
}
