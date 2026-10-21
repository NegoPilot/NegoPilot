package com.negopilot.backend;

import java.time.LocalDate;
import java.time.format.DateTimeParseException;
import java.util.HashSet;
import java.util.Set;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;

/**
 * Checks the v2 live lookup contract. Python reads business-mcp, so values cannot be compared
 * with a fixture. Shape, data sources, requested filters and ordering are checked instead.
 */
@Component
final class SupplierLookupResponseValidator {
    private static final Set<String> PROFILE_SOURCES = Set.of("DART", "MOCK", "MANUAL");
    private static final Set<String> FINANCIAL_SOURCES = Set.of("DART", "MOCK");
    private static final Set<String> FINANCIAL_SUMMARIES = Set.of("DART", "MOCK", "UNKNOWN", "MIXED");
    private static final Set<String> OPERATION_SOURCES = Set.of("MOCK", "MANUAL", "MIXED");

    void validateCatalog(JsonNode body) {
        JsonNode data = data(body);
        object(data, "processes", "regions");
        strings(data.get("regions"), 100);
        array(data.get("processes"));
        Set<String> processes = new HashSet<>();
        for (JsonNode entry : data.get("processes")) {
            object(entry, "process", "materials");
            require(processes.add(text(entry.get("process"), 100)));
            strings(entry.get("materials"), 100);
        }
    }

    void validateSearch(JsonNode body, JsonNode filters) {
        JsonNode data = data(body);
        object(data, "items", "returned_count", "limit");
        JsonNode items = data.get("items");
        array(items);
        integer(data.get("returned_count"), 0, 100);
        integer(data.get("limit"), 1, 100);
        require(filters != null && filters.isObject());
        String process = text(filters.get("process"), 100);
        String material = optionalText(filters.get("material"));
        String region = optionalText(filters.get("region"));
        boolean iatf = flag(filters, "iatf_required");
        boolean iso = flag(filters, "iso14001_required");
        int minCapacity = 0;
        if (filters.has("min_monthly_capacity")) {
            integer(filters.get("min_monthly_capacity"), 1, 10_000_000);
            minCapacity = filters.get("min_monthly_capacity").intValue();
        }
        int limit = filters.has("limit") ? filters.get("limit").intValue() : 20;
        require(data.get("limit").intValue() == limit);
        require(items.size() <= limit && data.get("returned_count").intValue() == items.size());
        String previous = null;
        for (JsonNode item : items) {
            object(item, "supplier_id", "name", "region", "iatf16949", "iso14001", "matched_capabilities",
                    "provenance");
            String id = supplierId(item.get("supplier_id"));
            require(previous == null || previous.compareTo(id) < 0);
            previous = id;
            text(item.get("name"), 100);
            require(region == null || region.equals(text(item.get("region"), 100)));
            require(item.get("iatf16949").isBoolean() && (!iatf || item.get("iatf16949").booleanValue()));
            require(item.get("iso14001").isBoolean() && (!iso || item.get("iso14001").booleanValue()));
            provenance(item.get("provenance"));
            capabilities(item.get("matched_capabilities"));
            for (JsonNode capability : item.get("matched_capabilities")) {
                require(process.equals(capability.get("process").stringValue())
                        && (material == null || material.equals(capability.get("material").stringValue()))
                        && capability.get("monthly_capacity").intValue() >= minCapacity);
            }
        }
    }

    void validateDetail(JsonNode body, String supplierId) {
        JsonNode supplier = data(body);
        object(supplier, "supplier_id", "name", "region", "iatf16949", "iso14001", "capabilities", "quality",
                "financials", "recent_quotes", "existing_contract", "provenance");
        literal(supplier.get("supplier_id"), supplierId);
        supplierId(supplier.get("supplier_id"));
        text(supplier.get("name"), 100);
        text(supplier.get("region"), 100);
        require(supplier.get("iatf16949").isBoolean() && supplier.get("iso14001").isBoolean());
        capabilities(supplier.get("capabilities"));
        JsonNode quality = supplier.get("quality");
        object(quality, "defect_ppm", "on_time_delivery_rate");
        nullableNumber(quality.get("defect_ppm"));
        nullableNumber(quality.get("on_time_delivery_rate"));
        array(supplier.get("financials"));
        Set<String> financialSources = new HashSet<>();
        for (JsonNode financial : supplier.get("financials")) {
            object(financial, "year", "revenue_krw", "operating_profit_krw", "debt_ratio", "source");
            integer(financial.get("year"), 1900, 9999);
            nullableNumber(financial.get("revenue_krw"));
            nullableNumber(financial.get("operating_profit_krw"));
            nullableNumber(financial.get("debt_ratio"));
            String source = text(financial.get("source"), 100);
            require(FINANCIAL_SOURCES.contains(source));
            financialSources.add(source);
        }
        array(supplier.get("recent_quotes"));
        for (JsonNode quote : supplier.get("recent_quotes")) {
            object(quote, "process", "material", "unit_price_krw", "quoted_at");
            text(quote.get("process"), 100);
            text(quote.get("material"), 100);
            require(quote.get("unit_price_krw").isNumber() && Double.isFinite(quote.get("unit_price_krw").doubleValue()));
            String date = text(quote.get("quoted_at"), 10);
            require(date.matches("[0-9]{4}-[0-9]{2}-[0-9]{2}"));
            try { LocalDate.parse(date); } catch (DateTimeParseException ignored) { require(false); }
        }
        JsonNode contract = supplier.get("existing_contract");
        if (!contract.isNull()) {
            object(contract, "payment_days", "price_linkage");
            integer(contract.get("payment_days"), 0, Integer.MAX_VALUE);
            require(contract.get("price_linkage").isBoolean());
        }
        provenance(supplier.get("provenance"));
        String financialSummary = financialSources.isEmpty() ? "UNKNOWN"
                : financialSources.size() == 1 ? financialSources.iterator().next() : "MIXED";
        literal(supplier.get("provenance").get("financials"), financialSummary);
    }

    private static JsonNode data(JsonNode body) {
        object(body, "schema_version", "mode", "data");
        literal(body.get("schema_version"), "0.3");
        literal(body.get("mode"), "live");
        return body.get("data");
    }

    private static boolean flag(JsonNode filters, String field) {
        if (!filters.has(field)) return false;
        require(filters.get(field).isBoolean());
        return filters.get(field).booleanValue();
    }

    private static void provenance(JsonNode provenance) {
        object(provenance, "profile", "financials", "operations");
        require(PROFILE_SOURCES.contains(text(provenance.get("profile"), 100)));
        require(FINANCIAL_SUMMARIES.contains(text(provenance.get("financials"), 100)));
        require(OPERATION_SOURCES.contains(text(provenance.get("operations"), 100)));
    }

    private static String supplierId(JsonNode node) {
        String value = text(node, 22);
        require(value.matches("SUP-[0-9]{4,18}"));
        return value;
    }

    private static void capabilities(JsonNode capabilities) {
        array(capabilities);
        require(!capabilities.isEmpty());
        for (JsonNode capability : capabilities) {
            object(capability, "process", "material", "monthly_capacity");
            text(capability.get("process"), 100);
            text(capability.get("material"), 100);
            integer(capability.get("monthly_capacity"), 0, Long.MAX_VALUE);
        }
    }

    private static void object(JsonNode node, String... fields) {
        require(node != null && node.isObject() && node.size() == fields.length);
        for (String field : fields) require(node.has(field));
    }

    private static void array(JsonNode node) { require(node != null && node.isArray()); }

    private static String text(JsonNode node, int maximum) {
        require(node != null && node.isString());
        String value = node.stringValue();
        require(value.codePointCount(0, value.length()) <= maximum && value.codePoints().anyMatch(
                cp -> !Character.isWhitespace(cp) && !Character.isSpaceChar(cp) && cp != 0x85));
        return value;
    }

    private static String optionalText(JsonNode node) { return node == null || node.isNull() ? null : text(node, 100); }

    private static void strings(JsonNode node, int maximum) {
        array(node);
        Set<String> values = new HashSet<>();
        for (JsonNode item : node) require(values.add(text(item, maximum)));
    }

    private static void literal(JsonNode node, String expected) { require(text(node, 4000).equals(expected)); }

    private static void integer(JsonNode node, long minimum, long maximum) {
        require(node != null && node.isIntegralNumber() && node.canConvertToLong());
        require(node.longValue() >= minimum && node.longValue() <= maximum);
    }

    private static void nullableNumber(JsonNode node) {
        require(node != null && (node.isNull() || (node.isNumber() && Double.isFinite(node.doubleValue()))));
    }

    private static void require(boolean condition) {
        if (!condition) throw new IllegalArgumentException("Invalid supplier lookup response");
    }
}
