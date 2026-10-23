package com.negopilot.backend.admin.supplier;

import com.negopilot.backend.common.api.ApiException;

import java.time.LocalDate;
import java.time.Year;
import java.time.format.DateTimeParseException;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

@Component
final class AdminSupplierValidator {
    static final Set<String> PROFILE = Set.of("name", "region", "employees", "revenue_eok", "founded_year",
            "biz_no_masked", "stock_code", "induty_code");
    // Origin fields are accepted only on creation. PATCH never changes them.
    static final Set<String> ORIGIN = Set.of("source", "corp_code");
    static final Set<String> PROCESSES = Set.of("다이캐스팅", "프레스", "사출", "단조", "CNC가공");
    private final JsonMapper mapper;
    AdminSupplierValidator(JsonMapper mapper) { this.mapper = mapper; }

    ObjectNode parse(String raw, UUID id) {
        try {
            JsonNode body = raw == null ? null : mapper.readTree(raw);
            if (body instanceof ObjectNode object) return object;
        } catch (RuntimeException ignored) { }
        throw ApiException.validation(id);
    }

    String supplierId(String value, UUID id) {
        if (value == null || !value.matches("[A-Za-z0-9_-]{1,100}")) throw ApiException.validation(id);
        return value;
    }

    ObjectNode create(ObjectNode body, UUID id) {
        fields(body, Set.of("profile", "certifications", "capabilities", "quality"), id);
        profile(body.get("profile"), true, id);
        certifications(body.get("certifications"), true, id);
        capabilities(body.get("capabilities"), id);
        if (body.has("quality") && !body.get("quality").isNull()) quality(body.get("quality"), id);
        return body;
    }

    ObjectNode patch(ObjectNode body, UUID id) {
        fields(body, Set.of("version", "profile", "certifications"), id);
        version(body, id);
        if (!body.has("profile") && !body.has("certifications")) throw ApiException.validation(id);
        if (body.has("profile")) profile(body.get("profile"), false, id);
        if (body.has("certifications")) certifications(body.get("certifications"), false, id);
        return body;
    }

    ObjectNode capabilitiesRequest(ObjectNode body, UUID id) {
        fields(body, Set.of("version", "capabilities"), id); version(body, id);
        capabilities(body.get("capabilities"), id); return body;
    }

    ObjectNode quotesRequest(ObjectNode body, UUID id) {
        fields(body, Set.of("version", "quotes"), id); version(body, id);
        quotes(body.get("quotes"), id); return body;
    }

    ObjectNode qualityRequest(ObjectNode body, UUID id) {
        fields(body, Set.of("version", "quality"), id); version(body, id);
        quality(body.get("quality"), id); return body;
    }

    ObjectNode statusRequest(ObjectNode body, UUID id) {
        fields(body, Set.of("version", "status"), id); version(body, id);
        if (!Set.of("ACTIVE", "INACTIVE").contains(text(body.get("status"), 8, id))) throw ApiException.validation(id);
        return body;
    }

    long version(ObjectNode body, UUID id) { return integer(body.get("version"), 0, Long.MAX_VALUE - 1, id); }

    record Search(int page, int size, String q, String source, String status) { }
    Search search(Map<String, String[]> params, UUID id) {
        if (!Set.of("page", "size", "q", "source", "status").containsAll(params.keySet())) throw ApiException.validation(id);
        Map<String, String> values = new LinkedHashMap<>();
        params.forEach((key, value) -> {
            if (value == null || value.length != 1) throw ApiException.validation(id);
            values.put(key, value[0].strip());
        });
        int page = queryInteger(values.getOrDefault("page", "0"), 0, 1_000_000, id);
        int size = queryInteger(values.getOrDefault("size", "20"), 1, 100, id);
        String q = values.get("q");
        if (q != null && (q.isBlank() || q.length() > 100)) throw ApiException.validation(id);
        String source = values.get("source"), status = values.get("status");
        if (source != null && !Set.of("DART", "MOCK", "MANUAL").contains(source)) throw ApiException.validation(id);
        if (status != null && !Set.of("ACTIVE", "INACTIVE").contains(status)) throw ApiException.validation(id);
        return new Search(page, size, q, source, status);
    }

    private int queryInteger(String raw, int min, int max, UUID id) {
        try {
            if (!raw.matches("[0-9]{1,10}")) throw new IllegalArgumentException();
            int value = Integer.parseInt(raw);
            if (value >= min && value <= max) return value;
        } catch (IllegalArgumentException ignored) { }
        throw ApiException.validation(id);
    }

    private void profile(JsonNode node, boolean full, UUID id) {
        Set<String> allowed = new HashSet<>(PROFILE);
        if (full) allowed.addAll(ORIGIN);
        fields(node, allowed, id);
        if (node.isEmpty()) throw ApiException.validation(id);
        if (full) origin((ObjectNode) node, id);
        if (full && !node.propertyNames().containsAll(Set.of("name", "region", "founded_year", "biz_no_masked")))
            throw ApiException.validation(id);
        for (String key : List.of("name", "region", "biz_no_masked")) {
            if (node.has(key)) ((ObjectNode) node).put(key, text(node.get(key), 100, id));
        }
        if (node.has("biz_no_masked") && !node.get("biz_no_masked").stringValue().matches("[0-9]{3}-[0-9]{2}-\\*{5}"))
            throw ApiException.validation(id);
        if (node.has("region") && !Set.of("서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종", "경기",
                "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주").contains(node.get("region").stringValue()))
            throw ApiException.validation(id);
        for (String key : List.of("employees", "revenue_eok"))
            if (node.has(key) && !node.get(key).isNull()) integer(node.get(key), key.equals("employees") ? 1 : 0, Integer.MAX_VALUE, id);
        if (node.has("founded_year")) integer(node.get("founded_year"), 1900, Year.now().getValue(), id);
        for (String key : List.of("stock_code", "induty_code")) {
            if (node.has(key) && !node.get(key).isNull()) {
                String value = text(node.get(key), 6, id);
                if (!value.matches(key.equals("stock_code") ? "[0-9]{6}" : "[0-9]{3,5}")) throw ApiException.validation(id);
                ((ObjectNode) node).put(key, value);
            }
        }
    }

    /** source defaults to MANUAL. Only DART carries a corp_code, and DART requires one. */
    private void origin(ObjectNode node, UUID id) {
        String source = node.hasNonNull("source") ? text(node.get("source"), 6, id) : "MANUAL";
        if (!Set.of("DART", "MOCK", "MANUAL").contains(source)) throw ApiException.validation(id);
        node.put("source", source);
        if (source.equals("DART")) {
            String corpCode = text(node.get("corp_code"), 8, id);
            if (!corpCode.matches("[0-9]{8}")) throw ApiException.validation(id);
            node.put("corp_code", corpCode);
        } else if (node.hasNonNull("corp_code")) {
            throw ApiException.validation(id);
        } else {
            node.putNull("corp_code");
        }
    }

    private void certifications(JsonNode node, boolean full, UUID id) {
        fields(node, Set.of("iatf16949", "iso14001"), id);
        if (node.isEmpty() || (full && node.size() != 2)) throw ApiException.validation(id);
        for (JsonNode value : node) if (!value.isBoolean()) throw ApiException.validation(id);
    }

    private void capabilities(JsonNode node, UUID id) {
        if (node == null || !node.isArray() || node.isEmpty() || node.size() > 100) throw ApiException.validation(id);
        Set<String> seen = new HashSet<>();
        for (JsonNode row : node) {
            fields(row, Set.of("process", "material", "monthly_capacity"), id);
            if (row.size() != 3) throw ApiException.validation(id);
            String process = text(row.get("process"), 100, id), material = text(row.get("material"), 100, id);
            if (!PROCESSES.contains(process) || !seen.add(process + "\u0000" + material)) throw ApiException.validation(id);
            ((ObjectNode) row).put("process", process).put("material", material);
            integer(row.get("monthly_capacity"), 1, 10_000_000, id);
        }
    }

    private void quotes(JsonNode node, UUID id) {
        if (node == null || !node.isArray() || node.isEmpty() || node.size() > 100) throw ApiException.validation(id);
        for (JsonNode row : node) {
            fields(row, Set.of("process", "material", "unit_price", "quoted_at"), id);
            if (row.size() != 4) throw ApiException.validation(id);
            String process = text(row.get("process"), 100, id), material = text(row.get("material"), 100, id);
            if (!PROCESSES.contains(process)) throw ApiException.validation(id);
            ((ObjectNode) row).put("process", process).put("material", material);
            integer(row.get("unit_price"), 1, Integer.MAX_VALUE, id);
            String quotedAt = text(row.get("quoted_at"), 10, id);
            try {
                if (!quotedAt.matches("[0-9]{4}-[0-9]{2}-[0-9]{2}") || LocalDate.parse(quotedAt).isAfter(LocalDate.now()))
                    throw ApiException.validation(id);
            } catch (DateTimeParseException ignored) { throw ApiException.validation(id); }
        }
    }

    private void quality(JsonNode node, UUID id) {
        fields(node, Set.of("defect_ppm", "on_time_rate", "audit_grade", "years_dealing"), id);
        if (node.size() != 4) throw ApiException.validation(id);
        integer(node.get("defect_ppm"), 0, Integer.MAX_VALUE, id);
        integer(node.get("years_dealing"), 0, 1000, id);
        if (!Set.of("A", "B", "C", "D").contains(text(node.get("audit_grade"), 1, id))) throw ApiException.validation(id);
        JsonNode rate = node.get("on_time_rate");
        if (rate == null || !rate.isNumber() || !Double.isFinite(rate.doubleValue())
                || rate.doubleValue() < 0 || rate.doubleValue() > 1 || rate.decimalValue().stripTrailingZeros().scale() > 3)
            throw ApiException.validation(id);
    }

    private void fields(JsonNode node, Set<String> allowed, UUID id) {
        if (node == null || !node.isObject() || !allowed.containsAll(node.propertyNames())) throw ApiException.validation(id);
    }
    private String text(JsonNode node, int max, UUID id) {
        if (node == null || !node.isString()) throw ApiException.validation(id);
        String value = node.stringValue().strip();
        if (value.isEmpty() || value.length() > max || value.codePoints().anyMatch(Character::isISOControl))
            throw ApiException.validation(id);
        return value;
    }
    private long integer(JsonNode node, long min, long max, UUID id) {
        if (node == null || !node.isIntegralNumber() || !node.canConvertToLong()) throw ApiException.validation(id);
        long value = node.longValue();
        if (value < min || value > max) throw ApiException.validation(id);
        return value;
    }
}
