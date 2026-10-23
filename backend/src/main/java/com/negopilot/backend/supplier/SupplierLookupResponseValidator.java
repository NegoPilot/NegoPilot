package com.negopilot.backend.supplier;

import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.time.format.DateTimeParseException;
import java.util.HashSet;
import java.util.Locale;
import java.util.Set;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;

/** Validates live database results by type, provenance, requested filters and stable page ordering. */
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
        object(data, "items", "page", "size", "returned_count", "total", "total_pages", "sort", "direction");
        page(data, filters);
        String process = optionalText(filters.get("process"));
        String material = optionalText(filters.get("material"));
        String region = optionalText(filters.get("region"));
        String query = optionalText(filters.get("q"));
        boolean iatf = flag(filters, "iatf_required");
        boolean iso = flag(filters, "iso14001_required");
        long minCapacity = filters.has("min_monthly_capacity") ? filters.get("min_monthly_capacity").longValue() : 0;
        boolean requiresCapability = process != null || material != null || filters.has("min_monthly_capacity");
        String sort = text(data.get("sort"), 100);
        require(SupplierLookupRequestValidator.SORTS.contains(sort));
        boolean ascending = data.get("direction").stringValue().equals("asc");
        Set<String> ids = new HashSet<>();
        JsonNode previous = null;
        for (JsonNode item : data.get("items")) {
            object(item, "supplier_id", "name", "region", "iatf16949", "iso14001", "matched_capabilities",
                    "provenance", "founded_year", "employees", "quality", "existing_contract",
                    "max_monthly_capacity", "latest_quote", "updated_at");
            String id = supplierId(item.get("supplier_id"));
            require(ids.add(id));
            profile(item);
            require(query == null || lower(item.get("name")).contains(query.toLowerCase(Locale.ROOT))
                    || id.toLowerCase(Locale.ROOT).contains(query.toLowerCase(Locale.ROOT)));
            require(region == null || region.equals(item.get("region").stringValue()));
            require(!iatf || item.get("iatf16949").booleanValue());
            require(!iso || item.get("iso14001").booleanValue());
            quality(item.get("quality"));
            contract(item.get("existing_contract"));
            JsonNode capabilities = item.get("matched_capabilities");
            capabilities(capabilities);
            require(!requiresCapability || !capabilities.isEmpty());
            long max = -1;
            for (JsonNode capability : capabilities) {
                require(process == null || process.equals(capability.get("process").stringValue()));
                require(material == null || material.equals(capability.get("material").stringValue()));
                long capacity = capability.get("monthly_capacity").longValue();
                require(capacity >= minCapacity);
                max = Math.max(max, capacity);
            }
            nullableInteger(item.get("max_monthly_capacity"), 0, Long.MAX_VALUE);
            require(max < 0 ? item.get("max_monthly_capacity").isNull()
                    : !item.get("max_monthly_capacity").isNull() && item.get("max_monthly_capacity").longValue() == max);
            if (!item.get("latest_quote").isNull()) {
                quote(item.get("latest_quote"));
                quoteFilters(item.get("latest_quote"), process, material);
            }
            if (previous != null) require(ordered(previous, item, sort, ascending));
            previous = item;
        }
    }

    void validateDetail(JsonNode body, String requestedId) {
        JsonNode supplier = data(body);
        object(supplier, "supplier_id", "name", "region", "iatf16949", "iso14001", "capabilities", "quality",
                "financials", "recent_quotes", "existing_contract", "provenance", "founded_year", "employees",
                "corp_code", "stock_code", "induty_code", "biz_no_masked", "created_at", "updated_at");
        literal(supplier.get("supplier_id"), requestedId);
        supplierId(supplier.get("supplier_id"));
        profile(supplier);
        for (String key : Set.of("corp_code", "stock_code", "induty_code", "biz_no_masked")) optionalText(supplier.get(key));
        timestamp(supplier.get("created_at"));
        capabilities(supplier.get("capabilities"));
        quality(supplier.get("quality"));
        contract(supplier.get("existing_contract"));
        array(supplier.get("financials"));
        Set<String> financialSources = new HashSet<>();
        Set<String> financialIds = new HashSet<>();
        for (JsonNode financial : supplier.get("financials")) {
            object(financial, "year", "fs_div", "revenue_krw", "operating_profit_krw", "debt_ratio", "source",
                    "net_income_krw", "total_assets_krw", "total_liabilities_krw", "total_equity_krw");
            integer(financial.get("year"), 1900, 9999);
            require(Set.of("CFS", "OFS").contains(text(financial.get("fs_div"), 3)));
            require(financialIds.add(financial.get("year").asString() + ":" + financial.get("fs_div").stringValue()));
            for (String key : Set.of("revenue_krw", "operating_profit_krw", "debt_ratio", "net_income_krw",
                    "total_assets_krw", "total_liabilities_krw", "total_equity_krw")) nullableNumber(financial.get(key));
            String source = text(financial.get("source"), 100);
            require(FINANCIAL_SOURCES.contains(source));
            financialSources.add(source);
        }
        array(supplier.get("recent_quotes"));
        require(supplier.get("recent_quotes").size() <= 5);
        Set<Long> quoteIds = new HashSet<>();
        JsonNode previous = null;
        for (JsonNode quote : supplier.get("recent_quotes")) {
            quote(quote);
            require(quoteIds.add(quote.get("quote_id").longValue()));
            if (previous != null) require(quoteOrdered(previous, quote, "quoted_at", false));
            previous = quote;
        }
        String financialSummary = financialSources.isEmpty() ? "UNKNOWN"
                : financialSources.size() == 1 ? financialSources.iterator().next() : "MIXED";
        literal(supplier.get("provenance").get("financials"), financialSummary);
    }

    void validateQuotes(JsonNode body, String requestedId, JsonNode filters) {
        JsonNode data = data(body);
        object(data, "supplier_id", "items", "page", "size", "returned_count", "total", "total_pages", "sort", "direction");
        literal(data.get("supplier_id"), requestedId);
        supplierId(data.get("supplier_id"));
        page(data, filters);
        String sort = text(data.get("sort"), 100);
        require(Set.of("quoted_at", "unit_price_krw").contains(sort));
        boolean ascending = data.get("direction").stringValue().equals("asc");
        Set<Long> ids = new HashSet<>();
        JsonNode previous = null;
        for (JsonNode quote : data.get("items")) {
            quote(quote);
            require(ids.add(quote.get("quote_id").longValue()));
            quoteFilters(quote, optionalText(filters.get("process")), optionalText(filters.get("material")));
            if (previous != null) require(quoteOrdered(previous, quote, sort, ascending));
            previous = quote;
        }
    }

    private static void page(JsonNode data, JsonNode filters) {
        require(filters != null && filters.isObject());
        integer(data.get("page"), 0, 1_000_000);
        integer(data.get("size"), 1, 100);
        integer(data.get("returned_count"), 0, 100);
        integer(data.get("total"), 0, Long.MAX_VALUE);
        integer(data.get("total_pages"), 0, Long.MAX_VALUE);
        for (String key : Set.of("page", "size", "sort", "direction")) require(data.get(key).equals(filters.get(key)));
        require(Set.of("asc", "desc").contains(text(data.get("direction"), 4)));
        array(data.get("items"));
        long total = data.get("total").longValue();
        int size = data.get("size").intValue();
        long pages = total / size + (total % size == 0 ? 0 : 1);
        long expected = Math.min(size, Math.max(0, total - data.get("page").longValue() * size));
        require(data.get("total_pages").longValue() == pages);
        require(data.get("returned_count").intValue() == data.get("items").size());
        require(data.get("items").size() == expected);
    }

    private static void profile(JsonNode item) {
        text(item.get("name"), 100);
        text(item.get("region"), 100);
        require(item.get("iatf16949").isBoolean() && item.get("iso14001").isBoolean());
        nullableInteger(item.get("founded_year"), 1, 9999);
        nullableInteger(item.get("employees"), 0, Long.MAX_VALUE);
        timestamp(item.get("updated_at"));
        provenance(item.get("provenance"));
    }

    private static void quality(JsonNode node) {
        object(node, "defect_ppm", "on_time_delivery_rate", "audit_grade", "years_dealing");
        nullableRange(node.get("defect_ppm"), 0, Double.MAX_VALUE);
        nullableRange(node.get("on_time_delivery_rate"), 0, 100);
        require(node.get("audit_grade").isNull() || Set.of("A", "B", "C", "D").contains(text(node.get("audit_grade"), 1)));
        nullableInteger(node.get("years_dealing"), 0, Integer.MAX_VALUE);
    }

    private static void contract(JsonNode node) {
        require(node != null);
        if (node.isNull()) return;
        object(node, "payment_days", "price_linkage", "raw_material_ratio", "auto_renewal");
        integer(node.get("payment_days"), 0, Integer.MAX_VALUE);
        require(node.get("price_linkage").isBoolean() && node.get("auto_renewal").isBoolean());
        require(node.get("raw_material_ratio").isNumber());
        nullableRange(node.get("raw_material_ratio"), 0, 1);
    }

    private static void quote(JsonNode node) {
        object(node, "quote_id", "process", "material", "unit_price_krw", "quoted_at", "source");
        require(Set.of("MOCK", "MANUAL").contains(text(node.get("source"), 10)));
        integer(node.get("quote_id"), 1, Long.MAX_VALUE);
        text(node.get("process"), 100);
        text(node.get("material"), 100);
        require(node.get("unit_price_krw").isNumber());
        nullableRange(node.get("unit_price_krw"), 0, Double.MAX_VALUE);
        String date = text(node.get("quoted_at"), 10);
        require(date.matches("[0-9]{4}-[0-9]{2}-[0-9]{2}"));
        try { LocalDate.parse(date); } catch (DateTimeParseException ignored) { require(false); }
    }

    private static void quoteFilters(JsonNode quote, String process, String material) {
        require(process == null || process.equals(quote.get("process").stringValue()));
        require(material == null || material.equals(quote.get("material").stringValue()));
    }

    private static boolean ordered(JsonNode previous, JsonNode current, String sort, boolean ascending) {
        JsonNode left = sortValue(previous, sort);
        JsonNode right = sortValue(current, sort);
        int comparison = compare(left, right, ascending, sort.equals("name") || sort.equals("region"));
        return comparison < 0 || comparison == 0
                && previous.get("supplier_id").stringValue().compareTo(current.get("supplier_id").stringValue()) < 0;
    }

    private static JsonNode sortValue(JsonNode item, String sort) {
        return switch (sort) {
            case "monthly_capacity" -> item.get("max_monthly_capacity");
            case "defect_ppm", "on_time_delivery_rate", "audit_grade", "years_dealing" -> item.get("quality").get(sort);
            case "payment_days", "raw_material_ratio" -> item.get("existing_contract").path(sort);
            case "latest_quote_date" -> item.get("latest_quote").path("quoted_at");
            case "latest_quote_price" -> item.get("latest_quote").path("unit_price_krw");
            default -> item.get(sort);
        };
    }

    private static boolean quoteOrdered(JsonNode previous, JsonNode current, String sort, boolean ascending) {
        int comparison = compare(previous.get(sort), current.get(sort), ascending, false);
        return comparison < 0 || comparison == 0 && previous.get("quote_id").longValue() > current.get("quote_id").longValue();
    }

    private static int compare(JsonNode left, JsonNode right, boolean ascending, boolean caseInsensitive) {
        boolean leftNull = left == null || left.isNull() || left.isMissingNode();
        boolean rightNull = right == null || right.isNull() || right.isMissingNode();
        if (leftNull || rightNull) return leftNull == rightNull ? 0 : leftNull ? 1 : -1;
        int result = left.isNumber() ? left.decimalValue().compareTo(right.decimalValue())
                : unicodeCompare(caseInsensitive ? lower(left) : left.stringValue(), caseInsensitive ? lower(right) : right.stringValue());
        return ascending ? result : -result;
    }

    private static int unicodeCompare(String left, String right) {
        int a = 0;
        int b = 0;
        while (a < left.length() && b < right.length()) {
            int x = left.codePointAt(a);
            int y = right.codePointAt(b);
            if (x != y) return Integer.compare(x, y);
            a += Character.charCount(x);
            b += Character.charCount(y);
        }
        return Integer.compare(left.length() - a, right.length() - b);
    }

    private static String lower(JsonNode node) { return node.stringValue().toLowerCase(Locale.ROOT); }

    private static JsonNode data(JsonNode body) {
        object(body, "schema_version", "mode", "data");
        literal(body.get("schema_version"), "0.4");
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
        Set<String> keys = new HashSet<>();
        for (JsonNode capability : capabilities) {
            object(capability, "process", "material", "monthly_capacity");
            String process = text(capability.get("process"), 100);
            String material = text(capability.get("material"), 100);
            require(keys.add(process + "\u0000" + material));
            integer(capability.get("monthly_capacity"), 0, Long.MAX_VALUE);
        }
    }

    private static void timestamp(JsonNode node) {
        require(node != null);
        if (node.isNull()) return;
        try { OffsetDateTime.parse(text(node, 100)); } catch (DateTimeParseException ignored) { require(false); }
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

    private static void nullableInteger(JsonNode node, long minimum, long maximum) {
        require(node != null);
        if (!node.isNull()) integer(node, minimum, maximum);
    }

    private static void nullableNumber(JsonNode node) {
        require(node != null && (node.isNull() || (node.isNumber() && Double.isFinite(node.doubleValue()))));
    }

    private static void nullableRange(JsonNode node, double minimum, double maximum) {
        nullableNumber(node);
        if (!node.isNull()) require(node.doubleValue() >= minimum && node.doubleValue() <= maximum);
    }

    private static void require(boolean condition) {
        if (!condition) throw new IllegalArgumentException("Invalid supplier lookup response");
    }
}
