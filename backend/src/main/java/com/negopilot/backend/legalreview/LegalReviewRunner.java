package com.negopilot.backend.legalreview;

import com.negopilot.backend.agent.AgentProperties;
import com.negopilot.backend.common.api.ApiException;
import com.negopilot.backend.negotiation.NegotiationReviews;
import jakarta.annotation.PreDestroy;
import java.time.Duration;
import java.time.OffsetDateTime;
import java.util.UUID;
import java.util.concurrent.Executor;
import java.util.concurrent.RejectedExecutionException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.scheduling.concurrent.ThreadPoolTaskExecutor;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.JsonNodeFactory;
import tools.jackson.databind.node.ObjectNode;

/**
 * QUEUED 행마다 협상 작업을 새로 만들고 그 작업에 법규 검토를 저장한다(POST /api/negotiations → …/reviews와 같은 규칙).
 * 한 행이 실패해도 다른 행은 계속 처리한다. 서버가 다시 뜨면 실행 중이던 행은 다시 시도할 수 있는 실패로 바꾸고 대기 행은 다시 넣는다.
 */
@Component
class LegalReviewRunner {
    private static final Logger log = LoggerFactory.getLogger(LegalReviewRunner.class);
    private final LegalReviewUploadStore store;
    private final NegotiationReviews negotiations;
    private final Duration lease;
    private final Executor executor;
    private final ThreadPoolTaskExecutor owned;

    @Autowired
    LegalReviewRunner(LegalReviewUploadStore store, NegotiationReviews negotiations, AgentProperties agent,
                      @Value("${legal-review-upload.concurrency:2}") int concurrency) {
        this(store, negotiations, agent, pool(concurrency));
    }

    LegalReviewRunner(LegalReviewUploadStore store, NegotiationReviews negotiations, AgentProperties agent, Executor executor) {
        this.store = store;
        this.negotiations = negotiations;
        // 작업 생성 + Agent 응답 제한이 지나도 결과가 없으면 그 실행은 끝난 것으로 본다
        this.lease = agent.responseTimeout().plusSeconds(60);
        this.executor = executor;
        this.owned = executor instanceof ThreadPoolTaskExecutor pool ? pool : null;
    }

    private static ThreadPoolTaskExecutor pool(int concurrency) {
        ThreadPoolTaskExecutor pool = new ThreadPoolTaskExecutor();
        pool.setCorePoolSize(Math.max(1, concurrency));
        pool.setMaxPoolSize(Math.max(1, concurrency));
        pool.setQueueCapacity(1000);
        pool.setThreadNamePrefix("legal-review-");
        pool.initialize();
        return pool;
    }

    void submit(LegalReviewUploadStore.Key key) {
        try { executor.execute(() -> run(key)); }
        catch (RejectedExecutionException e) { store.reject(key); }
    }

    void run(LegalReviewUploadStore.Key key) {
        UUID requestId = UUID.randomUUID();
        if (!store.claim(key, OffsetDateTime.now().plus(lease), requestId)) return;
        ObjectNode result = JsonNodeFactory.instance.objectNode();
        try {
            ObjectNode row = store.row(key);
            UUID owner = UUID.fromString(row.get("created_by").stringValue());
            UUID workspace = row.path("negotiation_id").isString() ? UUID.fromString(row.get("negotiation_id").stringValue()) : null;
            if (workspace == null) {
                workspace = negotiations.create(owner, row.get("supplier_id").stringValue(), row.get("rfq_id").stringValue(),
                        title(row), requestId);
                store.attachWorkspace(key, workspace);
            }
            NegotiationReviews.Outcome outcome = negotiations.review(owner, workspace, (ObjectNode) row.get("offer"), requestId);
            JsonNode body = outcome.body();
            if (outcome.status() == 201) {
                result.put("status", "COMPLETED").put("review_revision", body.get("review_revision").intValue())
                        .put("execution_status", body.path("execution_status").asString(null));
            } else {
                // Agent 실행 실패는 협상 작업에 저장되지 않는다. 다시 시도할 수 있게 행에 실패로 남긴다
                String code = body.path("error").path("code").asString("EXECUTION_FAILED");
                result.put("status", "FAILED").put("execution_status", "FAILED")
                        .put("error_code", code.length() <= 40 ? code : "EXECUTION_FAILED").put("error_retryable", true);
            }
        } catch (ApiException e) {
            result.put("status", "FAILED").put("error_code", e.code).put("error_retryable", e.retryable);
        } catch (RuntimeException e) {
            log.warn("법규 검토 행 처리 실패: {} {}행 ({})", key.uploadId(), key.rowNumber(), e.getClass().getSimpleName());
            result.put("status", "FAILED").put("error_code", "INTERNAL_ERROR").put("error_retryable", true);
        }
        if (!store.finish(key, result)) log.info("임대가 끝난 법규 검토 결과는 행에 쓰지 않음: {} {}행", key.uploadId(), key.rowNumber());
    }

    /** 작업 목록에서 업로드 행을 알아볼 수 있게 "파일 이름 N행"을 붙인다. */
    private static String title(ObjectNode row) {
        return row.path("file_name").asString("엑셀") + " " + row.get("row_number").intValue() + "행";
    }

    @EventListener(ApplicationReadyEvent.class)
    void recover() {
        int stale = store.expireRunning(true);
        var queued = store.queued();
        if (stale > 0 || !queued.isEmpty()) log.info("법규 검토 재시작 정리: 실행 중 {}건 실패 처리, 대기 {}건 다시 넣음", stale, queued.size());
        queued.forEach(this::submit);
    }

    @PreDestroy
    void stop() { if (owned != null) owned.shutdown(); }
}
