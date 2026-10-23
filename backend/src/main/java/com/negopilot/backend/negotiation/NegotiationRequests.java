package com.negopilot.backend.negotiation;

import com.negopilot.backend.agent.AgentProperties;
import com.negopilot.backend.common.api.ApiException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import java.util.UUID;
import java.util.function.Supplier;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

@Component
class NegotiationRequests {
    record Result(int status, JsonNode body) {}
    record Claim(UUID user, UUID key, String lease, Result cached) {}
    private final NegotiationRepository repository;
    private final TransactionTemplate transaction;
    private final JsonMapper mapper;
    private final long leaseSeconds;
    private final ThreadLocal<Claim> current = new ThreadLocal<>();

    NegotiationRequests(NegotiationRepository repository, TransactionTemplate transaction, JsonMapper mapper, AgentProperties properties) {
        this.repository = repository; this.transaction = transaction; this.mapper = mapper;
        leaseSeconds = properties.responseTimeout().toSeconds() + 30;
    }

    Result run(UUID user, UUID key, String operation, String resource, ObjectNode body, UUID requestId, Supplier<Result> action) {
        if (key == null) return action.get();
        String hash = sha256((resource + "\n" + canonical(body)).getBytes(StandardCharsets.UTF_8));
        Claim claim = claim(user, key, operation, hash, requestId);
        if (claim.cached() != null) return claim.cached();
        current.set(claim);
        try { return action.get(); }
        catch (RuntimeException failure) {
            try { release(claim); }
            catch (RuntimeException cleanup) { failure.addSuppressed(cleanup); }
            throw failure;
        }
        finally { current.remove(); }
    }

    // 서비스의 저장 트랜잭션 안에서 호출해 응답과 변경을 함께 확정한다.
    Result complete(int status, JsonNode body, UUID requestId) {
        Claim claim = current.get();
        if (claim != null) {
            int changed = repository.execute("UPDATE neg_requests SET state='DONE', lease_until=NULL, response_status=?, response=?::jsonb "
                    + "WHERE user_id=? AND idempotency_key=? AND state='IN_PROGRESS' AND lease_until=?::timestamptz AND lease_until>clock_timestamp()",
                    status, mapper.writeValueAsString(body), claim.user(), claim.key(), claim.lease());
            if (changed != 1) throw busy(requestId);
        }
        return new Result(status, body);
    }

    Claim claim(UUID user, UUID key, String operation, String hash, UUID requestId) {
        return transaction.execute(tx -> {
            int inserted = repository.execute("INSERT INTO neg_requests(user_id,idempotency_key,operation,body_hash,state,lease_until) "
                    + "VALUES(?,?,?,?,'IN_PROGRESS',clock_timestamp()+(? * interval '1 second')) ON CONFLICT DO NOTHING",
                    user, key, operation, hash, leaseSeconds);
            ObjectNode row = repository.one("SELECT to_jsonb(r) || jsonb_build_object('lease_active',lease_until>clock_timestamp()) FROM neg_requests r "
                    + "WHERE user_id=? AND idempotency_key=? FOR UPDATE", user, key);
            if (!operation.equals(row.path("operation").asText()) || !hash.equals(row.path("body_hash").asText())) {
                throw ApiException.of(requestId, 409, "IDEMPOTENCY_CONFLICT", "다른 요청에 사용된 멱등 키입니다", false);
            }
            if ("DONE".equals(row.path("state").asText())) {
                return new Claim(user, key, null, new Result(row.path("response_status").intValue(), row.get("response")));
            }
            if (inserted == 0) {
                if (row.path("lease_active").asBoolean()) throw busy(requestId);
                repository.execute("UPDATE neg_requests SET lease_until=clock_timestamp()+(? * interval '1 second') WHERE user_id=? AND idempotency_key=?", leaseSeconds, user, key);
                row = repository.one("SELECT to_jsonb(r) FROM neg_requests r WHERE user_id=? AND idempotency_key=?", user, key);
            }
            return new Claim(user, key, row.path("lease_until").asText(), null);
        });
    }

    void assertLease(Claim claim, UUID requestId) {
        ObjectNode row = repository.one("SELECT to_jsonb(r) FROM neg_requests r WHERE user_id=? AND idempotency_key=? "
                + "AND state='IN_PROGRESS' AND lease_until=?::timestamptz AND lease_until>clock_timestamp() FOR UPDATE", claim.user(), claim.key(), claim.lease());
        if (row == null) throw busy(requestId);
    }

    void release(Claim claim) {
        repository.execute("DELETE FROM neg_requests WHERE user_id=? AND idempotency_key=? AND state='IN_PROGRESS' AND lease_until=?::timestamptz",
                claim.user(), claim.key(), claim.lease());
    }

    private String canonical(JsonNode value) {
        if (value.isObject()) {
            ObjectNode sorted = mapper.createObjectNode();
            value.propertyNames().stream().sorted().forEach(key -> sorted.set(key, mapper.readTree(canonical(value.get(key)))));
            return mapper.writeValueAsString(sorted);
        }
        if (value.isArray()) {
            var array = mapper.createArrayNode();
            value.forEach(child -> array.add(mapper.readTree(canonical(child))));
            return mapper.writeValueAsString(array);
        }
        return mapper.writeValueAsString(value);
    }

    static String sha256(byte[] bytes) {
        try { return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes)); }
        catch (NoSuchAlgorithmException failure) { throw new IllegalStateException(failure); }
    }
    private ApiException busy(UUID id) { return ApiException.of(id, 409, "IDEMPOTENCY_CONFLICT", "요청을 처리 중입니다", true); }
}
