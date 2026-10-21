package com.negopilot.backend;

import java.util.Map;
import java.util.Set;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.JsonNodeFactory;
import tools.jackson.databind.node.ObjectNode;

@Component
final class SupplierChatRequestValidator {
    private static final Set<String> SEARCH_FIELDS = Set.of("process", "material", "region", "iatf_required", "limit");

    record MessageRequest(String query, ObjectNode context) {}

    ObjectNode search(Map<String, String[]> parameters) {
        if (parameters == null || !parameters.containsKey("process")) throw invalid();
        for (Map.Entry<String, String[]> entry : parameters.entrySet()) {
            if (entry.getKey() == null || !SEARCH_FIELDS.contains(entry.getKey()) || entry.getValue() == null
                    || entry.getValue().length != 1 || entry.getValue()[0] == null) {
                throw invalid();
            }
        }
        ObjectNode result = JsonNodeFactory.instance.objectNode();
        for (String key : Set.of("process", "material", "region")) {
            if (parameters.containsKey(key)) result.put(key, normalizedText(parameters.get(key)[0], 100));
        }
        String certification = parameters.containsKey("iatf_required") ? parameters.get("iatf_required")[0] : "false";
        if (!(certification.equals("true") || certification.equals("false"))) throw invalid();
        result.put("iatf_required", Boolean.parseBoolean(certification));

        String rawLimit = parameters.containsKey("limit") ? parameters.get("limit")[0] : "20";
        if (!rawLimit.matches("[0-9]+")) throw invalid();
        int limit;
        try {
            limit = Integer.parseInt(rawLimit);
        } catch (NumberFormatException ignored) {
            throw invalid();
        }
        if (limit < 1 || limit > 100) throw invalid();
        return result.put("limit", limit);
    }

    ObjectNode create(JsonNode body) {
        if (!onlyFields(body, Set.of("context")) || !body.has("context")) throw invalid();
        return context(body.get("context"));
    }

    MessageRequest message(JsonNode body) {
        if (!onlyFields(body, Set.of("query", "context")) || !body.path("query").isString()) throw invalid();
        String query = normalizedText(body.get("query").stringValue(), 4000);
        return new MessageRequest(query, body.has("context") ? context(body.get("context")) : null);
    }

    String conversationId(String raw) {
        // Preserve case for the service's exact ID lookup instead of normalizing UUID aliases.
        if (raw == null || !raw.matches("[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")) {
            throw invalid();
        }
        return raw;
    }

    String supplierId(String raw) {
        return checkedSupplierId(raw);
    }

    private static ObjectNode context(JsonNode context) {
        if (!onlyFields(context, Set.of("selected_supplier_ids"))) throw invalid();
        JsonNode selected = context.get("selected_supplier_ids");
        if (selected == null || !selected.isArray() || selected.size() > 1) throw invalid();
        for (JsonNode id : selected) {
            if (!id.isString()) throw invalid();
            checkedSupplierId(id.stringValue());
        }
        return ((ObjectNode) context).deepCopy();
    }

    private static boolean onlyFields(JsonNode body, Set<String> allowed) {
        return body != null && body.isObject() && body.propertyNames().stream().allMatch(allowed::contains);
    }

    private static String normalizedText(String raw, int maxLength) {
        String value = trimWhitespace(raw);
        int length = value.codePointCount(0, value.length());
        if (length < 1 || length > maxLength) throw invalid();
        return value;
    }

    private static String checkedSupplierId(String raw) {
        if (raw == null || !raw.matches("[A-Za-z0-9_-]{1,100}")) {
            throw invalid();
        }
        return raw;
    }

    private static String trimWhitespace(String value) {
        int start = 0;
        int end = value.length();
        while (start < end && whitespace(value.codePointAt(start))) start += Character.charCount(value.codePointAt(start));
        while (end > start && whitespace(value.codePointBefore(end))) end -= Character.charCount(value.codePointBefore(end));
        return value.substring(start, end);
    }

    private static boolean whitespace(int codePoint) {
        return Character.isWhitespace(codePoint) || Character.isSpaceChar(codePoint) || codePoint == 0x85;
    }

    private static ApiException invalid() {
        return ApiException.validation(null);
    }
}
