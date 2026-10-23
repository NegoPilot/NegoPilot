package com.negopilot.backend.negotiation;

import com.negopilot.backend.agent.AgentProperties;
import com.negopilot.backend.agent.GenerationGateway;
import com.negopilot.backend.agent.LegalReviewRequestFactory;
import com.negopilot.backend.common.api.ApiException;
import com.negopilot.backend.supplier.SupplierLookupGateway;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.OffsetDateTime;
import java.util.HashMap;
import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.support.AbstractPlatformTransactionManager;
import org.springframework.transaction.support.DefaultTransactionStatus;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ObjectNode;
import static com.negopilot.backend.negotiation.NegotiationFixtures.*;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class NegotiationServiceTest {
    final UUID owner = UUID.randomUUID();
    MemoryNegotiationRepository repository;
    GenerationGateway gateway;
    SupplierLookupGateway suppliers;
    NegotiationService service;
    final Map<String, byte[]> bytes = new HashMap<>();
    String assessmentStatus;

    @BeforeEach void setup() {
        repository = new MemoryNegotiationRepository();
        gateway = mock(GenerationGateway.class);
        suppliers = mock(SupplierLookupGateway.class);
        assessmentStatus = "ready";
        var properties = new AgentProperties(URI.create("http://agent.invalid"), "test", Duration.ofSeconds(1), Duration.ofSeconds(2));
        var transactions = new TransactionTemplate(new AbstractPlatformTransactionManager() {
            @Override protected Object doGetTransaction() { return new Object(); }
            @Override protected void doBegin(Object tx, TransactionDefinition definition) {}
            @Override protected void doCommit(DefaultTransactionStatus status) {}
            @Override protected void doRollback(DefaultTransactionStatus status) {}
        });
        var requests = new NegotiationRequests(repository, transactions, JSON, properties);
        NegotiationExportStore store = new NegotiationExportStore() {
            public void write(String key, byte[] value) { bytes.put(key, value.clone()); }
            public byte[] read(String key) { return bytes.get(key).clone(); }
        };
        service = new NegotiationService(repository, transactions, gateway, suppliers, new LegalReviewRequestFactory(JSON),
                JSON, requests, store, properties);
        when(suppliers.detail(anyString(), any())).thenReturn(object("{\"data\":{\"supplier_id\":\"SUP-0060\",\"name\":\"온새프레스\"}}"));
        when(gateway.review(any(), any())).thenAnswer(call -> {
            assertThat(TransactionSynchronizationManager.isActualTransactionActive()).isFalse();
            return reviewResponse(call.getArgument(0), assessmentStatus);
        });
        when(gateway.propose(any(), any())).thenAnswer(call -> {
            assertThat(TransactionSynchronizationManager.isActualTransactionActive()).isFalse();
            return proposalResponse(call.getArgument(0));
        });
        when(gateway.render(any(), any(), anyString())).thenAnswer(call -> {
            assertThat(TransactionSynchronizationManager.isActualTransactionActive()).isFalse();
            return renderResponse(call.getArgument(0), call.getArgument(1), call.getArgument(2));
        });
    }

    @Test void reviewStoresTokenOnlyInItsDedicatedColumnAndRejectsStaleBase() {
        UUID workspace = workspace();
        var first = service.review(owner, workspace, reviewInput(), null, UUID.randomUUID());
        assertThat(first.status()).isEqualTo(201);
        assertThat(first.body().path("review_revision").intValue()).isEqualTo(1);
        assertThat(first.body().toString()).doesNotContain("review_token", TOKEN);
        ObjectNode stored = repository.table("neg_review_revisions").getFirst();
        assertThat(stored.path("review_token").asText()).isEqualTo(TOKEN);
        assertThat(stored.get("response").toString()).doesNotContain("review_token", TOKEN);
        assertThat(stored.get("legal_review").toString()).doesNotContain("review_token", TOKEN);
        assertThat(service.getReview(owner, workspace, 1, UUID.randomUUID()).toString()).doesNotContain("review_token", TOKEN);
        error(() -> service.review(owner, workspace, reviewInput(), null, UUID.randomUUID()), 409, "REVISION_CONFLICT");
        verify(gateway, times(1)).review(any(), any());
    }

    @Test void eligibilityMapsUnknownInformationAndBlocksMissingOrBlockedAssessments() {
        assessmentStatus = "needs_information";
        UUID workspace = workspace();
        var review = service.review(owner, workspace, reviewInput(), null, UUID.randomUUID());
        assertThat(review.body().path("eligibility").asText()).isEqualTo("conditional");
        assertThat(proposal(workspace).path("validation").path("conclusion").asText()).isEqualTo("conditional");
        for (String assessment : new String[] {"blocked", null}) {
            assessmentStatus = assessment;
            UUID blocked = workspace();
            service.review(owner, blocked, reviewInput(), null, UUID.randomUUID());
            error(() -> proposal(blocked), 422, "REVIEW_NOT_READY");
        }
        verify(gateway, times(1)).propose(any(), any());
    }

    @Test void generationKeepsExplicitGoalAndSessionSenderWithoutReviewUnitPriceOrTransaction() {
        UUID workspace = reviewed();
        var proposal = proposal(workspace);
        JsonNode sent = proposal.at("/generation_input_snapshot/offer");
        assertThat(sent.has("unit_price")).isFalse();
        assertThat(sent.has("transaction")).isFalse();
        assertThat(sent.path("target_price").intValue()).isEqualTo(1750);
        assertThat(sent.path("sender")).isEqualTo(object("{\"name\":\"세션 회원\",\"department\":\"구매팀\",\"job_title\":\"매니저\",\"email\":\"session@example.com\"}"));
        assertThat(sent.get("energy_ratio").isNull()).isTrue();
        assertThat(sent.get("supplier_size").isEmpty()).isTrue();
        assertThat(proposal.at("/validation/unresolved_issue_ids/0").asText()).isEqualTo("unresolved-1");
        service.propose(owner, workspace, object("{\"review_revision\":1,\"offer\":{}}"), null, UUID.randomUUID());
        JsonNode second = repository.table("neg_proposals").getLast().at("/generation_input/offer");
        assertThat(second.has("target_price")).isFalse();
        assertThat(repository.table("neg_proposals").stream().filter(row -> row.path("status").asText().equals("ACTIVE"))).hasSize(1);
    }

    @Test void anotherOwnerCannotReadAnyWorkspaceResourceOrExportFile() {
        UUID workspace = reviewed(); UUID proposal = id(proposal(workspace), "proposal_id");
        var exported = export(workspace, proposal, 1, false);
        UUID file = id(exported.body().path("files").get(0), "file_id"); UUID stranger = UUID.randomUUID();
        error(() -> service.get(stranger, workspace, UUID.randomUUID()), 404, "RESOURCE_NOT_FOUND");
        error(() -> service.getReview(stranger, workspace, 1, UUID.randomUUID()), 404, "RESOURCE_NOT_FOUND");
        error(() -> service.getProposal(stranger, workspace, proposal, null, UUID.randomUUID()), 404, "RESOURCE_NOT_FOUND");
        error(() -> service.external(stranger, workspace, proposal, null, UUID.randomUUID()), 404, "RESOURCE_NOT_FOUND");
        error(() -> service.download(stranger, file, UUID.randomUUID()), 404, "RESOURCE_NOT_FOUND");
    }

    @Test void externalDtoNeverContainsInternalFieldsOrPrivateMaximumPrice() {
        UUID workspace = reviewed(); UUID proposal = id(proposal(workspace), "proposal_id");
        JsonNode external = JSON.valueToTree(service.external(owner, workspace, proposal, null, UUID.randomUUID()));
        assertThat(external.propertyNames()).containsExactlyInAnyOrder("proposal_id", "proposal_revision", "external_document", "validation");
        assertThat(external.toString()).doesNotContain("price_range", "concessions", "objections", "writing", "benchmarks",
                "considered", "filled", "rationale", "warnings", "law_refs", "target_price", "max_price", Integer.toString(PRIVATE_MAX_PRICE));
        assertThat(external.path("validation").propertyNames()).containsExactlyInAnyOrder("status", "checked_revision", "conclusion");
    }

    @Test void editingRejectsConflictsAndNoChangesDoNotCreateARevision() {
        UUID workspace = reviewed(); UUID proposal = id(proposal(workspace), "proposal_id");
        var unchanged = patch(workspace, proposal, 1, "{\"offer\":{\"payment_days\":60}}");
        assertThat(unchanged.body().path("head_revision").intValue()).isEqualTo(1);
        assertThat(repository.table("neg_proposal_revisions")).hasSize(1);
        error(() -> patch(workspace, proposal, 2, "{}"), 409, "REVISION_CONFLICT");
        error(() -> patch(workspace, proposal, 1, "{\"offer\":{\"supplier_size\":{}}}"), 409, "REVIEW_RECHECK_REQUIRED");
        error(() -> patch(workspace, proposal, 1, "{\"offer\":{\"warnings\":[]}}"), 422, "FIELD_NOT_EDITABLE");
        error(() -> patch(workspace, proposal, 1, "{\"offer\":{\"unknown\":1}}"), 400, "VALIDATION_ERROR");
        var changed = patch(workspace, proposal, 1, "{\"offer\":{\"payment_days\":45}}");
        assertThat(changed.body().at("/validation/status").asText()).isEqualTo("recheck_required");
        assertThat(changed.body().at("/validation/conclusion").isNull()).isTrue();
        assertThat(changed.body().at("/validation/previous").isObject()).isTrue();
    }

    @Test void internalTextKeepsExternalVersionWhileNotesAdvanceBoth() {
        UUID workspace = reviewed(); UUID proposal = id(proposal(workspace), "proposal_id");
        var internal = patch(workspace, proposal, 1, "{\"writing\":{\"strategy\":\"변경한 전략\"}}");
        assertThat(internal.body().at("/external_document/version").intValue()).isEqualTo(1);
        assertThat(internal.body().at("/validation/inherited_from").intValue()).isEqualTo(1);
        var notes = patch(workspace, proposal, 2, "{\"offer\":{\"notes\":\"납품 협의\"}}");
        assertThat(notes.body().at("/external_document/version").intValue()).isEqualTo(2);
        assertThat(notes.body().at("/impacts/internal/content_revision").intValue()).isEqualTo(3);
        assertThat(notes.body().at("/impacts/external/content_revision").intValue()).isEqualTo(2);
        assertThat(notes.body().at("/external_document/delivery_notes").asText()).isEqualTo("납품 협의");
    }

    @Test void exportsUseTheRequestedRevisionAndReuseReadyBundles() {
        UUID workspace = reviewed(); UUID proposal = id(proposal(workspace), "proposal_id");
        var first = export(workspace, proposal, 1, false);
        patch(workspace, proposal, 1, "{\"offer\":{\"unit_price\":1900}}");
        var second = export(workspace, proposal, 2, false);
        assertThat(first.status()).isEqualTo(201); assertThat(second.status()).isEqualTo(201);
        verify(gateway).render(any(), argThat(doc -> doc.path("unit_price").intValue() == 1948 && doc.path("version").intValue() == 1), eq("pdf"));
        verify(gateway).render(any(), argThat(doc -> doc.path("unit_price").intValue() == 1900 && doc.path("version").intValue() == 2), eq("pdf"));
        var reused = export(workspace, proposal, 1, false);
        assertThat(reused.status()).isEqualTo(200); assertThat(reused.body()).isEqualTo(first.body());
        verify(gateway, times(2)).render(any(), any(), anyString());
        error(() -> export(workspace, proposal, 1, true), 422, "EXPORT_NOT_ALLOWED");
    }

    @Test void recheckRecordsViolationsAsCheckedAndExportRecordsFailure() {
        UUID workspace = reviewed(); UUID proposal = id(proposal(workspace), "proposal_id");
        patch(workspace, proposal, 1, "{\"offer\":{\"payment_days\":90}}");
        doThrow(ApiException.of(UUID.randomUUID(), 422, "OFFER_VIOLATION", "위반 조건", false,
                object("{\"findings\":[{\"code\":\"PAYMENT_DAYS_OVER_60\"}]}"))).when(gateway).render(any(), any(), anyString());
        var validation = service.validate(owner, workspace, proposal, 2, object("{}"), null, UUID.randomUUID());
        assertThat(validation.status()).isEqualTo(200);
        assertThat(validation.body().at("/validation/conclusion").asText()).isEqualTo("risk_identified");
        assertThat(validation.body().at("/execution/violations/0/code").asText()).isEqualTo("PAYMENT_DAYS_OVER_60");
        error(() -> export(workspace, proposal, 2, false), 422, "OFFER_VIOLATION");
        assertThat(repository.table("neg_export_bundles").getFirst().path("status").asText()).isEqualTo("failed");
    }

    @Test void recheckExpiresStaleExecutionsAndReturnsTheHeadState() {
        UUID workspace = reviewed(); UUID proposal = id(proposal(workspace), "proposal_id");
        patch(workspace, proposal, 1, "{\"offer\":{\"payment_days\":45}}");
        ObjectNode stale = object("{\"revision\":2,\"origin\":\"RECHECK\",\"status\":\"checking\"}")
                .put("id", UUID.randomUUID().toString()).put("proposal_id", proposal.toString()).put("lease_until", OffsetDateTime.now().minusSeconds(1).toString());
        repository.insert("neg_validations", stale);
        var checked = service.validate(owner, workspace, proposal, 2, object("{}"), null, UUID.randomUUID());
        assertThat(checked.body().at("/validation/status").asText()).isEqualTo("checked");
        assertThat(checked.body().at("/validation/checked_revision").intValue()).isEqualTo(2);
        assertThat(repository.table("neg_validations").stream().filter(row -> row.path("error_code").asText().equals("STALE_CHECK"))).hasSize(1);
    }

    @Test void corruptedDownloadFailsInsteadOfReturningModifiedBytes() {
        UUID workspace = reviewed(); UUID proposal = id(proposal(workspace), "proposal_id");
        var bundle = export(workspace, proposal, 1, false);
        UUID file = id(bundle.body().path("files").get(0), "file_id");
        assertThat(service.download(owner, file, UUID.randomUUID()).filename()).contains("제안서_");
        bytes.replaceAll((key, value) -> "corrupt".getBytes(StandardCharsets.UTF_8));
        error(() -> service.download(owner, file, UUID.randomUUID()), 500, "INTERNAL_ERROR");
    }

    @Test void idempotentReviewReturnsTheStoredResponseAndCallsAgentOnce() {
        UUID workspace = workspace(); UUID key = UUID.randomUUID();
        var first = service.review(owner, workspace, reviewInput(), key, UUID.randomUUID());
        var again = service.review(owner, workspace, reviewInput(), key, UUID.randomUUID());
        assertThat(again.status()).isEqualTo(first.status());
        // DB에서 다시 읽은 JSON은 숫자 노드 타입이 달라질 수 있어 직렬화 결과로 비교한다
        assertThat(again.body().toString()).isEqualTo(first.body().toString());
        verify(gateway, times(1)).review(any(), any());
        error(() -> service.review(owner, workspace, reviewInput().put("unit_price", 2100), key, UUID.randomUUID()), 409, "IDEMPOTENCY_CONFLICT");
    }

    @Test void failedReviewDoesNotStoreARevisionOrReserveTheIdempotencyKey() {
        UUID workspace = workspace(); UUID key = UUID.randomUUID();
        doThrow(ApiException.timeout(UUID.randomUUID())).when(gateway).review(any(), any());
        error(() -> service.review(owner, workspace, reviewInput(), key, UUID.randomUUID()), 504, "UPSTREAM_TIMEOUT");
        assertThat(repository.table("neg_review_revisions")).isEmpty();
        assertThat(repository.table("neg_requests")).isEmpty();
        doAnswer(call -> reviewResponse(call.getArgument(0), "ready")).when(gateway).review(any(), any());
        assertThat(service.review(owner, workspace, reviewInput(), key, UUID.randomUUID()).status()).isEqualTo(201);
    }

    @Test void changedReviewHeadDuringGenerationPreventsSavingTheAgentResult() {
        UUID workspace = reviewed();
        doAnswer(call -> {
            repository.table("neg_workspaces").getFirst().put("review_head_revision", 2);
            return proposalResponse(call.getArgument(0));
        }).when(gateway).propose(any(), any());
        error(() -> proposal(workspace), 409, "REVIEW_RECHECK_REQUIRED");
        assertThat(repository.table("neg_proposals")).isEmpty();
        assertThat(repository.table("neg_proposal_revisions")).isEmpty();
    }

    @Test void noOfferCannotBeViewedExternallyRecheckedOrExported() {
        UUID workspace = reviewed();
        doAnswer(call -> noOfferResponse(call.getArgument(0))).when(gateway).propose(any(), any());
        JsonNode created = proposal(workspace); UUID proposal = id(created, "proposal_id");
        assertThat(created.at("/available_export_formats/external").isEmpty()).isTrue();
        error(() -> service.external(owner, workspace, proposal, null, UUID.randomUUID()), 422, "NO_OFFER");
        error(() -> service.validate(owner, workspace, proposal, 1, object("{}"), null, UUID.randomUUID()), 422, "NO_OFFER");
        error(() -> export(workspace, proposal, 1, false), 422, "NO_OFFER");
        verify(gateway, never()).render(any(), any(), anyString());
    }

    UUID workspace() {
        return id(service.create(owner, object("{\"supplier_id\":\"SUP-0060\",\"rfq_id\":\"RFQ-2026-002\"}"), null, UUID.randomUUID()).body(), "negotiation_id");
    }
    UUID reviewed() {
        UUID workspace = workspace(); service.review(owner, workspace, reviewInput(), null, UUID.randomUUID()); return workspace;
    }
    JsonNode proposal(UUID workspace) {
        return service.propose(owner, workspace, object("{\"review_revision\":1,\"offer\":{\"target_price\":1750}}"), null, UUID.randomUUID()).body();
    }
    NegotiationRequests.Result patch(UUID workspace, UUID proposal, int revision, String changes) {
        return service.patchProposal(owner, workspace, proposal, object("{\"base_revision\":" + revision + ",\"changes\":" + changes + "}"), null, UUID.randomUUID());
    }
    NegotiationRequests.Result export(UUID workspace, UUID proposal, int revision, boolean last) {
        return service.export(owner, workspace, proposal, object("{\"revision\":" + revision + ",\"document_type\":\"external\",\"formats\":[\"pdf\"],\"final\":" + last + "}"), null, UUID.randomUUID());
    }
    static UUID id(JsonNode row, String field) { return UUID.fromString(row.path(field).asText()); }
    static void error(Runnable action, int status, String code) {
        assertThatThrownBy(action::run).isInstanceOfSatisfying(ApiException.class, failure -> {
            assertThat(failure.status.value()).isEqualTo(status); assertThat(failure.code).isEqualTo(code);
        });
    }
}
