package com.negopilot.backend.legalreview;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

import com.negopilot.backend.agent.AgentProperties;
import com.negopilot.backend.common.api.ApiException;
import com.negopilot.backend.negotiation.NegotiationReviews;
import java.net.URI;
import java.time.Duration;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.TransactionStatus;
import org.springframework.transaction.support.SimpleTransactionStatus;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

class LegalReviewUploadServiceTest {
    static final JsonMapper JSON = JsonMapper.builder().build();
    final UUID owner = UUID.randomUUID();
    MemoryUploadStore store;
    NegotiationReviews negotiations;
    List<Runnable> pending;
    LegalReviewUploadService service;

    @BeforeEach void setup() {
        store = new MemoryUploadStore(JSON);
        negotiations = mock(NegotiationReviews.class);
        pending = new ArrayList<>();
        var agent = new AgentProperties(URI.create("http://127.0.0.1:9200"), "", Duration.ofSeconds(2), Duration.ofSeconds(35));
        var runner = new LegalReviewRunner(store, negotiations, agent, pending::add);   // 테스트가 실행 시점을 정한다
        var transactions = new TransactionTemplate(new PlatformTransactionManager() {
            public TransactionStatus getTransaction(TransactionDefinition definition) { return new SimpleTransactionStatus(); }
            public void commit(TransactionStatus status) { }
            public void rollback(TransactionStatus status) { }
        });
        service = new LegalReviewUploadService(store, runner, transactions, JSON);
        when(negotiations.create(any(), any(), any(), any(), any(), any())).thenAnswer(inv -> UUID.randomUUID());
    }

    /** /internal/legal-reviews 공개 응답 (review_token은 협상 작업 저장 때 빠진다). */
    static ObjectNode agentResponse(String execution, String assessment) {
        return (ObjectNode) JSON.readTree("""
                {"execution_status":"%s","error":null,
                 "answer":{"text":"검토","citations":[],"notices":[],"evidence":[
                   {"id":"e1","source":"business","kind":"company_size","label":"기업 규모 검토: supplier",
                    "text":"{\\"status\\":\\"criteria_met\\",\\"is_sme\\":true,\\"is_small_business\\":false,\\"missing\\":[]}",
                    "meta":{"role":"supplier"}},
                   {"id":"e2","source":"business","kind":"company_size","label":"기업 규모 검토: buyer",
                    "text":"{\\"status\\":\\"unknown\\",\\"is_sme\\":null,\\"is_small_business\\":null,\\"missing\\":[\\"independence_met\\"]}",
                    "meta":{"role":"buyer"}}]},
                 "routing":{"outcomes":[{"id":"q1","calls":[{"tool":"check_offer","result":{"verdict_code":"CHECK_NEEDED"}}]}]},
                 "legal_review":{"review_id":"rv-1","version":"1","input_hash":"%s",
                   "assessment":{"status":"%s","findings":[],"missing_fields":["transaction_type"],"constraints":[],"sources_complete":true}}}
                """.formatted(execution, "a".repeat(64), assessment));
    }

    /** NegotiationService.review처럼 작업에 검토 버전을 쌓고 201 검토 응답을 돌려준다. */
    private void reviewsSucceed(String assessment) {
        when(negotiations.review(any(), any(), any(), any(), any())).thenAnswer(inv -> saveReview(inv.getArgument(1), assessment));
    }

    private NegotiationReviews.Outcome saveReview(UUID workspace, String assessment) {
        int revision = store.heads.merge(workspace, 1, Integer::sum);
        ObjectNode response = agentResponse("COMPLETED", assessment);
        ObjectNode saved = JSON.createObjectNode().put("execution_status", "COMPLETED").put("assessment_status", assessment)
                .put("eligibility", assessment.equals("needs_information") ? "conditional" : assessment);
        saved.set("response", response);
        store.reviews.put(workspace + "|" + revision, saved);
        return new NegotiationReviews.Outcome(201, response.deepCopy().put("review_revision", revision));
    }

    @SafeVarargs
    private byte[] workbook(List<Object>... rows) {
        List<List<Object>> sheet = new ArrayList<>();
        sheet.add(ReviewWorkbookParserTest.headers());
        sheet.addAll(List.of(rows));
        return TestXlsx.of(sheet);
    }

    private static List<Object> row(String supplier) {
        List<Object> row = new ArrayList<>(ReviewWorkbookParserTest.validRow());
        row.set(0, supplier);
        return row;
    }

    private void runPending() {
        List<Runnable> now = new ArrayList<>(pending);
        pending.clear();
        now.forEach(Runnable::run);
    }

    private UUID uploadId(ObjectNode view) { return UUID.fromString(view.get("upload_id").stringValue()); }

    @Test void eachValidRowGetsNewNegotiationAndSavedReview() {
        List<Object> bad = row("SUP-0034");
        bad.set(4, "육십일");
        reviewsSucceed("needs_information");

        ObjectNode accepted = service.upload(owner, "조건.xlsx", workbook(row("SUP-0034"), bad, row("SUP-0034")), UUID.randomUUID());
        assertThat(accepted.get("status").stringValue()).isEqualTo("PROCESSING");
        assertThat(accepted.get("counts").get("queued").intValue()).isEqualTo(2);
        assertThat(accepted.get("counts").get("invalid").intValue()).isEqualTo(1);
        assertThat(accepted.get("rows").get(1).get("input_errors").get(0).get("column").stringValue()).isEqualTo("지급기한");

        runPending();
        UUID id = uploadId(accepted);
        ObjectNode done = service.get(owner, id, UUID.randomUUID());
        assertThat(done.get("status").stringValue()).isEqualTo("DONE");
        JsonNode first = done.get("rows").get(0);
        JsonNode third = done.get("rows").get(2);
        assertThat(first.get("status").stringValue()).isEqualTo("COMPLETED");
        // 같은 공급자 + RFQ여도 행마다 새 협상 작업이다
        assertThat(first.get("negotiation_id").stringValue()).isNotEqualTo(third.get("negotiation_id").stringValue());
        assertThat(first.get("review_revision").intValue()).isEqualTo(1);
        assertThat(first.get("review_head_revision").intValue()).isEqualTo(1);
        assertThat(first.get("eligibility").stringValue()).isEqualTo("conditional");
        assertThat(first.get("assessment_status").stringValue()).isEqualTo("needs_information");
        assertThat(first.get("check_verdict_code").stringValue()).isEqualTo("CHECK_NEEDED");
        assertThat(first.get("check_error_code").isNull()).isTrue();
        assertThat(first.get("missing_fields").get(0).stringValue()).isEqualTo("transaction_type");
        // 위반 소지, 규모 판정 보류, 실행 실패가 다른 필드다
        assertThat(first.get("company_size").get("supplier").get("is_sme").booleanValue()).isTrue();
        assertThat(first.get("company_size").get("buyer").get("is_sme").isNull()).isTrue();
        assertThat(first.get("error").isNull()).isTrue();
        assertThat(done.get("rows").get(1).get("negotiation_id").isNull()).isTrue();

        verify(negotiations).create(eq(owner), eq("SUP-0034"), eq("RFQ-2026-001"), eq("조건.xlsx 2행"),
                eq(LegalReviewRunner.source(new LegalReviewUploadStore.Key(id, 2))), any());
        verify(negotiations).create(eq(owner), eq("SUP-0034"), eq("RFQ-2026-001"), eq("조건.xlsx 4행"),
                eq(LegalReviewRunner.source(new LegalReviewUploadStore.Key(id, 4))), any());
        ArgumentCaptor<ObjectNode> offer = ArgumentCaptor.forClass(ObjectNode.class);
        verify(negotiations, times(2)).review(eq(owner), any(), offer.capture(), any(), any());
        // 엑셀에서 읽은 조건을 그대로 보낸다
        assertThat(offer.getAllValues().getFirst()).isEqualTo(store.rows.get(new LegalReviewUploadStore.Key(id, 2)).get("offer"));
        assertThat(offer.getAllValues().getFirst().get("transaction").get("transaction_type").stringValue()).isEqualTo("manufacturing");

        ObjectNode detail = service.row(owner, id, 2, UUID.randomUUID());
        assertThat(detail.get("source_cells").get("공급자 ID").stringValue()).isEqualTo("SUP-0034");
        assertThat(detail.get("response").get("legal_review").get("assessment").get("status").stringValue()).isEqualTo("needs_information");
    }

    @Test void failuresStayOnTheirRowAndRetryReusesTheWorkspace() {
        when(negotiations.create(any(), eq("SUP-0070"), any(), any(), any(), any()))
                .thenThrow(ApiException.of(UUID.randomUUID(), 422, "SUPPLIER_NOT_FOUND", "x", false));
        UUID[] failedWorkspace = new UUID[1];
        when(negotiations.review(any(), any(), any(), any(), any()))
                .thenThrow(ApiException.timeout(UUID.randomUUID()))
                .thenAnswer(inv -> {
                    failedWorkspace[0] = inv.getArgument(1);
                    ObjectNode body = agentResponse("FAILED", "ready");
                    body.putObject("error").put("code", "GENERATION_FAILED");
                    return new NegotiationReviews.Outcome(200, body);
                })
                .thenAnswer(inv -> saveReview(inv.getArgument(1), "ready"));
        UUID id = uploadId(service.upload(owner, "조건.xlsx",
                workbook(row("SUP-0034"), row("SUP-0060"), row("SUP-0070"), row("SUP-0080")), UUID.randomUUID()));
        runPending();

        JsonNode rows = service.get(owner, id, UUID.randomUUID()).get("rows");
        assertThat(rows.get(0).get("status").stringValue()).isEqualTo("FAILED");
        assertThat(rows.get(0).get("error").get("code").stringValue()).isEqualTo("UPSTREAM_TIMEOUT");
        assertThat(rows.get(0).get("error").get("retryable").booleanValue()).isTrue();
        assertThat(rows.get(1).get("status").stringValue()).isEqualTo("FAILED");
        assertThat(rows.get(1).get("execution_status").stringValue()).isEqualTo("FAILED");
        assertThat(rows.get(1).get("error").get("code").stringValue()).isEqualTo("GENERATION_FAILED");
        assertThat(rows.get(1).get("negotiation_id").stringValue()).isEqualTo(failedWorkspace[0].toString());
        assertThat(rows.get(2).get("error").get("code").stringValue()).isEqualTo("SUPPLIER_NOT_FOUND");
        assertThat(rows.get(2).get("error").get("retryable").booleanValue()).isFalse();
        assertThat(rows.get(2).get("negotiation_id").isNull()).isTrue();
        assertThat(rows.get(3).get("status").stringValue()).isEqualTo("COMPLETED");

        // 실패한 행을 다시 시도하면 이미 만든 협상 작업에 검토를 저장한다
        reviewsSucceed("ready");
        ObjectNode retried = service.retry(owner, id, 3, UUID.randomUUID());
        assertThat(retried.get("status").stringValue()).isEqualTo("QUEUED");
        runPending();
        JsonNode row = service.row(owner, id, 3, UUID.randomUUID());
        assertThat(row.get("status").stringValue()).isEqualTo("COMPLETED");
        assertThat(row.get("negotiation_id").stringValue()).isEqualTo(failedWorkspace[0].toString());
        assertThat(row.get("attempts").intValue()).isEqualTo(2);
        verify(negotiations, times(4)).create(any(), any(), any(), any(), any(), any());
        // 실행마다 request_id는 달라도 검토 저장은 같은 행 기준(source)으로 요청한다
        verify(negotiations, times(2)).review(any(), eq(failedWorkspace[0]), any(), eq(LegalReviewRunner.source(new LegalReviewUploadStore.Key(id, 3))), any());
    }

    @Test void rejectedCheckOfferInputIsShownSeparately() {
        ObjectNode row = (ObjectNode) JSON.readTree("""
                {"row_number":2,"status":"COMPLETED","review":{"execution_status":"COMPLETED","eligibility":"ready",
                 "response":{"routing":{"outcomes":[{"calls":[{"tool":"check_offer","result":null,"error":"Tool execution failed.",
                 "error_code":"RFQ_NOT_FOUND"}]}]}}}}
                """);
        ObjectNode view = LegalReviewRowView.summary(row, JSON);
        assertThat(view.get("check_error_code").stringValue()).isEqualTo("RFQ_NOT_FOUND");
        assertThat(view.get("check_verdict_code").isNull()).isTrue();
        assertThat(view.get("company_size").isNull()).isTrue();
    }

    @Test void retryRules() {
        reviewsSucceed("ready");
        List<Object> bad = row("SUP-0034");
        bad.set(6, null);
        UUID id = uploadId(service.upload(owner, "조건.xlsx", workbook(row("SUP-0034"), bad), UUID.randomUUID()));
        assertError(() -> service.retry(owner, id, 2, UUID.randomUUID()), 409, "REVIEW_IN_PROGRESS");
        runPending();
        assertError(() -> service.retry(owner, id, 2, UUID.randomUUID()), 409, "ROW_ALREADY_REVIEWED");
        assertError(() -> service.retry(owner, id, 3, UUID.randomUUID()), 422, "ROW_INVALID");
        assertError(() -> service.retry(owner, id, 9, UUID.randomUUID()), 404, "RESOURCE_NOT_FOUND");

        UUID stranger = UUID.randomUUID();
        assertError(() -> service.get(stranger, id, UUID.randomUUID()), 404, "RESOURCE_NOT_FOUND");
        assertError(() -> service.row(stranger, id, 2, UUID.randomUUID()), 404, "RESOURCE_NOT_FOUND");
        assertError(() -> service.retry(stranger, id, 2, UUID.randomUUID()), 404, "RESOURCE_NOT_FOUND");
        assertThat(service.list(stranger, 20).get("items")).isEmpty();
        assertThat(service.list(owner, 20).get("items")).hasSize(1);
    }

    @Test void expiredRunningRowsBecomeRetryableFailures() {
        UUID id = uploadId(service.upload(owner, "조건.xlsx", workbook(row("SUP-0034")), UUID.randomUUID()));
        var key = new LegalReviewUploadStore.Key(id, 2);
        store.claim(key, OffsetDateTime.now().minusSeconds(1), UUID.randomUUID());
        JsonNode row = service.get(owner, id, UUID.randomUUID()).get("rows").get(0);
        assertThat(row.get("status").stringValue()).isEqualTo("FAILED");
        assertThat(row.get("error").get("code").stringValue()).isEqualTo("STALE_REVIEW");
        // 대기열의 실행은 이미 정리된 행을 다시 가져가지 않는다
        runPending();
        verifyNoInteractions(negotiations);
    }

    @Test void restartRecoveryLeavesRowsAnotherServerIsStillRunning() {
        UUID id = uploadId(service.upload(owner, "조건.xlsx", workbook(row("SUP-0034"), row("SUP-0060")), UUID.randomUUID()));
        pending.clear();
        store.claim(new LegalReviewUploadStore.Key(id, 2), OffsetDateTime.now().plusMinutes(5), UUID.randomUUID());
        store.claim(new LegalReviewUploadStore.Key(id, 3), OffsetDateTime.now().minusSeconds(1), UUID.randomUUID());
        new LegalReviewRunner(store, negotiations, new AgentProperties(URI.create("http://127.0.0.1:9200"), "", Duration.ofSeconds(2), Duration.ofSeconds(35)), pending::add).recover();
        JsonNode rows = service.get(owner, id, UUID.randomUUID()).get("rows");
        // 임대가 남은 행은 다른 서버가 처리 중일 수 있어 그대로 두고, 임대가 끝난 행만 다시 시도할 수 있는 실패로 바꾼다
        assertThat(rows.get(0).get("status").stringValue()).isEqualTo("RUNNING");
        assertThat(rows.get(1).get("status").stringValue()).isEqualTo("FAILED");
        assertThat(rows.get(1).get("error").get("code").stringValue()).isEqualTo("STALE_REVIEW");
    }

    private static void assertError(Runnable action, int status, String code) {
        assertThatThrownBy(action::run).isInstanceOfSatisfying(ApiException.class, e -> {
            assertThat(e.status.value()).isEqualTo(status);
            assertThat(e.code).isEqualTo(code);
        });
    }
}
