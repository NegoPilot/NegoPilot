package com.negopilot.backend.negotiation;

import com.negopilot.backend.common.api.ApiException;
import java.time.LocalDate;
import java.time.format.DateTimeParseException;
import java.util.HashSet;
import java.util.Set;
import java.util.UUID;
import org.springframework.stereotype.Component;
import tools.jackson.core.StreamReadFeature;
import tools.jackson.databind.DeserializationFeature;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

@Component
final class NegotiationRequestValidator {
    private static final Set<String> REVIEW_FIELDS = Set.of("as_of", "unit_price", "payment_days", "price_linkage",
            "raw_material_ratio", "energy_ratio", "contract_days", "linkage_waived_by_agreement", "supplier_size",
            "buyer_size", "transaction", "base_revision");
    private static final Set<String> SIZE_NUMBERS = Set.of("average_revenue_eok", "assets_eok", "related_aggregate_revenue_eok");
    private static final Set<String> SIZE_NULLABLE_FLAGS = Set.of("independence_met", "has_related_enterprises",
            "seat_manufacturing", "deemed_sme");
    private static final Set<String> SIZE_FLAGS = Set.of("related_calculation_verified", "transition_reviewed");
    private static final Set<String> TRANSACTION_DATES = Set.of("contracted_on", "renewed_on", "receipt_date",
            "completion_date", "monthly_agreed_date", "agreed_due_date", "upstream_received_on");
    private static final Set<String> TRANSACTION_NUMBERS = Set.of("actual_contract_amount", "committed_quantity", "previous_unit_price");
    private static final Set<String> TRANSACTION_FLAGS = Set.of("party_requirements_confirmed", "upstream_payment_applicable",
            "payment_exception_claimed");
    private static final Set<String> TRANSACTION_TEXT = Set.of("payment_exception_reason", "reduction_reason", "waiver_reason");
    private static final Set<String> TRANSACTION_LISTS = Set.of("payment_exception_evidence", "reduction_evidence",
            "waiver_evidence", "evidence_references");
    private static final Set<String> LINKAGE_TEXT = Set.of("indicator", "formula", "frequency", "reference_period", "reflection_date");
    private final JsonMapper mapper;

    NegotiationRequestValidator(JsonMapper mapper) {
        this.mapper = mapper.rebuild().enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION)
                .enable(DeserializationFeature.FAIL_ON_TRAILING_TOKENS).build();
    }

    ObjectNode parse(String raw, UUID id) {
        if (raw == null || raw.length() > 64000 || raw.indexOf('\0') >= 0) throw ApiException.validation(id);
        try {
            JsonNode body = mapper.readTree(raw);
            if (body instanceof ObjectNode object) {
                rejectNullCharacters(object, id);
                return object;
            }
        } catch (RuntimeException ignored) { }
        throw ApiException.validation(id);
    }

    ObjectNode create(ObjectNode body, UUID id) {
        fields(body, Set.of("supplier_id", "rfq_id", "title"), id);
        pattern(body.get("supplier_id"), "SUP-[0-9]{4}", id);
        pattern(body.get("rfq_id"), "RFQ-[0-9]{4}-[0-9]{3}", id);
        if (body.has("title")) text(body.get("title"), 1, 100, false, id);
        return body;
    }

    ObjectNode workspacePatch(ObjectNode body, UUID id) {
        fields(body, Set.of("row_version", "title", "status"), id);
        integer(body.get("row_version"), 0, Long.MAX_VALUE - 1, false, id);
        if (!body.has("title") && !body.has("status")) throw ApiException.validation(id);
        if (body.has("title")) text(body.get("title"), 1, 100, false, id);
        if (body.has("status")) literal(body.get("status"), Set.of("OPEN", "ARCHIVED"), false, id);
        return body;
    }

    ObjectNode review(ObjectNode body, UUID id) {
        fields(body, REVIEW_FIELDS, id);
        date(body.get("as_of"), false, id);
        for (String key : Set.of("unit_price", "payment_days", "contract_days"))
            integer(body.get(key), 1, Integer.MAX_VALUE, false, id);
        bool(body.get("price_linkage"), false, id);
        for (String key : Set.of("raw_material_ratio", "energy_ratio"))
            if (body.has(key)) number(body.get(key), 0, 1, true, id);
        if (body.has("linkage_waived_by_agreement")) bool(body.get("linkage_waived_by_agreement"), false, id);
        if (body.has("base_revision")) integer(body.get("base_revision"), 1, Integer.MAX_VALUE, true, id);
        company(body.get("supplier_size"), id);
        company(body.get("buyer_size"), id);
        if (body.has("transaction") && !body.get("transaction").isNull()) transaction(body.get("transaction"), id);
        return body;
    }

    ObjectNode proposal(ObjectNode body, UUID id) {
        fields(body, Set.of("review_revision", "offer"), id);
        integer(body.get("review_revision"), 1, Integer.MAX_VALUE, false, id);
        JsonNode offer = body.get("offer");
        fields(offer, Set.of("intent", "target_price", "max_price", "notes"), id);
        if (offer.has("intent")) literal(offer.get("intent"), Set.of("target", "market", "payment"), true, id);
        for (String key : Set.of("target_price", "max_price"))
            if (offer.has(key)) integer(offer.get(key), 1, Integer.MAX_VALUE, true, id);
        if (offer.has("notes")) text(offer.get("notes"), 0, 500, true, id);
        if (offer.hasNonNull("target_price") && offer.hasNonNull("max_price")
                && offer.get("max_price").longValue() < offer.get("target_price").longValue()) throw ApiException.validation(id);
        return body;
    }

    ObjectNode proposalPatchEnvelope(ObjectNode body, UUID id) {
        fields(body, Set.of("base_revision", "changes", "change_note"), id);
        integer(body.get("base_revision"), 1, Integer.MAX_VALUE, false, id);
        if (!body.path("changes").isObject()) throw ApiException.validation(id);
        if (body.has("change_note")) text(body.get("change_note"), 0, 500, false, id);
        return body;
    }

    ObjectNode export(ObjectNode body, UUID id) {
        fields(body, Set.of("revision", "document_type", "formats", "final"), id);
        integer(body.get("revision"), 1, Integer.MAX_VALUE, false, id);
        literal(body.get("document_type"), Set.of("internal", "external"), false, id);
        if (body.get("document_type").stringValue().equals("internal"))
            throw ApiException.of(id, 422, "UNSUPPORTED_EXPORT_FORMAT", "내부 문서 내보내기는 지원하지 않습니다.", false);
        JsonNode formats = body.get("formats");
        if (formats == null || !formats.isArray() || formats.isEmpty() || formats.size() > 2) throw ApiException.validation(id);
        Set<String> seen = new HashSet<>();
        for (JsonNode format : formats) {
            literal(format, Set.of("pdf", "docx"), false, id);
            if (!seen.add(format.stringValue())) throw ApiException.validation(id);
        }
        if (body.has("final")) bool(body.get("final"), false, id);
        return body;
    }

    private void company(JsonNode node, UUID id) {
        Set<String> allowed = new HashSet<>(Set.of("ksic_code", "evidence_reference"));
        allowed.addAll(SIZE_NUMBERS); allowed.addAll(SIZE_NULLABLE_FLAGS); allowed.addAll(SIZE_FLAGS);
        fields(node, allowed, id);
        if (node.has("ksic_code")) text(node.get("ksic_code"), 0, 20, true, id);
        if (node.has("evidence_reference")) text(node.get("evidence_reference"), 0, 500, true, id);
        for (String key : SIZE_NUMBERS) if (node.has(key)) number(node.get(key), 0, Double.MAX_VALUE, true, id);
        for (String key : SIZE_NULLABLE_FLAGS) if (node.has(key)) bool(node.get(key), true, id);
        for (String key : SIZE_FLAGS) if (node.has(key)) bool(node.get(key), false, id);
    }

    private void transaction(JsonNode node, UUID id) {
        Set<String> allowed = new HashSet<>(Set.of("transaction_type", "price_scope", "payment_basis", "linkage_details"));
        allowed.addAll(TRANSACTION_DATES); allowed.addAll(TRANSACTION_NUMBERS); allowed.addAll(TRANSACTION_FLAGS);
        allowed.addAll(TRANSACTION_TEXT); allowed.addAll(TRANSACTION_LISTS);
        fields(node, allowed, id);
        if (node.has("transaction_type")) literal(node.get("transaction_type"), Set.of("manufacturing", "purchase", "unknown"), false, id);
        if (node.has("price_scope")) literal(node.get("price_scope"), Set.of("new_order", "existing_order", "unknown"), false, id);
        if (node.has("payment_basis")) literal(node.get("payment_basis"), Set.of("receipt", "completion", "monthly_agreed", "unknown"), false, id);
        for (String key : TRANSACTION_DATES) if (node.has(key)) date(node.get(key), true, id);
        for (String key : TRANSACTION_NUMBERS) if (node.has(key)) integer(node.get(key), 1, Long.MAX_VALUE, true, id);
        for (String key : TRANSACTION_FLAGS) if (node.has(key)) bool(node.get(key), true, id);
        for (String key : TRANSACTION_TEXT) if (node.has(key)) text(node.get(key), 0, 64000, true, id);
        for (String key : TRANSACTION_LISTS) if (node.has(key)) strings(node.get(key), id);
        if (node.has("linkage_details")) {
            JsonNode details = node.get("linkage_details");
            if (!details.isArray()) throw ApiException.validation(id);
            Set<String> linkageFields = new HashSet<>(Set.of("category", "name", "cost_ratio", "trigger_ratio", "evidence_references"));
            linkageFields.addAll(LINKAGE_TEXT);
            for (JsonNode detail : details) {
                fields(detail, linkageFields, id);
                literal(detail.get("category"), Set.of("material", "energy"), false, id);
                text(detail.get("name"), 1, 64000, false, id);
                if (detail.has("cost_ratio")) number(detail.get("cost_ratio"), 0, 1, true, id);
                if (detail.has("trigger_ratio")) number(detail.get("trigger_ratio"), 0, 0.1, true, id);
                for (String key : LINKAGE_TEXT) if (detail.has(key)) text(detail.get(key), 0, 64000, true, id);
                if (detail.has("evidence_references")) strings(detail.get("evidence_references"), id);
            }
        }
    }

    ObjectNode mailPreview(ObjectNode body, UUID id) {
        fields(body, Set.of("bundle_id", "contact_id", "message", "acknowledgements"), id);
        pattern(body.get("bundle_id"), UUID_PATTERN, id);
        if (body.has("contact_id")) integer(body.get("contact_id"), 1, Integer.MAX_VALUE, true, id);
        if (body.has("message")) text(body.get("message"), 0, 2000, true, id);
        JsonNode acknowledgements = body.get("acknowledgements");
        if (acknowledgements != null) {
            fields(acknowledgements, Set.of("check_needed", "unresolved_issue_ids"), id);
            if (acknowledgements.has("check_needed")) bool(acknowledgements.get("check_needed"), false, id);
            JsonNode issues = acknowledgements.get("unresolved_issue_ids");
            if (issues != null) {
                if (!issues.isArray() || issues.size() > 50) throw ApiException.validation(id);
                for (JsonNode issue : issues) text(issue, 1, 100, false, id);
            }
        }
        return body;
    }

    ObjectNode mailSend(ObjectNode body, UUID id) {
        fields(body, Set.of("content_hash"), id);
        pattern(body.get("content_hash"), "sha256:[0-9a-f]{64}", id);
        return body;
    }

    private static final String UUID_PATTERN = "(?i)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";

    private static void fields(JsonNode node, Set<String> allowed, UUID id) {
        if (node == null || !node.isObject() || !allowed.containsAll(node.propertyNames())) throw ApiException.validation(id);
    }

    private static void pattern(JsonNode node, String pattern, UUID id) {
        if (node == null || !node.isString() || !node.stringValue().matches(pattern)) throw ApiException.validation(id);
    }

    private static void integer(JsonNode node, long minimum, long maximum, boolean nullable, UUID id) {
        if (nullable && node != null && node.isNull()) return;
        if (node == null || !node.isIntegralNumber() || !node.canConvertToLong()
                || node.longValue() < minimum || node.longValue() > maximum) throw ApiException.validation(id);
    }

    private static void number(JsonNode node, double minimum, double maximum, boolean nullable, UUID id) {
        if (nullable && node != null && node.isNull()) return;
        if (node == null || !node.isNumber() || !Double.isFinite(node.doubleValue())
                || node.doubleValue() < minimum || node.doubleValue() > maximum) throw ApiException.validation(id);
    }

    private static void bool(JsonNode node, boolean nullable, UUID id) {
        if (nullable && node != null && node.isNull()) return;
        if (node == null || !node.isBoolean()) throw ApiException.validation(id);
    }

    private static void literal(JsonNode node, Set<String> values, boolean nullable, UUID id) {
        if (nullable && node != null && node.isNull()) return;
        if (node == null || !node.isString() || !values.contains(node.stringValue())) throw ApiException.validation(id);
    }

    private static void text(JsonNode node, int minimum, int maximum, boolean nullable, UUID id) {
        if (nullable && node != null && node.isNull()) return;
        if (node == null || !node.isString()) throw ApiException.validation(id);
        String value = node.stringValue();
        int length = value.codePointCount(0, value.length());
        if (length < minimum || length > maximum || value.indexOf('\0') >= 0 || minimum > 0 && value.isBlank())
            throw ApiException.validation(id);
    }

    private static void strings(JsonNode node, UUID id) {
        if (node == null || !node.isArray()) throw ApiException.validation(id);
        for (JsonNode value : node) text(value, 0, 64000, false, id);
    }

    private static void date(JsonNode node, boolean nullable, UUID id) {
        if (nullable && node != null && node.isNull()) return;
        pattern(node, "[0-9]{4}-[0-9]{2}-[0-9]{2}", id);
        try { LocalDate.parse(node.stringValue()); }
        catch (DateTimeParseException ignored) { throw ApiException.validation(id); }
    }

    private static void rejectNullCharacters(JsonNode node, UUID id) {
        if (node.isString() && node.stringValue().indexOf('\0') >= 0) throw ApiException.validation(id);
        if (node.isObject()) for (String key : node.propertyNames()) if (key.indexOf('\0') >= 0) throw ApiException.validation(id);
        if (node.isContainer()) for (JsonNode child : node) rejectNullCharacters(child, id);
    }
}
