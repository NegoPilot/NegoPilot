package com.negopilot.backend.supplier;

import com.negopilot.backend.common.api.ApiException;

import java.util.Map;
import java.util.Set;
import org.springframework.stereotype.Component;
import tools.jackson.databind.node.JsonNodeFactory;
import tools.jackson.databind.node.ObjectNode;

@Component
final class SupplierLookupRequestValidator {
    private static final Set<String> SEARCH_FIELDS = Set.of(
            "process", "material", "region", "iatf_required", "iso14001_required", "min_monthly_capacity", "limit");

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
        for (String key : new String[]{"iatf_required", "iso14001_required"}) {
            String certification = parameters.containsKey(key) ? parameters.get(key)[0] : "false";
            if (!(certification.equals("true") || certification.equals("false"))) throw invalid();
            result.put(key, Boolean.parseBoolean(certification));
        }

        if (parameters.containsKey("min_monthly_capacity")) {
            result.put("min_monthly_capacity", boundedInteger(parameters.get("min_monthly_capacity")[0], 1, 10_000_000));
        }
        String rawLimit = parameters.containsKey("limit") ? parameters.get("limit")[0] : "20";
        return result.put("limit", boundedInteger(rawLimit, 1, 100));
    }

    String supplierId(String raw) {
        if (raw == null || !raw.matches("SUP-[0-9]{4,18}")) throw invalid();
        return raw;
    }

    private static String normalizedText(String raw, int maxLength) {
        String value = trimWhitespace(raw);
        int length = value.codePointCount(0, value.length());
        if (length < 1 || length > maxLength) throw invalid();
        return value;
    }

    private static int boundedInteger(String raw, int min, int max) {
        if (!raw.matches("[0-9]+")) throw invalid();
        int value;
        try {
            value = Integer.parseInt(raw);
        } catch (NumberFormatException ignored) {
            throw invalid();
        }
        if (value < min || value > max) throw invalid();
        return value;
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
