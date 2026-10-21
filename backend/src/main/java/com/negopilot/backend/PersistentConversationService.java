package com.negopilot.backend;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Base64;
import java.util.Collections;
import java.util.HexFormat;
import java.util.List;
import java.util.Set;
import java.util.UUID;
import org.springframework.context.annotation.Profile;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

@Profile("!legacy-test")
@Service
final class PersistentConversationService {
    private static final String OPTIONS_SEPARATOR = "\n\n선택지 데이터:\n";
    private final JdbcTemplate jdbc;
    private final TransactionTemplate transaction;
    private final GenerationGateway gateway;
    private final JsonMapper mapper;
    private final long leaseSeconds;

    PersistentConversationService(JdbcTemplate jdbc, TransactionTemplate transaction, GenerationGateway gateway,
                                  JsonMapper mapper, AgentProperties properties) {
        this.jdbc = jdbc;
        this.transaction = transaction;
        this.gateway = gateway;
        this.mapper = mapper;
        this.leaseSeconds = Math.addExact(properties.responseTimeout().toSeconds(), 30);
    }

    JsonNode create(UUID owner, ObjectNode context, UUID requestId) {
        if (containsNul(context)) throw ApiException.validation(requestId);
        return transaction.execute(status -> {
            active(owner, requestId);
            UUID id = UUID.randomUUID();
            jdbc.update("INSERT INTO app_conversations(id, owner_id, context) VALUES (?, ?, CAST(? AS jsonb))",
                    id, owner, mapper.writeValueAsString(context));
            return snapshot(find(owner, id, requestId, false));
        });
    }

    JsonNode get(UUID owner, UUID id, UUID requestId) {
        return transaction.execute(status -> {
            active(owner, requestId);
            return snapshot(find(owner, id, requestId, false));
        });
    }

    JsonNode list(UUID owner, String cursor, int size, UUID requestId) {
        if (size < 1 || size > 100) throw ApiException.validation(requestId);
        Cursor before = decodeCursor(cursor, requestId);
        return transaction.execute(status -> {
            active(owner, requestId);
            String sql = "SELECT *, (lease_until > clock_timestamp()) AS lease_active FROM app_conversations WHERE owner_id=?";
            List<Conversation> records;
            if (before == null) {
                records = jdbc.query(sql + " ORDER BY updated_at DESC, id DESC LIMIT ?", this::conversation, owner, size + 1);
            } else {
                records = jdbc.query(sql + " AND (updated_at, id) < (?, ?) ORDER BY updated_at DESC, id DESC LIMIT ?",
                        this::conversation, owner, Timestamp.from(before.updatedAt), before.id, size + 1);
            }
            ObjectNode result = envelope();
            var items = result.putArray("items");
            records.stream().limit(size).forEach(record -> items.add(metadata(record)));
            if (records.size() > size) result.put("next_cursor", encodeCursor(records.get(size - 1)));
            else result.putNull("next_cursor");
            return result;
        });
    }

    JsonNode rename(UUID owner, UUID id, String title, UUID requestId) {
        if (title == null || title.isBlank() || title.indexOf('\0') >= 0 || title.codePointCount(0, title.length()) > 100) throw ApiException.validation(requestId);
        return transaction.execute(status -> {
            active(owner, requestId);
            find(owner, id, requestId, true);
            jdbc.update("UPDATE app_conversations SET title=?, title_manual=TRUE, updated_at=clock_timestamp() WHERE id=? AND owner_id=?",
                    title, id, owner);
            return snapshot(find(owner, id, requestId, false));
        });
    }

    void delete(UUID owner, UUID id, UUID requestId) {
        transaction.executeWithoutResult(status -> {
            active(owner, requestId);
            Conversation conversation = find(owner, id, requestId, true);
            if (conversation.leaseActive) throw busy(requestId);
            jdbc.update("DELETE FROM app_conversations WHERE id=? AND owner_id=?", id, owner);
        });
    }

    JsonNode messages(UUID owner, UUID id, Long before, int size, UUID requestId) {
        if (size < 1 || size > 100 || (before != null && before < 1)) throw ApiException.validation(requestId);
        return transaction.execute(status -> {
            active(owner, requestId);
            find(owner, id, requestId, false);
            ObjectNode result = envelope().put("conversation_id", id.toString());
            addMessages(result, id, before, size);
            return result;
        });
    }

    JsonNode response(UUID owner, UUID id, UUID responseId, UUID requestId) {
        return transaction.execute(status -> {
            active(owner, requestId);
            find(owner, id, requestId, false);
            List<ObjectNode> results = jdbc.query("""
                    SELECT u.content AS query, a.response::text AS response
                    FROM app_conversation_messages a
                    JOIN app_conversation_messages u ON u.conversation_id=a.conversation_id AND u.seq=a.seq-1 AND u.role='user'
                    WHERE a.conversation_id=? AND a.message_id=? AND a.role='assistant'
                    """, (rs, row) -> {
                ObjectNode result = mapper.createObjectNode().put("query", rs.getString("query"));
                result.set("response", mapper.readTree(rs.getString("response")));
                return result;
            }, id, responseId);
            if (results.isEmpty()) throw ApiException.of(requestId, 404, "RESPONSE_NOT_FOUND", "Response not found", false);
            return results.getFirst();
        });
    }

    JsonNode message(UUID owner, UUID id, GenerationRequestValidator.MessageRequest input, UUID key, UUID requestId) {
        if (input.query().indexOf('\0') >= 0 || containsNul(input.context())) throw ApiException.validation(requestId);
        String hash = bodyHash(input);
        Claim claim = transaction.execute(status -> claim(owner, id, input, key, hash, requestId));
        if (claim.cached != null) return claim.cached;
        try {
            // The model call runs after the claim transaction has committed.
            JsonNode upstream = gateway.execute(requestId, claim.request);
            if (upstream == null || !upstream.isObject() || containsNul(upstream)) throw ApiException.invalidResponse(requestId);
            ObjectNode response = ((ObjectNode) upstream).deepCopy();
            response.put("conversation_id", id.toString()).put("response_id", UUID.randomUUID().toString());
            String execution = response.path("execution_status").stringValue();
            if (!Set.of("FAILED", "COMPLETED", "NEEDS_INPUT").contains(execution == null ? "" : execution)) {
                throw ApiException.invalidResponse(requestId);
            }
            if (!"FAILED".equals(execution) && !response.path("answer").path("text").isString()) {
                throw ApiException.invalidResponse(requestId);
            }
            return transaction.execute(status -> finish(owner, id, input, key, claim, response, requestId));
        } catch (RuntimeException failure) {
            try { release(owner, id, claim.token); }
            catch (RuntimeException cleanupFailure) { failure.addSuppressed(cleanupFailure); }
            throw failure;
        }
    }

    private Claim claim(UUID owner, UUID id, GenerationRequestValidator.MessageRequest input, UUID key,
                        String hash, UUID requestId) {
        active(owner, requestId);
        Conversation conversation = find(owner, id, requestId, true);
        if (key != null) {
            List<RequestRecord> existing = jdbc.query("SELECT body_hash, response::text AS response FROM app_conversation_requests WHERE conversation_id=? AND idempotency_key=?",
                    (rs, row) -> new RequestRecord(rs.getString("body_hash"), rs.getString("response")), id, key);
            if (!existing.isEmpty()) {
                RequestRecord previous = existing.getFirst();
                if (!previous.hash.equals(hash)) throw ApiException.of(requestId, 409, "IDEMPOTENCY_CONFLICT", "Idempotency key was used for another request", false);
                if (previous.response != null) return new Claim(null, null, null, mapper.readTree(previous.response));
            }
        }
        if (conversation.leaseActive) throw busy(requestId);
        UUID token = UUID.randomUUID();
        jdbc.update("UPDATE app_conversations SET lease_token=?, lease_until=clock_timestamp() + (? * interval '1 second') WHERE id=? AND owner_id=?",
                token, leaseSeconds, id, owner);
        if (key != null) {
            jdbc.update("INSERT INTO app_conversation_requests(conversation_id, idempotency_key, body_hash) VALUES (?, ?, ?) ON CONFLICT DO NOTHING", id, key, hash);
        }
        ObjectNode context = input.context() == null ? conversation.context.deepCopy() : input.context().deepCopy();
        ObjectNode request = mapper.createObjectNode().put("request_id", requestId.toString()).put("query", input.query());
        request.set("context", context.deepCopy());
        var history = request.putArray("history");
        List<ObjectNode> messages = readMessages(id, null, 20);
        Collections.reverse(messages);
        for (ObjectNode message : messages) {
            String role = message.get("role").stringValue();
            history.addObject().put("role", role).put("content", "assistant".equals(role)
                    ? historyContent(message) : message.get("content").stringValue());
        }
        return new Claim(token, context, request, null);
    }

    private JsonNode finish(UUID owner, UUID id, GenerationRequestValidator.MessageRequest input, UUID key,
                            Claim claim, ObjectNode response, UUID requestId) {
        active(owner, requestId);
        Conversation conversation = find(owner, id, requestId, true);
        if (!claim.token.equals(conversation.leaseToken) || !conversation.leaseActive) {
            throw ApiException.of(requestId, 409, "CONVERSATION_LEASE_EXPIRED", "Conversation execution lease expired", true);
        }
        if ("FAILED".equals(response.get("execution_status").stringValue())) {
            release(owner, id, claim.token);
            return response;
        }
        long firstSeq = Math.addExact(conversation.lastSeq, 1);
        long secondSeq = Math.addExact(firstSeq, 1);
        jdbc.update("INSERT INTO app_conversation_messages(conversation_id, seq, message_id, role, content) VALUES (?, ?, ?, 'user', ?)",
                id, firstSeq, UUID.randomUUID(), input.query());
        jdbc.update("INSERT INTO app_conversation_messages(conversation_id, seq, message_id, role, content, response) VALUES (?, ?, ?, 'assistant', ?, CAST(? AS jsonb))",
                id, secondSeq, UUID.fromString(response.get("response_id").stringValue()), response.get("answer").get("text").stringValue(), mapper.writeValueAsString(response));
        String title = conversation.lastSeq == 0 && !conversation.titleManual ? clip(input.query(), 50) : conversation.title;
        int changed = jdbc.update("""
                UPDATE app_conversations SET context=CAST(? AS jsonb), title=?, last_seq=?, updated_at=clock_timestamp(), lease_token=NULL, lease_until=NULL
                WHERE id=? AND owner_id=? AND lease_token=?
                """, mapper.writeValueAsString(claim.context), title, secondSeq, id, owner, claim.token);
        if (changed != 1) throw busy(requestId);
        if (key != null) {
            jdbc.update("UPDATE app_conversation_requests SET response=CAST(? AS jsonb) WHERE conversation_id=? AND idempotency_key=?",
                    mapper.writeValueAsString(response), id, key);
        }
        return response;
    }

    private void release(UUID owner, UUID id, UUID token) {
        jdbc.update("UPDATE app_conversations SET lease_token=NULL, lease_until=NULL WHERE id=? AND owner_id=? AND lease_token=?", id, owner, token);
    }

    private void active(UUID owner, UUID requestId) {
        List<String> statuses = jdbc.query("SELECT status FROM app_users WHERE id=? FOR SHARE", (rs, row) -> rs.getString(1), owner);
        if (statuses.isEmpty() || !"ACTIVE".equals(statuses.getFirst())) {
            throw ApiException.of(requestId, 403, "ACCOUNT_NOT_ACTIVE", "Active membership is required", false);
        }
    }

    private Conversation find(UUID owner, UUID id, UUID requestId, boolean update) {
        List<Conversation> records = jdbc.query("SELECT *, (lease_until > clock_timestamp()) AS lease_active FROM app_conversations WHERE id=? AND owner_id=? FOR " + (update ? "UPDATE" : "SHARE"),
                this::conversation, id, owner);
        if (records.isEmpty()) throw ApiException.conversationNotFound(requestId);
        return records.getFirst();
    }

    private Conversation conversation(ResultSet rs, int row) throws SQLException {
        return new Conversation(rs.getObject("id", UUID.class), rs.getString("title"), rs.getBoolean("title_manual"),
                (ObjectNode) mapper.readTree(rs.getString("context")), rs.getTimestamp("created_at").toInstant(),
                rs.getTimestamp("updated_at").toInstant(), rs.getLong("last_seq"), rs.getObject("lease_token", UUID.class), rs.getBoolean("lease_active"));
    }

    private ObjectNode envelope() { return mapper.createObjectNode().put("schema_version", "0.4").put("storage", "database"); }

    private ObjectNode metadata(Conversation record) {
        ObjectNode result = mapper.createObjectNode().put("conversation_id", record.id.toString()).put("title", record.title)
                .put("created_at", record.createdAt.toString()).put("updated_at", record.updatedAt.toString()).put("message_count", record.lastSeq);
        result.set("context", record.context.deepCopy());
        return result;
    }

    private ObjectNode snapshot(Conversation record) {
        ObjectNode result = metadata(record).put("schema_version", "0.4").put("storage", "database");
        addMessages(result, record.id, null, 50);
        return result;
    }

    private void addMessages(ObjectNode result, UUID id, Long before, int size) {
        List<ObjectNode> messages = readMessages(id, before, size + 1);
        boolean more = messages.size() > size;
        if (more) messages.removeLast();
        Collections.reverse(messages);
        var items = result.putArray("messages");
        messages.forEach(items::add);
        if (more) result.put("next_cursor", Long.toString(messages.getFirst().get("seq").longValue()));
        else result.putNull("next_cursor");
    }

    private List<ObjectNode> readMessages(UUID id, Long before, int size) {
        String sql = "SELECT seq, message_id, role, content, response::text AS response, created_at FROM app_conversation_messages WHERE conversation_id=?";
        var rowMapper = (org.springframework.jdbc.core.RowMapper<ObjectNode>) (rs, row) -> {
            ObjectNode result = mapper.createObjectNode().put("seq", rs.getLong("seq")).put("message_id", rs.getObject("message_id", UUID.class).toString())
                    .put("role", rs.getString("role")).put("content", rs.getString("content")).put("created_at", rs.getTimestamp("created_at").toInstant().toString());
            String response = rs.getString("response");
            if (response == null) result.putNull("response");
            else result.set("response", mapper.readTree(response));
            return result;
        };
        return new ArrayList<>(before == null
                ? jdbc.query(sql + " ORDER BY seq DESC LIMIT ?", rowMapper, id, size)
                : jdbc.query(sql + " AND seq < ? ORDER BY seq DESC LIMIT ?", rowMapper, id, before, size));
    }

    private String bodyHash(GenerationRequestValidator.MessageRequest input) {
        ObjectNode body = mapper.createObjectNode().put("query", input.query());
        if (input.context() == null) body.putNull("context");
        else body.set("context", input.context());
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(mapper.writeValueAsString(body).getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException failure) { throw new IllegalStateException(failure); }
    }

    // PostgreSQL text and jsonb cannot represent U+0000. Inspect decoded values so
    // a literal backslash followed by "u0000" remains valid user content.
    private static boolean containsNul(JsonNode value) {
        if (value == null) return false;
        if (value.isString()) return value.stringValue().indexOf('\0') >= 0;
        if (value.isObject() && value.propertyNames().stream().anyMatch(name -> name.indexOf('\0') >= 0)) return true;
        for (JsonNode child : value) if (containsNul(child)) return true;
        return false;
    }

    private String historyContent(ObjectNode message) {
        String content = message.get("content").stringValue();
        JsonNode options = message.path("response").path("answer").path("options");
        if (!options.isArray() || options.isEmpty()) return clip(content, 4000);
        var numbered = mapper.createArrayNode();
        int number = 1;
        for (JsonNode option : options) {
            if (!option.isString()) return clip(content, 4000);
            numbered.addObject().put("number", number++).put("text", option.stringValue());
        }
        String suffix = OPTIONS_SEPARATOR + mapper.writeValueAsString(numbered);
        int limit = 4000 - suffix.codePointCount(0, suffix.length());
        return limit < 1 ? clip(content, 4000) : clip(content, limit) + suffix;
    }

    private static String clip(String content, int limit) {
        if (content.codePointCount(0, content.length()) <= limit) return content;
        String prefix = content.substring(0, content.offsetByCodePoints(0, limit));
        if (prefix.codePoints().anyMatch(cp -> !whitespace(cp))) return prefix;
        int start = prefix.length();
        while (start < content.length() && whitespace(content.codePointAt(start))) start += Character.charCount(content.codePointAt(start));
        int remaining = Math.min(limit, content.codePointCount(start, content.length()));
        return content.substring(start, content.offsetByCodePoints(start, remaining));
    }

    private static boolean whitespace(int cp) { return Character.isWhitespace(cp) || Character.isSpaceChar(cp) || cp == 0x85; }

    private Cursor decodeCursor(String raw, UUID requestId) {
        if (raw == null) return null;
        try {
            if (raw.isEmpty() || raw.length() > 200 || !raw.matches("[A-Za-z0-9_-]+")) throw new IllegalArgumentException();
            String[] parts = new String(Base64.getUrlDecoder().decode(raw), StandardCharsets.UTF_8).split("\\|", -1);
            if (parts.length != 2 || !parts[1].matches("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")) throw new IllegalArgumentException();
            return new Cursor(Instant.parse(parts[0]), UUID.fromString(parts[1]));
        } catch (RuntimeException failure) { throw ApiException.validation(requestId); }
    }

    private String encodeCursor(Conversation conversation) {
        return Base64.getUrlEncoder().withoutPadding().encodeToString((conversation.updatedAt + "|" + conversation.id).getBytes(StandardCharsets.UTF_8));
    }

    private ApiException busy(UUID requestId) { return ApiException.of(requestId, 409, "CONVERSATION_BUSY", "Conversation is processing another request", true); }
    private record Conversation(UUID id, String title, boolean titleManual, ObjectNode context, Instant createdAt,
                                Instant updatedAt, long lastSeq, UUID leaseToken, boolean leaseActive) {}
    private record Cursor(Instant updatedAt, UUID id) {}
    private record RequestRecord(String hash, String response) {}
    private record Claim(UUID token, ObjectNode context, ObjectNode request, JsonNode cached) {}
}
