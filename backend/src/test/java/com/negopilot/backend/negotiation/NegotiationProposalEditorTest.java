package com.negopilot.backend.negotiation;

import com.negopilot.backend.common.api.ApiException;
import java.util.Set;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import tools.jackson.databind.node.ObjectNode;

import static com.negopilot.backend.negotiation.NegotiationPureFixtures.*;
import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class NegotiationProposalEditorTest {
    @Test
    void internalTextChangesOnlyInternalCounterAndUsesJsonPointerHistory() {
        ObjectNode original = revision();
        var result = NegotiationProposalEditor.edit(original, object("{\"writing\":{\"strategy\":\"변경한 전략\"}}"));
        assertThat(result.changed()).isTrue();
        assertThat(result.revision().path("revision").asInt()).isEqualTo(2);
        assertThat(result.revision().path("internal_content_rev").asInt()).isEqualTo(2);
        assertThat(result.revision().path("external_content_rev").asInt()).isEqualTo(1);
        assertThat(result.revision().path("terms_rev").asInt()).isEqualTo(1);
        assertThat(result.changedScopes()).isEqualTo(Set.of("INTERNAL_TEXT"));
        assertThat(result.changeSet().get(0).path("path").asString()).isEqualTo("/writing/strategy");
        assertThat(result.changeSet().get(0).path("before").asString()).isEqualTo("기존 전략");
        assertThat(original.path("internal_body").path("writing").path("strategy").asString()).isEqualTo("기존 전략");
    }

    @Test
    void notesChangesBothBodiesAndBothContentCounters() {
        var result = NegotiationProposalEditor.edit(revision(), object("{\"offer\":{\"notes\":\"납품 협의\"}}"));
        assertThat(result.changedScopes()).isEqualTo(Set.of("INTERNAL_TEXT", "EXTERNAL_TEXT"));
        assertThat(result.revision().path("internal_body").path("offer").path("notes").asString()).isEqualTo("납품 협의");
        assertThat(result.revision().path("external_body").path("delivery_notes").asString()).isEqualTo("납품 협의");
        assertThat(result.revision().path("internal_content_rev").asInt()).isEqualTo(2);
        assertThat(result.revision().path("external_content_rev").asInt()).isEqualTo(2);
        assertThat(result.impacts().path("internal").path("user_text_unchecked").asBoolean()).isTrue();
        var cleared = NegotiationProposalEditor.edit(result.revision(), object("{\"offer\":{\"notes\":null}}"));
        assertThat(cleared.revision().path("external_body").path("delivery_notes").isNull()).isTrue();
    }

    @Test
    void riskyTermsAreKeptAndDerivedFiguresAreNotRecalculated() {
        var result = NegotiationProposalEditor.edit(revision(), object("{\"offer\":{\"payment_days\":90,\"unit_price\":1050}}"));
        assertThat(result.revision().path("payment_days").asInt()).isEqualTo(90);
        assertThat(result.revision().path("terms_rev").asInt()).isEqualTo(2);
        assertThat(result.revision().path("external_content_rev").asInt()).isEqualTo(2);
        var internal = result.revision().path("internal_body");
        assertThat(internal.path("derived_stale").asBoolean()).isTrue();
        assertThat(internal.path("offer").path("contract_amount").asInt()).isEqualTo(10000000);
        assertThat(internal.path("offer").path("price_range").get(0).path("unit_price").asInt()).isEqualTo(1050);
        assertThat(internal.path("offer").path("price_range").get(0).path("vs_target_pct").doubleValue()).isEqualTo(11.1);
    }

    @Test
    void priceRangeUsesNamedPointsAndOnlyInternalContentCounterChanges() {
        var result = NegotiationProposalEditor.edit(revision(), object("{\"offer\":{\"price_range\":{\"settlement\":1200,\"walk_away\":1300}}}"));
        assertThat(result.changedScopes()).isEqualTo(Set.of("PRICE_RANGE"));
        assertThat(result.revision().path("external_content_rev").asInt()).isEqualTo(1);
        assertThat(result.revision().path("internal_content_rev").asInt()).isEqualTo(2);
        assertThat(result.revision().path("internal_body").path("offer").path("price_range").get(1).path("unit_price").asInt()).isEqualTo(1200);
        assertThat(result.revision().path("internal_body").path("offer").path("price_range").get(1).path("verdict_code").asString()).isEqualTo("OK");
    }

    @Test
    void sameValuesDoNotCreateRevisionOrHistory() {
        ObjectNode original = revision();
        var result = NegotiationProposalEditor.edit(original, object("""
                {"offer":{"payment_days":60,"notes":null},"writing":{"strategy":"기존 전략"},"external":{"title":"거래조건 제안"}}
                """));
        assertThat(result.changed()).isFalse();
        assertThat(result.changeSet()).isEmpty();
        assertThat(result.changedScopes()).isEmpty();
        assertThat(result.revision()).isEqualTo(original);
    }

    @Test
    void externalEditsOnlyIncrementExternalCounterAndMergeLinkageFields() {
        var result = NegotiationProposalEditor.edit(revision(), object("""
                {"external":{"reply_by":"2026-11-01","linkage_terms":{"기준 지표":"원자재 지수"}}}
                """));
        assertThat(result.changedScopes()).isEqualTo(Set.of("EXTERNAL_TEXT"));
        assertThat(result.revision().path("internal_content_rev").asInt()).isEqualTo(1);
        assertThat(result.revision().path("external_content_rev").asInt()).isEqualTo(2);
        assertThat(result.revision().path("external_body").path("linkage_terms").size()).isEqualTo(4);
    }

    @ParameterizedTest
    @ValueSource(strings = {"{\"offer\":{\"buyer_size\":{}}}", "{\"external\":{\"supplier\":\"SUP-0002\"}}",
            "{\"writing\":{\"anything\":[{\"transaction\":{}}]}}", "{\"offer\":{\"as_of\":\"2026-10-24\"}}"})
    void transactionFactsAnywhereRequireFreshReview(String changes) { error(changes, 409, "REVIEW_RECHECK_REQUIRED"); }

    @ParameterizedTest
    @ValueSource(strings = {"{\"offer\":{\"rationale\":[]}}", "{\"offer\":{\"price_range\":{\"opening\":1000}}}",
            "{\"external\":{\"sender\":{}}}", "{\"writing\":{\"validation\":{}}}",
            "{\"offer\":{\"gap_to_target_pct\":1}}", "{\"offer\":{\"price_range\":{\"vs_market_pct\":1}}}"})
    void systemFieldsAreNotEditable(String changes) { error(changes, 422, "FIELD_NOT_EDITABLE"); }

    @ParameterizedTest
    @ValueSource(strings = {"{\"offer\":{\"mystery\":1}}", "{\"extra\":{}}", "{\"offer\":{\"unit_price\":0}}",
            "{\"offer\":{\"payment_days\":1.5}}", "{\"offer\":{\"payment_days\":2147483648}}",
            "{\"offer\":{\"price_linkage\":null}}", "{\"offer\":{\"notes\":3}}", "{\"writing\":{\"strategy\":null}}",
            "{\"external\":{\"reply_by\":\"2026-02-30\"}}", "{\"external\":{\"linkage_terms\":{\"unknown\":\"\"}}}",
            "{\"offer\":{\"concessions\":[1]}}", "{\"offer\":{\"objections\":[{\"issue\":\"x\"}]}}",
            "{\"offer\":{\"unit_price\":1101}}", "{\"offer\":{\"price_range\":{\"settlement\":999}}}",
            "{\"offer\":{\"price_range\":{\"walk_away\":1099}}}"})
    void unknownFieldsInvalidTypesAndUnorderedPricesAreRejected(String changes) { error(changes, 400, "VALIDATION_ERROR"); }

    @Test
    void stringLengthsUseUnicodeCodePoints() {
        ObjectNode changes = object("{\"offer\":{}}");
        ((ObjectNode) changes.get("offer")).put("notes", "😀".repeat(500));
        assertThat(NegotiationProposalEditor.edit(revision(), changes).changed()).isTrue();
        ((ObjectNode) changes.get("offer")).put("notes", "😀".repeat(501));
        assertThatThrownBy(() -> NegotiationProposalEditor.edit(revision(), changes)).isInstanceOf(ApiException.class);
    }

    @Test
    void fieldErrorIncludesPathsAndKeepsRequestId() {
        UUID requestId = UUID.randomUUID();
        assertThatThrownBy(() -> NegotiationProposalEditor.edit(requestId, revision(), object("{\"offer\":{\"warnings\":[]}}")))
                .isInstanceOfSatisfying(ApiException.class, error -> {
                    assertThat(error.requestId).isEqualTo(requestId);
                    assertThat(JSON.valueToTree(error.details).path("paths").get(0).asString()).isEqualTo("/offer/warnings");
                });
    }

    private static void error(String changes, int status, String code) {
        assertThatThrownBy(() -> NegotiationProposalEditor.edit(revision(), object(changes)))
                .isInstanceOfSatisfying(ApiException.class, error -> {
                    assertThat(error.status.value()).isEqualTo(status);
                    assertThat(error.code).isEqualTo(code);
                });
    }
}
