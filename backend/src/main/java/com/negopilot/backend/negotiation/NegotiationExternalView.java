package com.negopilot.backend.negotiation;

import com.fasterxml.jackson.annotation.JsonProperty;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ObjectNode;

public record NegotiationExternalView(
        @JsonProperty("proposal_id") String proposalId,
        @JsonProperty("proposal_revision") int proposalRevision,
        @JsonProperty("external_document") ObjectNode externalDocument,
        Validation validation) {

    public static NegotiationExternalView from(ObjectNode workspace, ObjectNode proposal,
                                               ObjectNode revision, ObjectNode validation) {
        return new NegotiationExternalView(proposal.path("id").asString(), revision.path("revision").asInt(),
                NegotiationDocumentAssembler.assemble(workspace, proposal, revision, "draft"),
                new Validation(validation.path("status").asString(),
                        integerOrNull(validation.path("checked_revision")), textOrNull(validation.path("conclusion"))));
    }

    public record Validation(String status, @JsonProperty("checked_revision") Integer checkedRevision, String conclusion) {}

    private static Integer integerOrNull(JsonNode value) {
        return value.isIntegralNumber() ? value.intValue() : null;
    }

    private static String textOrNull(JsonNode value) {
        return value.isString() ? value.asString() : null;
    }
}
