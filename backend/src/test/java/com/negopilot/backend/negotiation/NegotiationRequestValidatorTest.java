package com.negopilot.backend.negotiation;

import com.negopilot.backend.common.api.ApiException;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.node.ObjectNode;
import static com.negopilot.backend.negotiation.NegotiationFixtures.*;
import static org.assertj.core.api.Assertions.*;

class NegotiationRequestValidatorTest {
    private final NegotiationRequestValidator validator = new NegotiationRequestValidator(JSON);
    private final UUID requestId = UUID.randomUUID();

    @Test void parsesOnlyOneObjectWithUniqueKeysAndNoNullCharacters() {
        assertThat(validator.parse("{}", requestId)).isEqualTo(object("{}"));
        for (String raw : new String[] {"", "null", "[]", "{} {}", "{\"a\":1,\"a\":2}",
                "{\"nested\":{\"a\":1,\"a\":2}}", "{\"text\":\"\\u0000\"}", "{\"\\u0000\":1}", " ".repeat(64001)}) {
            invalid(() -> validator.parse(raw, requestId));
        }
        invalid(() -> validator.parse(null, requestId));
    }

    @Test void createAndWorkspacePatchRejectUnknownKeysAndBadVersions() {
        ObjectNode create = object("{\"supplier_id\":\"SUP-0060\",\"rfq_id\":\"RFQ-2026-002\"}");
        assertThat(validator.create(create, requestId)).isSameAs(create);
        for (String key : new String[] {"user_id", "owner_id", "requested_by", "sender", "rfq"})
            invalid(() -> validator.create(create.deepCopy().put(key, "spoofed"), requestId));
        invalid(() -> validator.create(create.deepCopy().put("supplier_id", "SUP-6"), requestId));
        invalid(() -> validator.create(create.deepCopy().put("title", " "), requestId));
        invalid(() -> validator.create(create.deepCopy().put("title", "가".repeat(101)), requestId));
        assertThat(validator.workspacePatch(object("{\"row_version\":0,\"status\":\"ARCHIVED\"}"), requestId)
                .path("status").stringValue()).isEqualTo("ARCHIVED");
        for (String raw : new String[] {"{}", "{\"row_version\":0}", "{\"row_version\":-1,\"title\":\"제목\"}",
                "{\"row_version\":1.5,\"title\":\"제목\"}", "{\"row_version\":0,\"status\":\"CLOSED\"}"})
            invalid(() -> validator.workspacePatch(object(raw), requestId));
    }

    @Test void reviewPreservesUnknownFactsButRejectsSpoofingAndInvalidDates() {
        ObjectNode body = reviewInput();
        ObjectNode checked = validator.review(body, requestId);
        assertThat(checked).isSameAs(body);
        assertThat(checked.path("supplier_size").isEmpty()).isTrue();
        assertThat(checked.get("energy_ratio").isNull()).isTrue();
        assertThat(checked.get("raw_material_ratio").doubleValue()).isEqualTo(0.4200123456789);
        assertThat(validator.review(body.deepCopy().putNull("base_revision"), requestId).get("base_revision").isNull()).isTrue();
        for (String key : new String[] {"supplier", "rfq_id", "sender", "edited_by", "owner_id"})
            invalid(() -> validator.review(body.deepCopy().put(key, "spoofed"), requestId));
        invalid(() -> validator.review(body.deepCopy().put("as_of", "2026-02-30"), requestId));
        invalid(() -> validator.review(body.deepCopy().put("raw_material_ratio", 1.01), requestId));
        invalid(() -> validator.review(body.deepCopy().put("unit_price", 1.5), requestId));
        invalid(() -> validator.review(body.deepCopy().putNull("supplier_size"), requestId));
        invalid(() -> validator.review(body.deepCopy().put("base_revision", 0), requestId));
    }

    @Test void reviewValidatesNestedCompanyAndTransactionFields() {
        ObjectNode body = reviewInput();
        body.set("buyer_size", object("{\"independence_met\":null,\"average_revenue_eok\":120.01,\"transition_reviewed\":false}"));
        body.set("transaction", object("""
                {"transaction_type":"manufacturing","actual_contract_amount":3000000000,
                 "party_requirements_confirmed":null,"contracted_on":"2026-10-23",
                 "linkage_details":[{"category":"material","name":"철강","cost_ratio":null,
                   "trigger_ratio":0.05,"evidence_references":["계약서"]}]}
                """));
        assertThat(validator.review(body, requestId)).isSameAs(body);
        for (String company : new String[] {"{\"sender\":{}}", "{\"average_revenue_eok\":-1}",
                "{\"independence_met\":\"true\"}", "{\"transition_reviewed\":null}"}) {
            ObjectNode changed = body.deepCopy(); changed.set("buyer_size", object(company));
            invalid(() -> validator.review(changed, requestId));
        }
        for (String facts : new String[] {"{\"supplier\":\"SUP-0001\"}", "{\"receipt_date\":\"2026-02-30\"}",
                "{\"committed_quantity\":0}", "{\"reduction_evidence\":[4]}",
                "{\"linkage_details\":[{\"category\":\"material\",\"name\":\"철강\",\"trigger_ratio\":0.11}]}",
                "{\"linkage_details\":[{\"category\":\"material\",\"name\":\"철강\",\"unknown\":true}]}"}) {
            ObjectNode changed = body.deepCopy(); changed.set("transaction", object(facts));
            invalid(() -> validator.review(changed, requestId));
        }
    }

    @Test void proposalOnlyAcceptsUserNegotiationGoalsAndDoesNotFillPrices() {
        ObjectNode body = object("{\"review_revision\":1,\"offer\":{}}");
        assertThat(validator.proposal(body, requestId).path("offer").isEmpty()).isTrue();
        for (String key : new String[] {"sender", "unit_price", "transaction", "supplier", "rfq_id", "intent_unknown"}) {
            ObjectNode changed = body.deepCopy(); ((ObjectNode) changed.get("offer")).put(key, "spoofed");
            invalid(() -> validator.proposal(changed, requestId));
        }
        invalid(() -> validator.proposal(object("{\"review_revision\":1,\"offer\":{\"target_price\":100,\"max_price\":99}}"), requestId));
        invalid(() -> validator.proposal(object("{\"review_revision\":1,\"offer\":{\"target_price\":1.5}}"), requestId));
        ObjectNode notes = body.deepCopy(); ((ObjectNode) notes.get("offer")).put("notes", "가".repeat(501));
        invalid(() -> validator.proposal(notes, requestId));
        assertThat(validator.proposal(object("{\"review_revision\":1,\"offer\":{\"notes\":null,\"target_price\":1750}}"), requestId)
                .at("/offer/target_price").intValue()).isEqualTo(1750);
    }

    @Test void editEnvelopeLeavesPathChecksToEditorAndRejectsSpoofedMetadata() {
        assertThat(validator.proposalPatchEnvelope(object("{\"base_revision\":1,\"changes\":{}}"), requestId)
                .path("changes").isEmpty()).isTrue();
        for (String raw : new String[] {"{\"base_revision\":1,\"changes\":[],\"change_note\":\"메모\"}",
                "{\"base_revision\":1,\"changes\":{},\"edited_by\":\"spoofed\"}",
                "{\"base_revision\":0,\"changes\":{}}", "{\"base_revision\":1,\"changes\":{},\"change_note\":null}"})
            invalid(() -> validator.proposalPatchEnvelope(object(raw), requestId));
    }

    @Test void exportsAcceptOnlySupportedUniqueFormatsAndRejectInternalDetails() {
        assertThat(validator.export(object("{\"revision\":1,\"document_type\":\"external\",\"formats\":[\"docx\",\"pdf\"],\"final\":true}"), requestId)
                .path("formats")).hasSize(2);
        assertThatThrownBy(() -> validator.export(object("{\"revision\":1,\"document_type\":\"internal\",\"formats\":[\"pdf\"]}"), requestId))
                .isInstanceOfSatisfying(ApiException.class, e -> {
                    assertThat(e.status.value()).isEqualTo(422);
                    assertThat(e.code).isEqualTo("UNSUPPORTED_EXPORT_FORMAT");
                });
        for (String formats : new String[] {"[]", "[\"pdf\",\"pdf\"]", "[\"xlsx\"]", "null"})
            invalid(() -> validator.export(object("{\"revision\":1,\"document_type\":\"external\",\"formats\":" + formats + "}"), requestId));
        invalid(() -> validator.export(object("{\"revision\":1,\"document_type\":\"external\",\"formats\":[\"pdf\"],\"include_review_details\":false}"), requestId));
    }

    private void invalid(Runnable operation) {
        assertThatThrownBy(operation::run).isInstanceOfSatisfying(ApiException.class, e -> {
            assertThat(e.status.value()).isEqualTo(400);
            assertThat(e.code).isEqualTo("VALIDATION_ERROR");
            assertThat(e.requestId).isEqualTo(requestId);
        });
    }
}
