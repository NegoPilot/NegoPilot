package com.negopilot.backend.negotiation;

import java.util.List;
import java.util.Set;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.JsonNodeFactory;
import tools.jackson.databind.node.ObjectNode;

public final class NegotiationDocumentAssembler {
    public static final List<String> SHARED_FIELDS = List.of("unit_price", "payment_days", "price_linkage",
            "contract_days", "linkage_waived_by_agreement", "raw_material_ratio", "energy_ratio", "annual_volume", "as_of");
    public static final Set<String> EXTERNAL_BODY_FIELDS = Set.of("schema_version", "title", "issued_on", "reply_by",
            "supplier_name", "part_name", "vehicle", "material", "payment_method", "linkage_needs_input",
            "linkage_terms", "delivery_notes", "cover_letter", "sender");
    public static final Set<String> LINKAGE_FIELDS = Set.of("기준 지표", "조정 요건(변동률)", "조정 주기", "조정 산식");
    private static final List<String> SENDER_FIELDS = List.of("company", "department", "contact_name", "job_title", "phone", "email");

    public static ObjectNode assemble(ObjectNode workspace, ObjectNode proposal, ObjectNode revision, String status) {
        if (!Set.of("draft", "final").contains(status)) throw new IllegalArgumentException("Invalid document status");
        if (!revision.path("external_body").isObject()) throw new IllegalArgumentException("No external document");
        ObjectNode document = externalBody(revision.path("external_body"));
        SHARED_FIELDS.forEach(key -> document.set(key, revision.path(key).isMissingNode()
                ? JsonNodeFactory.instance.nullNode() : revision.path(key).deepCopy()));
        document.set("supplier_id", workspace.path("supplier_id").deepCopy());
        document.set("rfq_id", workspace.path("rfq_id").deepCopy());
        document.set("document_id", proposal.path("mail_document_id").deepCopy());
        document.set("version", revision.path("external_content_rev").deepCopy());
        document.put("status", status);
        document.putNull("content_hash");
        JsonNode offer = revision.path("internal_body").path("offer");
        JsonNode amount = offer.path("contract_amount");
        if (amount.isIntegralNumber() && amount.longValue() >= 0) document.set("contract_amount", amount.deepCopy());
        else document.put("contract_amount", 0);
        JsonNode verdict = offer.path("verdict_code");
        if (verdict.isString() && Set.of("OK", "CHECK_NEEDED", "VIOLATION").contains(verdict.asString())) {
            document.set("verdict_code", verdict.deepCopy());
        } else document.putNull("verdict_code");
        return document;
    }

    public static ObjectNode externalBody(JsonNode source) {
        ObjectNode body = JsonNodeFactory.instance.objectNode();
        EXTERNAL_BODY_FIELDS.stream().sorted().forEach(key -> {
            if (source.has(key)) body.set(key, source.get(key).deepCopy());
        });
        if (body.path("sender").isObject()) {
            ObjectNode sender = JsonNodeFactory.instance.objectNode();
            SENDER_FIELDS.forEach(key -> {
                if (body.path("sender").has(key)) sender.set(key, body.path("sender").get(key).deepCopy());
            });
            body.set("sender", sender);
        }
        if (body.path("linkage_terms").isObject()) {
            ObjectNode terms = JsonNodeFactory.instance.objectNode();
            LINKAGE_FIELDS.stream().sorted().forEach(key -> {
                if (body.path("linkage_terms").has(key)) terms.set(key, body.path("linkage_terms").get(key).deepCopy());
            });
            body.set("linkage_terms", terms);
        }
        return body;
    }
}
