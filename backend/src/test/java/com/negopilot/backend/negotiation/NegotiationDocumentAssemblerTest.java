package com.negopilot.backend.negotiation;

import java.util.Set;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ObjectNode;

import static com.negopilot.backend.negotiation.NegotiationPureFixtures.*;
import static org.assertj.core.api.Assertions.assertThat;

class NegotiationDocumentAssemblerTest {
    private static final Set<String> DOCUMENT_FIELDS = Set.of("schema_version", "document_id", "version", "status",
            "issued_on", "reply_by", "as_of", "supplier_id", "supplier_name", "sender", "title", "rfq_id",
            "part_name", "vehicle", "material", "annual_volume", "unit_price", "contract_days", "contract_amount",
            "payment_days", "payment_method", "price_linkage", "linkage_waived_by_agreement", "linkage_needs_input",
            "raw_material_ratio", "energy_ratio", "linkage_terms", "delivery_notes", "cover_letter", "verdict_code", "content_hash");

    @Test
    void assembledFieldsExactlyMatchAgentDocumentAndPreserveRatios() {
        ObjectNode result = NegotiationDocumentAssembler.assemble(workspace(), proposal(), revision(), "draft");
        assertThat(result.propertyNames()).isEqualTo(DOCUMENT_FIELDS);
        assertThat(result.path("raw_material_ratio").doubleValue()).isEqualTo(0.123456789123);
        assertThat(result.path("energy_ratio").isNull()).isTrue();
        assertThat(result.path("version").asInt()).isEqualTo(1);
        assertThat(result.path("content_hash").isNull()).isTrue();
        assertThat(result.path("contract_amount").asLong()).isEqualTo(10000000);
    }

    @Test
    void assemblesEachSavedRevisionInsteadOfUsingHeadValues() {
        ObjectNode old = revision();
        ObjectNode edited = revision().put("unit_price", 1050).put("external_content_rev", 2);
        ObjectNode first = NegotiationDocumentAssembler.assemble(workspace(), proposal(), old, "draft");
        ObjectNode second = NegotiationDocumentAssembler.assemble(workspace(), proposal(), edited, "final");
        assertThat(first.path("unit_price").asInt()).isEqualTo(1000);
        assertThat(first.path("version").asInt()).isEqualTo(1);
        assertThat(second.path("unit_price").asInt()).isEqualTo(1050);
        assertThat(second.path("version").asInt()).isEqualTo(2);
        assertThat(second.path("status").asString()).isEqualTo("final");
    }

    @Test
    void externalDtoKeepsOnlyFixedPublicKeysAndNoInternalStrategyValues() {
        ObjectNode row = revision();
        ObjectNode external = (ObjectNode) row.get("external_body");
        external.put("max_price", 937291);
        external.put("warnings", "내부 경고");
        ((ObjectNode) external.get("sender")).put("target_price", 937291);
        ((ObjectNode) external.get("linkage_terms")).put("rationale", "내부 판단");
        ObjectNode validation = object("""
                {"status":"checked","checked_revision":1,"conclusion":"conditional","warnings":["내부 경고"]}
                """);
        JsonNode result = JSON.valueToTree(NegotiationExternalView.from(workspace(), proposal(), row, validation));
        assertThat(result.propertyNames()).isEqualTo(Set.of("proposal_id", "proposal_revision", "external_document", "validation"));
        assertThat(result.get("external_document").propertyNames()).isEqualTo(DOCUMENT_FIELDS);
        assertThat(result.get("validation").propertyNames()).isEqualTo(Set.of("status", "checked_revision", "conclusion"));
        assertThat(result.toString()).doesNotContain("937291", "price_range", "concessions", "objections", "writing",
                "benchmarks", "considered", "filled", "rationale", "warnings", "law_refs", "target_price", "max_price");
    }

    @Test
    void absentDerivedValuesUseAgentAcceptedPlaceholders() {
        ObjectNode row = revision();
        ((ObjectNode) row.path("internal_body").path("offer")).remove("contract_amount");
        ((ObjectNode) row.path("internal_body").path("offer")).remove("verdict_code");
        ObjectNode result = NegotiationDocumentAssembler.assemble(workspace(), proposal(), row, "draft");
        assertThat(result.path("contract_amount").asInt()).isZero();
        assertThat(result.path("verdict_code").isNull()).isTrue();
    }
}
