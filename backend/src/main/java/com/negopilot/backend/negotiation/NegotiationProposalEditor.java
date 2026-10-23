package com.negopilot.backend.negotiation;

import com.negopilot.backend.common.api.ApiException;
import java.time.LocalDate;
import java.time.format.DateTimeParseException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.JsonNodeFactory;
import tools.jackson.databind.node.ObjectNode;

public final class NegotiationProposalEditor {
    private static final JsonNodeFactory JSON = JsonNodeFactory.instance;
    private static final Set<String> FACTS = Set.of("supplier_size", "buyer_size", "raw_material_ratio", "energy_ratio",
            "as_of", "supplier", "rfq_id", "transaction");
    private static final Set<String> SYSTEM = Set.of("rationale", "warnings", "law_refs", "verdict_code", "revisions",
            "adjustments", "filled", "benchmarks", "considered", "contract_amount", "explanation", "review_linkage",
            "validation", "sender", "document_id", "version", "status", "content_hash", "target_price", "max_price", "intent",
            "opening", "verdict", "headline", "basis", "label", "annual_volume", "linkage_status", "input_needed",
            "no_offer_reasons", "source", "attempts", "first_try_passed", "problems");
    private static final Set<String> TERMS = Set.of("unit_price", "payment_days", "price_linkage", "contract_days",
            "linkage_waived_by_agreement");

    public record EditResult(ObjectNode revision, ArrayNode changeSet, Set<String> changedScopes,
                             ObjectNode impacts, boolean changed) {}

    public static EditResult edit(ObjectNode revision, ObjectNode changes) {
        return edit(UUID.randomUUID(), revision, changes);
    }

    public static EditResult edit(UUID requestId, ObjectNode revision, ObjectNode changes) {
        if (changes == null) throw ApiException.validation(requestId);
        List<String> facts = new ArrayList<>();
        List<String> systems = new ArrayList<>();
        scan(changes, "", facts, systems);
        if (!facts.isEmpty()) throw ApiException.of(requestId, 409, "REVIEW_RECHECK_REQUIRED",
                "거래 사실을 바꾸려면 검토를 다시 실행해야 합니다", false);
        if (!systems.isEmpty()) throw ApiException.of(requestId, 422, "FIELD_NOT_EDITABLE",
                "수정할 수 없는 필드입니다", false, Map.of("paths", systems));
        validate(requestId, changes);

        ObjectNode next = revision.deepCopy();
        ArrayNode changeSet = JSON.arrayNode();
        Set<String> scopes = new LinkedHashSet<>();
        for (String section : sortedKeys(changes)) {
            ObjectNode values = (ObjectNode) changes.get(section);
            if ("offer".equals(section)) {
                if (!next.path("internal_body").path("offer").isObject()) throw noOffer(requestId);
                ObjectNode offer = (ObjectNode) next.path("internal_body").path("offer");
                for (String key : sortedKeys(values)) {
                    JsonNode value = values.get(key);
                    if (TERMS.contains(key)) {
                        if (update(next, key, value, "/offer/" + key, changeSet)) scopes.add("TERMS");
                    } else if ("price_range".equals(key)) {
                        for (String pointName : sortedKeys(value)) {
                            ObjectNode point = pricePoint(offer, pointName);
                            if (point == null) throw ApiException.internal(requestId);
                            if (update(point, "unit_price", value.get(pointName), "/offer/price_range/" + pointName, changeSet)) {
                                scopes.add("PRICE_RANGE");
                            }
                        }
                    } else if ("notes".equals(key)) {
                        boolean changed = update(offer, key, value, "/offer/notes", changeSet);
                        if (next.path("external_body").isObject()) {
                            ObjectNode external = (ObjectNode) next.get("external_body");
                            if (!value.equals(external.path("delivery_notes"))) {
                                if (!changed) addChange(changeSet, "/offer/notes", external.path("delivery_notes"), value);
                                external.set("delivery_notes", value.deepCopy());
                                changed = true;
                            }
                        }
                        if (changed) {
                            scopes.add("INTERNAL_TEXT");
                            scopes.add("EXTERNAL_TEXT");
                        }
                    } else if (update(offer, key, value, "/offer/" + key, changeSet)) scopes.add("INTERNAL_TEXT");
                }
            } else if ("writing".equals(section)) {
                ObjectNode internal = (ObjectNode) next.get("internal_body");
                ObjectNode writing = internal.path("writing").isObject()
                        ? (ObjectNode) internal.get("writing") : JSON.objectNode();
                for (String key : sortedKeys(values)) {
                    if (update(writing, key, values.get(key), "/writing/" + key, changeSet)) scopes.add("INTERNAL_TEXT");
                }
                if (!values.isEmpty()) internal.set("writing", writing);
            } else {
                if (!next.path("external_body").isObject()) throw noOffer(requestId);
                ObjectNode external = (ObjectNode) next.get("external_body");
                for (String key : sortedKeys(values)) {
                    if ("linkage_terms".equals(key)) {
                        ObjectNode terms = external.path(key).isObject() ? (ObjectNode) external.get(key) : JSON.objectNode();
                        for (String term : sortedKeys(values.get(key))) {
                            if (update(terms, term, values.get(key).get(term), "/external/linkage_terms/" + pointer(term), changeSet)) {
                                scopes.add("EXTERNAL_TEXT");
                            }
                        }
                        if (!values.get(key).isEmpty()) external.set(key, terms);
                    } else if (update(external, key, values.get(key), "/external/" + key, changeSet)) scopes.add("EXTERNAL_TEXT");
                }
            }
        }
        if (next.path("internal_body").path("offer").isObject()) {
            ObjectNode offer = (ObjectNode) next.path("internal_body").path("offer");
            ObjectNode opening = pricePoint(offer, "opening");
            if (!revision.path("unit_price").equals(next.path("unit_price")) && opening != null) {
                opening.set("unit_price", next.path("unit_price").deepCopy());
            }
            checkPriceOrder(requestId, next, offer);
        }
        boolean changed = !changeSet.isEmpty();
        if (changed) {
            int base = revision.path("revision").asInt();
            next.put("revision", base + 1).put("base_revision", base).put("origin", "USER_EDIT");
            if (scopes.contains("TERMS")) next.put("terms_rev", revision.path("terms_rev").asInt() + 1);
            if (intersects(scopes, Set.of("TERMS", "PRICE_RANGE", "INTERNAL_TEXT"))) {
                next.put("internal_content_rev", revision.path("internal_content_rev").asInt() + 1);
            }
            if (revision.hasNonNull("external_content_rev") && intersects(scopes, Set.of("TERMS", "EXTERNAL_TEXT"))) {
                next.put("external_content_rev", revision.path("external_content_rev").asInt() + 1);
            }
            if (intersects(scopes, Set.of("TERMS", "PRICE_RANGE"))) {
                ((ObjectNode) next.get("internal_body")).put("derived_stale", true);
            }
            ArrayNode changedScopes = next.putArray("changed_scopes");
            scopes.forEach(changedScopes::add);
            next.set("change_set", changeSet.deepCopy());
        }
        return new EditResult(next, changeSet, Collections.unmodifiableSet(scopes), impacts(next, scopes), changed);
    }

    private static void validate(UUID id, ObjectNode changes) {
        for (String section : sortedKeys(changes)) {
            if (!Set.of("offer", "writing", "external").contains(section) || !changes.get(section).isObject()) invalid(id);
            for (String key : sortedKeys(changes.get(section))) {
                JsonNode value = changes.get(section).get(key);
                if ("offer".equals(section)) {
                    switch (key) {
                        case "unit_price", "payment_days", "contract_days" -> positive(id, value);
                        case "price_linkage", "linkage_waived_by_agreement" -> { if (!value.isBoolean()) invalid(id); }
                        case "notes" -> { if (!value.isNull()) string(id, value, 500, false); }
                        case "price_range" -> {
                            if (!value.isObject()) invalid(id);
                            for (String point : sortedKeys(value)) {
                                if (!Set.of("settlement", "walk_away").contains(point)) invalid(id);
                                positive(id, value.get(point));
                            }
                        }
                        case "concessions" -> strings(id, value);
                        case "objections" -> {
                            if (!value.isArray()) invalid(id);
                            for (JsonNode objection : value) {
                                if (!objection.isObject() || !objection.propertyNames().equals(Set.of("issue", "response"))) invalid(id);
                                string(id, objection.path("issue"), Integer.MAX_VALUE, false);
                                string(id, objection.path("response"), Integer.MAX_VALUE, false);
                            }
                        }
                        default -> invalid(id);
                    }
                } else if ("writing".equals(section)) {
                    switch (key) {
                        case "strategy", "opening_message" -> string(id, value, Integer.MAX_VALUE, false);
                        case "objection_scripts" -> strings(id, value);
                        default -> invalid(id);
                    }
                } else {
                    switch (key) {
                        case "title" -> string(id, value, 200, true);
                        case "cover_letter" -> string(id, value, 1500, false);
                        case "reply_by" -> {
                            string(id, value, 10, true);
                            try { LocalDate.parse(value.asString()); }
                            catch (DateTimeParseException ex) { invalid(id); }
                        }
                        case "linkage_terms" -> {
                            if (!value.isObject()) invalid(id);
                            for (String term : sortedKeys(value)) {
                                if (!NegotiationDocumentAssembler.LINKAGE_FIELDS.contains(term)) invalid(id);
                                string(id, value.get(term), Integer.MAX_VALUE, false);
                            }
                        }
                        default -> invalid(id);
                    }
                }
            }
        }
    }

    private static void scan(JsonNode node, String path, List<String> facts, List<String> systems) {
        if (node.isObject()) {
            for (String key : sortedKeys(node)) {
                String child = path + "/" + pointer(key);
                if (FACTS.contains(key)) facts.add(child);
                if (SYSTEM.contains(key) || key.startsWith("gap_") || key.startsWith("vs_")) systems.add(child);
                scan(node.get(key), child, facts, systems);
            }
        } else if (node.isArray()) {
            for (int i = 0; i < node.size(); i++) scan(node.get(i), path + "/" + i, facts, systems);
        }
    }

    private static ObjectNode impacts(ObjectNode revision, Set<String> scopes) {
        ObjectNode impacts = JSON.objectNode();
        ObjectNode internal = impacts.putObject("internal");
        internal.set("content_revision", revision.path("internal_content_rev").deepCopy());
        ArrayNode internalRecheck = internal.putArray("recheck");
        if (scopes.contains("TERMS")) internalRecheck.add("conditions");
        if (scopes.contains("TERMS") || scopes.contains("PRICE_RANGE")) internalRecheck.add("price_range");
        if (scopes.contains("INTERNAL_TEXT")) internalRecheck.add("text");
        internal.put("user_text_unchecked", scopes.contains("INTERNAL_TEXT"));
        ObjectNode external = impacts.putObject("external");
        external.set("content_revision", revision.path("external_content_rev").deepCopy());
        ArrayNode externalRecheck = external.putArray("recheck");
        if (scopes.contains("TERMS")) externalRecheck.add("conditions");
        if (scopes.contains("EXTERNAL_TEXT")) externalRecheck.add("text");
        return impacts;
    }

    private static boolean update(ObjectNode target, String key, JsonNode after, String path, ArrayNode changes) {
        JsonNode before = target.path(key);
        if (before.equals(after) || before.isMissingNode() && after.isNull()) return false;
        addChange(changes, path, before, after);
        target.set(key, after.deepCopy());
        return true;
    }

    private static void addChange(ArrayNode changes, String path, JsonNode before, JsonNode after) {
        ObjectNode change = changes.addObject().put("path", path);
        change.set("before", before.isMissingNode() ? JSON.nullNode() : before.deepCopy());
        change.set("after", after.deepCopy());
    }

    private static ObjectNode pricePoint(ObjectNode offer, String name) {
        for (JsonNode point : offer.path("price_range")) {
            if (point.isObject() && name.equals(point.path("name").asString())) return (ObjectNode) point;
        }
        return null;
    }

    private static void checkPriceOrder(UUID id, ObjectNode revision, ObjectNode offer) {
        ObjectNode settlement = pricePoint(offer, "settlement");
        ObjectNode walkAway = pricePoint(offer, "walk_away");
        if (settlement != null && walkAway != null && (revision.path("unit_price").asLong() > settlement.path("unit_price").asLong()
                || settlement.path("unit_price").asLong() > walkAway.path("unit_price").asLong())) invalid(id);
    }

    private static List<String> sortedKeys(JsonNode node) {
        return node.propertyNames().stream().sorted().toList();
    }

    private static boolean intersects(Set<String> left, Set<String> right) {
        return left.stream().anyMatch(right::contains);
    }

    private static String pointer(String value) { return value.replace("~", "~0").replace("/", "~1"); }
    private static void positive(UUID id, JsonNode value) {
        if (!value.isIntegralNumber() || !value.canConvertToInt() || value.intValue() <= 0) invalid(id);
    }
    private static void string(UUID id, JsonNode value, int max, boolean required) {
        if (!value.isString()) invalid(id);
        String text = value.asString();
        if (text.codePointCount(0, text.length()) > max || required && text.isBlank()) invalid(id);
    }
    private static void strings(UUID id, JsonNode value) {
        if (!value.isArray()) invalid(id);
        for (JsonNode item : value) string(id, item, Integer.MAX_VALUE, false);
    }
    private static void invalid(UUID id) { throw ApiException.validation(id); }
    private static ApiException noOffer(UUID id) {
        return ApiException.of(id, 422, "NO_OFFER", "협상안이 없습니다", false);
    }
}
