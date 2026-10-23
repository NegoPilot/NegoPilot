package com.negopilot.backend.supplier;

import com.negopilot.backend.common.api.ApiException;
import java.util.Map;
import java.util.Set;
import org.springframework.stereotype.Component;
import tools.jackson.databind.node.JsonNodeFactory;
import tools.jackson.databind.node.ObjectNode;

@Component
final class SupplierLookupRequestValidator {
    static final Set<String> SORTS = Set.of("name", "region", "founded_year", "monthly_capacity", "defect_ppm",
            "on_time_delivery_rate", "audit_grade", "years_dealing", "payment_days", "raw_material_ratio",
            "latest_quote_date", "latest_quote_price");
    private static final Set<String> SEARCH_FIELDS = Set.of("q", "process", "material", "region", "iatf_required",
            "iso14001_required", "min_monthly_capacity", "limit", "page", "size", "sort", "direction");
    private static final Set<String> QUOTE_FIELDS = Set.of("process", "material", "page", "size", "sort", "direction");

    ObjectNode search(Map<String, String[]> parameters) {
        parameters(parameters, SEARCH_FIELDS);
        ObjectNode result = JsonNodeFactory.instance.objectNode();
        for (String key : Set.of("q", "process", "material", "region")) {
            if (!parameters.containsKey(key)) continue;
            String value = trimWhitespace(parameters.get(key)[0]);
            if (key.equals("q") && value.isEmpty()) continue;
            result.put(key, normalizedText(value, 100));
        }
        for (String key : new String[]{"iatf_required", "iso14001_required"}) {
            String certification = value(parameters, key, "false");
            if (!(certification.equals("true") || certification.equals("false"))) throw invalid();
            result.put(key, Boolean.parseBoolean(certification));
        }
        if (parameters.containsKey("min_monthly_capacity")) {
            result.put("min_monthly_capacity", boundedInteger(parameters.get("min_monthly_capacity")[0], 1, 10_000_000));
        }
        if (parameters.containsKey("limit") && (parameters.containsKey("page") || parameters.containsKey("size"))) {
            throw invalid();
        }
        page(result, parameters, SORTS, "name", "asc");
        if (parameters.containsKey("limit")) result.put("size", boundedInteger(parameters.get("limit")[0], 1, 100));
        if (result.get("sort").stringValue().equals("latest_quote_price")
                && (!result.has("process") || !result.has("material"))) throw invalid();
        return result;
    }

    ObjectNode quotes(Map<String, String[]> parameters) {
        parameters(parameters, QUOTE_FIELDS);
        ObjectNode result = JsonNodeFactory.instance.objectNode();
        for (String key : Set.of("process", "material")) {
            if (parameters.containsKey(key)) result.put(key, normalizedText(parameters.get(key)[0], 100));
        }
        page(result, parameters, Set.of("quoted_at", "unit_price_krw"), "quoted_at", "desc");
        if (result.get("sort").stringValue().equals("unit_price_krw")
                && (!result.has("process") || !result.has("material"))) throw invalid();
        return result;
    }

    String supplierId(String raw) {
        if (raw == null || !raw.matches("SUP-[0-9]{4,18}")) throw invalid();
        return raw;
    }

    private static void page(ObjectNode result, Map<String, String[]> parameters, Set<String> sorts,
                             String defaultSort, String defaultDirection) {
        result.put("page", boundedInteger(value(parameters, "page", "0"), 0, 1_000_000));
        result.put("size", boundedInteger(value(parameters, "size", "20"), 1, 100));
        String sort = value(parameters, "sort", defaultSort);
        String direction = value(parameters, "direction", defaultDirection);
        if (!sorts.contains(sort) || !Set.of("asc", "desc").contains(direction)) throw invalid();
        result.put("sort", sort).put("direction", direction);
    }

    private static void parameters(Map<String, String[]> parameters, Set<String> fields) {
        if (parameters == null) throw invalid();
        for (Map.Entry<String, String[]> entry : parameters.entrySet()) {
            if (entry.getKey() == null || !fields.contains(entry.getKey()) || entry.getValue() == null
                    || entry.getValue().length != 1 || entry.getValue()[0] == null) throw invalid();
        }
    }

    private static String value(Map<String, String[]> parameters, String key, String fallback) {
        return parameters.containsKey(key) ? parameters.get(key)[0] : fallback;
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
        try { value = Integer.parseInt(raw); }
        catch (NumberFormatException ignored) { throw invalid(); }
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

    private static ApiException invalid() { return ApiException.validation(null); }
}
