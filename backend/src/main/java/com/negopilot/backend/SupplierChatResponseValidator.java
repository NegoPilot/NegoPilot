package com.negopilot.backend;

import java.time.LocalDate;
import java.time.format.DateTimeParseException;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.UUID;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.JsonNodeFactory;
import tools.jackson.databind.node.ObjectNode;

/**
 * Checks the v0.2 HTTP contract. Stub lookups and chat must equal the checked-in fixture.
 * Live lookups read business-mcp, so only their shape and filter consistency can be checked.
 */
public final class SupplierChatResponseValidator {
    private static final String OVERVIEW_ALIAS = "이 회사의 재무 상태와 납기 지표를 알려줘";
    private static final Set<String> STUB_SOURCES = Set.of("STUB");
    private static final Set<String> LIVE_SOURCES = Set.of("DART", "MOCK");
    private final boolean live;
    private final Map<String, JsonNode> suppliers = new TreeMap<>();
    private final Set<String> supportedQueries;
    private final Map<String, Set<String>> catalogProcesses = new HashMap<>();
    private final Set<String> catalogRegions = new HashSet<>();

    public SupplierChatResponseValidator(JsonNode fixture) {
        this(fixture, false);
    }

    public SupplierChatResponseValidator(JsonNode fixture, boolean live) {
        this.live = live;
        object(fixture, "schema_version", "mode", "notice", "supported_queries", "suppliers");
        version(fixture);
        text(fixture.get("notice"), 4000);
        supportedQueries = strings(fixture.get("supported_queries"), 4000);
        require(supportedQueries.size() == 3);
        array(fixture.get("suppliers"));
        for (JsonNode supplier : fixture.get("suppliers")) {
            supplierShape(supplier, STUB_SOURCES);
            String id = supplier.get("supplier_id").stringValue();
            require(suppliers.put(id, supplier.deepCopy()) == null);
            catalogRegions.add(supplier.get("region").stringValue());
            for (JsonNode capability : supplier.get("capabilities")) {
                catalogProcesses.computeIfAbsent(capability.get("process").stringValue(), ignored -> new HashSet<>())
                        .add(capability.get("material").stringValue());
            }
        }
    }

    public void validateCatalog(JsonNode body) {
        JsonNode data = data(body);
        object(data, "processes", "regions");
        Set<String> regions = strings(data.get("regions"), 100);
        array(data.get("processes"));
        Map<String, Set<String>> processes = new HashMap<>();
        for (JsonNode entry : data.get("processes")) {
            object(entry, "process", "materials");
            String process = text(entry.get("process"), 100);
            require(processes.put(process, strings(entry.get("materials"), 100)) == null);
        }
        if (live) return;
        require(regions.equals(catalogRegions));
        require(processes.equals(catalogProcesses));
    }

    public void validateSearch(JsonNode body, JsonNode normalizedFilters) {
        JsonNode data = data(body);
        object(data, "items", "returned_count", "limit");
        array(data.get("items"));
        integer(data.get("returned_count"), 0, 100);
        integer(data.get("limit"), 1, 100);
        require(normalizedFilters != null && normalizedFilters.isObject());
        String process = text(normalizedFilters.get("process"), 100);
        String material = optionalText(normalizedFilters.get("material"));
        String region = optionalText(normalizedFilters.get("region"));
        boolean iatf = false;
        if (normalizedFilters.has("iatf_required")) {
            require(normalizedFilters.get("iatf_required").isBoolean());
            iatf = normalizedFilters.get("iatf_required").booleanValue();
        }
        boolean iso = false;
        if (normalizedFilters.has("iso14001_required")) {
            require(normalizedFilters.get("iso14001_required").isBoolean());
            iso = normalizedFilters.get("iso14001_required").booleanValue();
        }
        int minCapacity = 0;
        if (normalizedFilters.has("min_monthly_capacity")) {
            integer(normalizedFilters.get("min_monthly_capacity"), 1, 10_000_000);
            minCapacity = normalizedFilters.get("min_monthly_capacity").intValue();
        }
        int limit = 20;
        if (normalizedFilters.has("limit")) {
            integer(normalizedFilters.get("limit"), 1, 100);
            limit = normalizedFilters.get("limit").intValue();
        }
        require(data.get("limit").intValue() == limit);
        if (live) {
            liveSearch(data.get("items"), process, material, region, iatf, iso, minCapacity, limit);
            require(data.get("returned_count").intValue() == data.get("items").size());
            return;
        }
        List<JsonNode> expected = new ArrayList<>();
        for (JsonNode supplier : suppliers.values()) {
            if ((region != null && !region.equals(supplier.get("region").stringValue()))
                    || (iatf && !supplier.get("iatf16949").booleanValue())
                    || (iso && !supplier.get("iso14001").booleanValue())) continue;
            ArrayNode matched = JsonNodeFactory.instance.arrayNode();
            for (JsonNode capability : supplier.get("capabilities")) {
                if (process.equals(capability.get("process").stringValue())
                        && (material == null || material.equals(capability.get("material").stringValue()))
                        && capability.get("monthly_capacity").intValue() >= minCapacity) {
                    matched.add(capability);
                }
            }
            if (!matched.isEmpty()) {
                ObjectNode summary = JsonNodeFactory.instance.objectNode();
                for (String field : List.of("supplier_id", "name", "region", "iatf16949", "iso14001", "provenance")) {
                    summary.set(field, supplier.get(field));
                }
                summary.set("matched_capabilities", matched);
                expected.add(summary);
                if (expected.size() == limit) break;
            }
        }
        JsonNode items = data.get("items");
        require(items.size() == expected.size() && data.get("returned_count").intValue() == items.size());
        for (int index = 0; index < items.size(); index++) {
            JsonNode item = items.get(index);
            object(item, "supplier_id", "name", "region", "iatf16949", "iso14001", "matched_capabilities",
                    "provenance");
            capabilities(item.get("matched_capabilities"));
            require(sameJson(item, expected.get(index)));
        }
    }

    public void validateDetail(JsonNode body, String supplierId) {
        JsonNode data = data(body);
        if (live) {
            supplierShape(data, LIVE_SOURCES);
            literal(data.get("supplier_id"), supplierId);
        } else {
            detail(data, supplierId);
        }
    }

    private static void liveSearch(JsonNode items, String process, String material, String region, boolean iatf,
                                   boolean iso, int minCapacity, int limit) {
        require(items.size() <= limit);
        String previous = null;
        for (JsonNode item : items) {
            object(item, "supplier_id", "name", "region", "iatf16949", "iso14001", "matched_capabilities",
                    "provenance");
            String id = text(item.get("supplier_id"), 100);
            require(previous == null || previous.compareTo(id) < 0);
            previous = id;
            text(item.get("name"), 100);
            require(region == null || region.equals(text(item.get("region"), 100)));
            require(item.get("iatf16949").isBoolean() && (!iatf || item.get("iatf16949").booleanValue()));
            require(item.get("iso14001").isBoolean() && (!iso || item.get("iso14001").booleanValue()));
            provenance(item.get("provenance"), LIVE_SOURCES);
            capabilities(item.get("matched_capabilities"));
            for (JsonNode capability : item.get("matched_capabilities")) {
                require(process.equals(capability.get("process").stringValue())
                        && (material == null || material.equals(capability.get("material").stringValue()))
                        && capability.get("monthly_capacity").intValue() >= minCapacity);
            }
        }
    }

    public void validateChat(JsonNode body, UUID requestId, UUID conversationId, JsonNode context, String query) {
        object(body, "schema_version", "mode", "request_id", "conversation_id", "response_id", "execution_status",
                "generation_status", "resolved_context", "presentation", "citations", "evidence", "clarification");
        version(body);
        require(requestId != null && conversationId != null);
        require(uuid(body.get("request_id")).equals(requestId.toString()));
        require(uuid(body.get("conversation_id")).equals(conversationId.toString()));
        uuid(body.get("response_id"));
        literal(body.get("generation_status"), "NOT_IMPLEMENTED");
        String selectedId = context(context);
        context(body.get("resolved_context"));
        require(context.equals(body.get("resolved_context")));
        require(selectedId == null || suppliers.containsKey(selectedId));
        require(query != null);
        boolean supported = supportedQueries.contains(query.strip()) || OVERVIEW_ALIAS.equals(query.strip());
        boolean completed = selectedId != null && supported;
        literal(body.get("execution_status"), completed ? "COMPLETED" : "NEEDS_INPUT");
        JsonNode presentation = body.get("presentation");
        object(presentation, "status", "renderer", "text", "cards");
        literal(presentation.get("status"), completed ? "READY" : "NEEDS_INPUT");
        literal(presentation.get("renderer"), "template");
        String presentationText = text(presentation.get("text"), 4000);
        array(presentation.get("cards"));
        array(body.get("evidence"));
        array(body.get("citations"));
        require(body.get("citations").isEmpty());
        if (completed) {
            require(body.get("clarification").isNull());
            require(presentation.get("cards").size() == 1 && body.get("evidence").size() == 1);
            JsonNode card = presentation.get("cards").get(0);
            object(card, "type", "supplier", "evidence_ids");
            literal(card.get("type"), "supplier_detail");
            detail(card.get("supplier"), selectedId);
            JsonNode evidence = body.get("evidence").get(0);
            object(evidence, "evidence_id", "server", "tool", "supplier_id", "source", "executed");
            String evidenceId = text(evidence.get("evidence_id"), 100);
            literal(evidence.get("server"), "business-mcp");
            literal(evidence.get("tool"), "get_supplier");
            literal(evidence.get("supplier_id"), selectedId);
            literal(evidence.get("source"), "stub");
            require(evidence.get("executed").isBoolean() && !evidence.get("executed").booleanValue());
            require(strings(card.get("evidence_ids"), 100).equals(Set.of(evidenceId)));
        } else {
            require(presentation.get("cards").isEmpty() && body.get("evidence").isEmpty());
            JsonNode clarification = body.get("clarification");
            object(clarification, "missing_fields", "question", "options");
            require(strings(clarification.get("missing_fields"), 100)
                    .equals(Set.of(selectedId == null ? "context.selected_supplier_ids" : "query")));
            require(text(clarification.get("question"), 4000).equals(presentationText));
            require(strings(clarification.get("options"), 4000).equals(supportedQueries));
        }
    }

    private void detail(JsonNode actual, String supplierId) {
        supplierShape(actual, STUB_SOURCES);
        JsonNode expected = suppliers.get(supplierId);
        require(expected != null && sameJson(actual, expected));
    }

    private static void supplierShape(JsonNode supplier, Set<String> sources) {
        object(supplier, "supplier_id", "name", "region", "iatf16949", "iso14001", "capabilities", "quality", "financials",
                "recent_quotes", "existing_contract", "provenance");
        for (String field : List.of("supplier_id", "name", "region")) text(supplier.get(field), 100);
        require(supplier.get("iatf16949").isBoolean() && supplier.get("iso14001").isBoolean());
        capabilities(supplier.get("capabilities"));
        JsonNode quality = supplier.get("quality");
        object(quality, "defect_ppm", "on_time_delivery_rate");
        nullableNumber(quality.get("defect_ppm"));
        nullableNumber(quality.get("on_time_delivery_rate"));
        array(supplier.get("financials"));
        for (JsonNode financial : supplier.get("financials")) {
            object(financial, "year", "revenue_krw", "operating_profit_krw", "debt_ratio", "source");
            integer(financial.get("year"), 1900, 9999);
            nullableNumber(financial.get("revenue_krw"));
            nullableNumber(financial.get("operating_profit_krw"));
            nullableNumber(financial.get("debt_ratio"));
            require(sources.contains(text(financial.get("source"), 100)));
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
        provenance(supplier.get("provenance"), sources);
    }

    private static void provenance(JsonNode provenance, Set<String> sources) {
        object(provenance, "profile", "financials", "operations");
        for (String field : List.of("profile", "financials", "operations")) {
            require(sources.contains(text(provenance.get(field), 100)));
        }
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

    private static String context(JsonNode node) {
        object(node, "selected_supplier_ids");
        Set<String> selected = strings(node.get("selected_supplier_ids"), 100);
        require(selected.size() <= 1);
        return selected.isEmpty() ? null : selected.iterator().next();
    }

    private JsonNode data(JsonNode body) {
        object(body, "schema_version", "mode", "data");
        version(body, live ? "live" : "stub");
        return body.get("data");
    }

    private static void version(JsonNode body) {
        version(body, "stub");
    }

    private static void version(JsonNode body, String mode) {
        literal(body.get("schema_version"), "0.2");
        literal(body.get("mode"), mode);
    }

    private static String uuid(JsonNode value) {
        String id = text(value, 36);
        require(id.matches("[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"));
        return id;
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

    private static Set<String> strings(JsonNode node, int maximum) {
        array(node);
        Set<String> values = new HashSet<>();
        for (JsonNode item : node) require(values.add(text(item, maximum)));
        return values;
    }

    private static void literal(JsonNode node, String expected) { require(text(node, 4000).equals(expected)); }

    private static void integer(JsonNode node, long minimum, long maximum) {
        require(node != null && node.isIntegralNumber() && node.canConvertToLong());
        require(node.longValue() >= minimum && node.longValue() <= maximum);
    }

    private static void nullableNumber(JsonNode node) {
        require(node != null && (node.isNull() || (node.isNumber() && Double.isFinite(node.doubleValue()))));
    }

    private static boolean sameJson(JsonNode actual, JsonNode expected) {
        if (actual == null || expected == null) return actual == expected;
        if (actual.isNumber() && expected.isNumber()) return Double.isFinite(actual.doubleValue())
                && Double.isFinite(expected.doubleValue()) && actual.decimalValue().compareTo(expected.decimalValue()) == 0;
        if (actual.isObject() && expected.isObject()) {
            if (actual.size() != expected.size()) return false;
            for (String field : expected.propertyNames()) if (!sameJson(actual.get(field), expected.get(field))) return false;
            return true;
        }
        if (actual.isArray() && expected.isArray()) {
            if (actual.size() != expected.size()) return false;
            for (int index = 0; index < expected.size(); index++) if (!sameJson(actual.get(index), expected.get(index))) return false;
            return true;
        }
        return actual.equals(expected);
    }

    private static void require(boolean condition) {
        if (!condition) throw new IllegalArgumentException("Invalid supplier chat response");
    }
}
