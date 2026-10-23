package com.negopilot.backend.negotiation;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.negopilot.backend.agent.AgentProperties;
import com.negopilot.backend.common.api.ApiException;
import java.net.URI;
import java.time.Duration;
import java.time.OffsetDateTime;
import java.util.HashMap;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.transaction.support.SimpleTransactionStatus;
import org.springframework.transaction.support.TransactionCallback;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

class NegotiationRequestsTest {
    private static final UUID USER = UUID.randomUUID();
    private static final UUID KEY = UUID.randomUUID();
    private static final UUID REQUEST_ID = UUID.randomUUID();
    private final JsonMapper mapper = JsonMapper.builder().build();
    private RequestRows repository;
    private NegotiationRequests requests;
    private final AtomicInteger transactions = new AtomicInteger();

    @BeforeEach
    void setup() {
        repository = new RequestRows(mapper);
        TransactionTemplate transaction = new TransactionTemplate() {
            @Override public <T> T execute(TransactionCallback<T> action) {
                transactions.incrementAndGet();
                return action.doInTransaction(new SimpleTransactionStatus());
            }
        };
        requests = new NegotiationRequests(repository, transaction, mapper,
                new AgentProperties(URI.create("http://agent.test"), "key", Duration.ofSeconds(2), Duration.ofSeconds(120)));
    }

    @Test
    void sameKeyAndCanonicalBodyReturnSavedStatusWithoutExecutingAgain() {
        AtomicInteger calls = new AtomicInteger();
        ObjectNode first = json("{\"offer\":{\"notes\":null,\"target_price\":1750},\"review_revision\":1}");
        ObjectNode reordered = json("{\"review_revision\":1,\"offer\":{\"target_price\":1750,\"notes\":null}}");
        var saved = requests.run(USER, KEY, "PROPOSE", "workspace", first, REQUEST_ID, () -> {
            calls.incrementAndGet();
            return requests.complete(201, json("{\"proposal_id\":\"saved\"}"), REQUEST_ID);
        });
        var reused = requests.run(USER, KEY, "PROPOSE", "workspace", reordered, UUID.randomUUID(), () -> {
            calls.incrementAndGet();
            throw new AssertionError("저장 응답을 재사용해야 합니다");
        });
        assertThat(calls.get()).isEqualTo(1);
        assertThat(reused).isEqualTo(saved);
        assertThat(reused.status()).isEqualTo(201);
        assertThat(repository.row(USER, KEY).path("state").stringValue()).isEqualTo("DONE");
    }

    @Test
    void differentBodyOperationAndResourceCannotReuseCompletedKey() {
        ObjectNode body = json("{\"title\":\"첫 작업\"}");
        requests.run(USER, KEY, "CREATE", "workspace", body, REQUEST_ID,
                () -> requests.complete(201, body, REQUEST_ID));
        assertConflict(() -> requests.run(USER, KEY, "CREATE", "workspace", json("{\"title\":\"다른 작업\"}"),
                REQUEST_ID, () -> new NegotiationRequests.Result(200, body)), false);
        assertConflict(() -> requests.run(USER, KEY, "REVIEW", "workspace", body, REQUEST_ID,
                () -> new NegotiationRequests.Result(200, body)), false);
        assertConflict(() -> requests.run(USER, KEY, "CREATE", "another-workspace", body, REQUEST_ID,
                () -> new NegotiationRequests.Result(200, body)), false);
    }

    @Test
    void activeLeaseConflictsAndExpiredLeaseCanBeReclaimed() {
        var first = requests.claim(USER, KEY, "REVIEW", "same-hash", REQUEST_ID);
        assertThat(repository.lastLeaseSeconds).isEqualTo(150);
        assertConflict(() -> requests.claim(USER, KEY, "REVIEW", "same-hash", REQUEST_ID), true);
        repository.now = repository.now.plusSeconds(151);
        var reclaimed = requests.claim(USER, KEY, "REVIEW", "same-hash", REQUEST_ID);
        assertThat(reclaimed.lease()).isNotEqualTo(first.lease());
        requests.assertLease(reclaimed, REQUEST_ID);
        assertConflict(() -> requests.assertLease(first, REQUEST_ID), true);
        requests.release(first);
        assertThat(repository.row(USER, KEY)).isNotNull();
        requests.release(reclaimed);
        assertThat(repository.row(USER, KEY)).isNull();
    }

    @Test
    void failedActionDeletesOnlyItsOwnClaimAndAllowsRetry() {
        ObjectNode body = json("{}");
        assertThatThrownBy(() -> requests.run(USER, KEY, "REVIEW", "workspace", body, REQUEST_ID, () -> {
            throw ApiException.unavailable(REQUEST_ID);
        })).isInstanceOfSatisfying(ApiException.class, ex -> assertThat(ex.code).isEqualTo("UPSTREAM_UNAVAILABLE"));
        assertThat(repository.row(USER, KEY)).isNull();
        var retried = requests.run(USER, KEY, "REVIEW", "workspace", body, REQUEST_ID,
                () -> requests.complete(201, json("{\"review_revision\":1}"), REQUEST_ID));
        assertThat(retried.status()).isEqualTo(201);
        assertThat(repository.row(USER, KEY).path("state").stringValue()).isEqualTo("DONE");
    }

    @Test
    void expiredExecutionCannotCompleteOrDeleteNewOwnersClaim() {
        assertConflict(() -> requests.run(USER, KEY, "REVIEW", "workspace", json("{}"), REQUEST_ID, () -> {
            ObjectNode row = repository.row(USER, KEY);
            String hash = row.path("body_hash").stringValue();
            repository.now = repository.now.plusSeconds(151);
            requests.claim(USER, KEY, "REVIEW", hash, UUID.randomUUID());
            return requests.complete(201, json("{\"stale\":true}"), REQUEST_ID);
        }), true);
        ObjectNode row = repository.row(USER, KEY);
        assertThat(row).isNotNull();
        assertThat(row.path("state").stringValue()).isEqualTo("IN_PROGRESS");
        assertThat(row.has("response")).isFalse();
    }

    @Test
    void expiredLeaseCannotCompleteBeforeAnotherRequestClaimsIt() {
        assertConflict(() -> requests.run(USER, KEY, "REVIEW", "workspace", json("{}"), REQUEST_ID, () -> {
            repository.now = repository.now.plusSeconds(151);
            return requests.complete(201, json("{}"), REQUEST_ID);
        }), true);
        assertThat(repository.row(USER, KEY)).isNull();
    }

    @Test
    void requestsWithoutKeysDoNotStoreClaimsAndClaimContextIsClearedAfterFailure() {
        var response = requests.run(USER, null, "CREATE", "workspace", json("{}"), REQUEST_ID,
                () -> requests.complete(201, json("{\"ok\":true}"), REQUEST_ID));
        assertThat(response.status()).isEqualTo(201);
        assertThat(transactions.get()).isZero();
        assertThat(repository.rows).isEmpty();
        assertThatThrownBy(() -> requests.run(USER, KEY, "CREATE", "workspace", json("{}"), REQUEST_ID, () -> {
            throw new IllegalStateException("실패");
        })).isInstanceOf(IllegalStateException.class);
        assertThat(requests.complete(200, json("{}"), REQUEST_ID).status()).isEqualTo(200);
    }

    @Test
    void sameKeyIsIsolatedByUser() {
        ObjectNode body = json("{}");
        requests.run(USER, KEY, "CREATE", "workspace", body, REQUEST_ID,
                () -> requests.complete(201, json("{\"owner\":1}"), REQUEST_ID));
        var other = requests.run(UUID.randomUUID(), KEY, "CREATE", "workspace", body, REQUEST_ID,
                () -> requests.complete(201, json("{\"owner\":2}"), REQUEST_ID));
        assertThat(other.body().path("owner").intValue()).isEqualTo(2);
        assertThat(repository.rows).hasSize(2);
    }

    private ObjectNode json(String value) { return (ObjectNode) mapper.readTree(value); }

    private void assertConflict(Runnable action, boolean retryable) {
        assertThatThrownBy(action::run).isInstanceOfSatisfying(ApiException.class, ex -> {
            assertThat(ex.status.value()).isEqualTo(409);
            assertThat(ex.code).isEqualTo("IDEMPOTENCY_CONFLICT");
            assertThat(ex.retryable).isEqualTo(retryable);
        });
    }

    /** DB 없이 멱등 실행의 상태와 임대 소유권을 확인한다. */
    private static final class RequestRows extends NegotiationRepository {
        private final JsonMapper mapper;
        private final Map<String, ObjectNode> rows = new HashMap<>();
        private OffsetDateTime now = OffsetDateTime.parse("2026-10-23T12:00:00+09:00");
        private long lastLeaseSeconds;

        RequestRows(JsonMapper mapper) { super(null, mapper); this.mapper = mapper; }

        ObjectNode row(Object user, Object key) { return rows.get(user + ":" + key); }

        @Override ObjectNode one(String sql, Object... args) {
            ObjectNode row = row(args[0], args[1]);
            if (row == null) return null;
            if (args.length > 2 && !owns(row, args[2], true)) return null;
            ObjectNode copy = row.deepCopy();
            copy.put("lease_active", active(row));
            return copy;
        }

        @Override int execute(String sql, Object... args) {
            if (sql.startsWith("INSERT INTO neg_requests")) {
                String key = args[0] + ":" + args[1];
                if (rows.containsKey(key)) return 0;
                lastLeaseSeconds = ((Number) args[4]).longValue();
                rows.put(key, mapper.createObjectNode().put("operation", (String) args[2]).put("body_hash", (String) args[3])
                        .put("state", "IN_PROGRESS").put("lease_until", now.plusSeconds(lastLeaseSeconds).toString()));
                return 1;
            }
            if (sql.startsWith("UPDATE neg_requests SET lease_until")) {
                ObjectNode row = row(args[1], args[2]);
                row.put("lease_until", now.plusSeconds(((Number) args[0]).longValue()).toString());
                return 1;
            }
            if (sql.startsWith("UPDATE neg_requests SET state='DONE'")) {
                ObjectNode row = row(args[2], args[3]);
                assertThat(sql).contains("lease_until=?::timestamptz", "lease_until>clock_timestamp()");
                if (row == null || !owns(row, args[4], true)) return 0;
                row.put("state", "DONE").putNull("lease_until").put("response_status", ((Number) args[0]).intValue());
                row.set("response", mapper.readTree((String) args[1]));
                return 1;
            }
            if (sql.startsWith("DELETE FROM neg_requests")) {
                ObjectNode row = row(args[0], args[1]);
                assertThat(sql).contains("state='IN_PROGRESS'", "lease_until=?::timestamptz");
                if (row == null || !owns(row, args[2], false)) return 0;
                rows.remove(args[0] + ":" + args[1]);
                return 1;
            }
            throw new AssertionError("지원하지 않는 테스트 SQL: " + sql);
        }

        private boolean active(ObjectNode row) {
            return row.path("lease_until").isString()
                    && OffsetDateTime.parse(row.path("lease_until").stringValue()).isAfter(now);
        }

        private boolean owns(ObjectNode row, Object lease, boolean checkActive) {
            return "IN_PROGRESS".equals(row.path("state").stringValue())
                    && row.path("lease_until").stringValue().equals(lease) && (!checkActive || active(row));
        }
    }
}
