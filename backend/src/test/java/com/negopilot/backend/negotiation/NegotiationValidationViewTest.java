package com.negopilot.backend.negotiation;

import java.time.OffsetDateTime;
import java.util.List;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.node.ObjectNode;

import static com.negopilot.backend.negotiation.NegotiationPureFixtures.object;
import static org.assertj.core.api.Assertions.assertThat;

class NegotiationValidationViewTest {
    private static final OffsetDateTime NOW = OffsetDateTime.parse("2026-10-23T12:00:00Z");

    @Test
    void termsChangeRequiresRecheckAndKeepsPriorConclusionOnlyInPrevious() {
        ObjectNode edited = object("""
                {"revision":2,"changed_scopes":["TERMS"],"change_set":[{"path":"/offer/payment_days"}]}
                """);
        ObjectNode result = NegotiationValidationView.calculate(2, List.of(edited), List.of(checked(1)), NOW);
        assertThat(result.path("status").asString()).isEqualTo("recheck_required");
        assertThat(result.path("checked_revision").asInt()).isEqualTo(1);
        assertThat(result.path("conclusion").isNull()).isTrue();
        assertThat(result.path("previous").path("conclusion").asString()).isEqualTo("conditional");
        assertThat(result.path("changed_fields")).isEqualTo(object("{\"paths\":[\"/offer/payment_days\"]}").get("paths"));
    }

    @Test
    void inheritsOnlyInternalTextChangesAndDoesNotMutateStoredLimitations() {
        ObjectNode checked = checked(1);
        ObjectNode edited = object("{\"revision\":2,\"changed_scopes\":[\"INTERNAL_TEXT\"],\"change_set\":[]}");
        ObjectNode result = NegotiationValidationView.calculate(2, List.of(edited), List.of(checked), NOW);
        assertThat(result.path("status").asString()).isEqualTo("checked");
        assertThat(result.path("inherited_from").asInt()).isEqualTo(1);
        assertThat(result.path("checked_revision").asInt()).isEqualTo(1);
        assertThat(result.path("conclusion").asString()).isEqualTo("conditional");
        assertThat(result.path("limitations").toString()).contains("사용자가 수정한 내부 문구는 검사하지 않았습니다");
        assertThat(checked.path("limitations").size()).isEqualTo(1);
    }

    @Test
    void everyRevisionBetweenCheckedAndHeadParticipatesInInheritance() {
        ObjectNode terms = object("{\"revision\":2,\"changed_scopes\":[\"EXTERNAL_TEXT\"],\"change_set\":[]}");
        ObjectNode text = object("{\"revision\":3,\"changed_scopes\":[\"INTERNAL_TEXT\"],\"change_set\":[]}");
        assertThat(NegotiationValidationView.calculate(3, List.of(text, terms), List.of(checked(1)), NOW)
                .path("status").asString()).isEqualTo("recheck_required");
    }

    @Test
    void headCheckReplacesEarlierResult() {
        ObjectNode head = checked(2).put("conclusion", "risk_identified");
        ObjectNode result = NegotiationValidationView.calculate(2, List.of(), List.of(head, checked(1)), NOW);
        assertThat(result.path("checked_revision").asInt()).isEqualTo(2);
        assertThat(result.path("conclusion").asString()).isEqualTo("risk_identified");
        assertThat(result.has("previous")).isFalse();
    }

    @Test
    void activeCheckingTakesPrecedenceAndExpiredCheckingBecomesFailed() {
        ObjectNode checking = object("""
                {"revision":2,"status":"checking","lease_until":"2026-10-23T12:00:01Z",
                 "started_at":"2026-10-23T11:00:00Z"}
                """);
        ObjectNode result = NegotiationValidationView.calculate(2, List.of(), List.of(checking, checked(2)), NOW);
        assertThat(result.path("status").asString()).isEqualTo("checking");
        checking.put("lease_until", NOW.toString());
        checking.put("started_at", NOW.toString());
        ObjectNode expired = NegotiationValidationView.calculate(2, List.of(), List.of(checking, checked(2)), NOW);
        assertThat(expired.path("status").asString()).isEqualTo("failed");
        assertThat(expired.path("error_code").asString()).isEqualTo("STALE_CHECK");
        assertThat(expired.path("conclusion").isNull()).isTrue();
        assertThat(checking.path("status").asString()).isEqualTo("checking");
    }

    @Test
    void latestFailedHeadDoesNotExposeOldCheckedConclusion() {
        ObjectNode failed = object("""
                {"revision":2,"status":"failed","error_code":"UPSTREAM_TIMEOUT","started_at":"2026-10-23T12:00:00Z"}
                """);
        ObjectNode result = NegotiationValidationView.calculate(2, List.of(), List.of(checked(2), failed), NOW);
        assertThat(result.path("status").asString()).isEqualTo("failed");
        assertThat(result.path("conclusion").isNull()).isTrue();
    }

    @Test
    void noCheckedResultsAndChecksOfFutureRevisionsAreIgnored() {
        ObjectNode result = NegotiationValidationView.calculate(1, List.of(), List.of(checked(2)), NOW);
        assertThat(result.path("status").asString()).isEqualTo("not_checked");
        assertThat(result.path("conclusion").isNull()).isTrue();
        assertThat(result.path("checked_revision").isNull()).isTrue();
    }

    private static ObjectNode checked(int revision) {
        return object("""
                {"status":"checked","conclusion":"conditional","started_at":"2026-10-23T11:30:00Z",
                 "scope":["선택한 협상안의 조건 검사"],"limitations":["문구 검사는 아직 구현되지 않았습니다"],
                 "warnings":[],"violations":[],"text_findings":null,"unresolved_issue_ids":["I01"]}
                """).put("revision", revision);
    }
}
