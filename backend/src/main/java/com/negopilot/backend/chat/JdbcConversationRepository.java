package com.negopilot.backend.chat;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

/** Participates in the service's transactions without opening a new transaction. */
@Repository
class JdbcConversationRepository {
    private final JdbcTemplate jdbc;
    private final JsonMapper mapper;

    JdbcConversationRepository(JdbcTemplate jdbc, JsonMapper mapper) {
        this.jdbc = jdbc;
        this.mapper = mapper;
    }

    String memberStatusForShare(UUID owner) {
        List<String> statuses = jdbc.query("SELECT status FROM app_users WHERE id=? FOR SHARE", (rs, row) -> rs.getString(1), owner);
        return statuses.isEmpty() ? null : statuses.getFirst();
    }

    void insertConversation(UUID id, UUID owner, ObjectNode context) {
        jdbc.update("INSERT INTO app_conversations(id, owner_id, context) VALUES (?, ?, CAST(? AS jsonb))",
                id, owner, mapper.writeValueAsString(context));
    }

    Optional<Conversation> find(UUID owner, UUID id, boolean update) {
        return jdbc.query("SELECT *, (lease_until > clock_timestamp()) AS lease_active FROM app_conversations WHERE id=? AND owner_id=? FOR " + (update ? "UPDATE" : "SHARE"),
                this::conversation, id, owner).stream().findFirst();
    }

    List<Conversation> findPage(UUID owner, Cursor before, int limit) {
        String sql = "SELECT *, (lease_until > clock_timestamp()) AS lease_active FROM app_conversations WHERE owner_id=?";
        if (before == null) {
            return jdbc.query(sql + " ORDER BY updated_at DESC, id DESC LIMIT ?", this::conversation, owner, limit);
        }
        return jdbc.query(sql + " AND (updated_at, id) < (?, ?) ORDER BY updated_at DESC, id DESC LIMIT ?",
                this::conversation, owner, Timestamp.from(before.updatedAt), before.id, limit);
    }

    void rename(UUID owner, UUID id, String title) {
        jdbc.update("UPDATE app_conversations SET title=?, title_manual=TRUE, updated_at=clock_timestamp() WHERE id=? AND owner_id=?",
                title, id, owner);
    }

    void delete(UUID owner, UUID id) {
        jdbc.update("DELETE FROM app_conversations WHERE id=? AND owner_id=?", id, owner);
    }

    // Callers first lock the owner's conversation in the same transaction.
    Optional<ObjectNode> findStoredResponse(UUID id, UUID responseId) {
        return jdbc.query("""
                SELECT u.content AS query, a.response::text AS response
                FROM app_conversation_messages a
                JOIN app_conversation_messages u ON u.conversation_id=a.conversation_id AND u.seq=a.seq-1 AND u.role='user'
                WHERE a.conversation_id=? AND a.message_id=? AND a.role='assistant'
                """, (rs, row) -> {
            ObjectNode result = mapper.createObjectNode().put("query", rs.getString("query"));
            result.set("response", mapper.readTree(rs.getString("response")));
            return result;
        }, id, responseId).stream().findFirst();
    }

    Optional<RequestRecord> findRequest(UUID id, UUID key) {
        return jdbc.query("SELECT body_hash, response::text AS response FROM app_conversation_requests WHERE conversation_id=? AND idempotency_key=?",
                (rs, row) -> new RequestRecord(rs.getString("body_hash"), rs.getString("response")), id, key).stream().findFirst();
    }

    JsonNode cachedResponse(RequestRecord request) {
        return request.response == null ? null : mapper.readTree(request.response);
    }

    void claimLease(UUID owner, UUID id, UUID token, long leaseSeconds) {
        jdbc.update("UPDATE app_conversations SET lease_token=?, lease_until=clock_timestamp() + (? * interval '1 second') WHERE id=? AND owner_id=?",
                token, leaseSeconds, id, owner);
    }

    void rememberRequest(UUID id, UUID key, String hash) {
        jdbc.update("INSERT INTO app_conversation_requests(conversation_id, idempotency_key, body_hash) VALUES (?, ?, ?) ON CONFLICT DO NOTHING", id, key, hash);
    }

    void insertUserMessage(UUID id, long seq, UUID messageId, String content) {
        jdbc.update("INSERT INTO app_conversation_messages(conversation_id, seq, message_id, role, content) VALUES (?, ?, ?, 'user', ?)",
                id, seq, messageId, content);
    }

    void insertAssistantMessage(UUID id, long seq, UUID messageId, String content, ObjectNode response) {
        jdbc.update("INSERT INTO app_conversation_messages(conversation_id, seq, message_id, role, content, response) VALUES (?, ?, ?, 'assistant', ?, CAST(? AS jsonb))",
                id, seq, messageId, content, mapper.writeValueAsString(response));
    }

    int completeConversation(UUID owner, UUID id, UUID token, ObjectNode context, String title, long lastSeq) {
        return jdbc.update("""
                UPDATE app_conversations SET context=CAST(? AS jsonb), title=?, last_seq=?, updated_at=clock_timestamp(), lease_token=NULL, lease_until=NULL
                WHERE id=? AND owner_id=? AND lease_token=?
                """, mapper.writeValueAsString(context), title, lastSeq, id, owner, token);
    }

    void cacheResponse(UUID id, UUID key, ObjectNode response) {
        jdbc.update("UPDATE app_conversation_requests SET response=CAST(? AS jsonb) WHERE conversation_id=? AND idempotency_key=?",
                mapper.writeValueAsString(response), id, key);
    }

    void releaseLease(UUID owner, UUID id, UUID token) {
        jdbc.update("UPDATE app_conversations SET lease_token=NULL, lease_until=NULL WHERE id=? AND owner_id=? AND lease_token=?", id, owner, token);
    }

    // Keep this list mutable: the service trims and reverses it for pages and history.
    List<ObjectNode> readMessages(UUID id, Long before, int size) {
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

    private Conversation conversation(ResultSet rs, int row) throws SQLException {
        return new Conversation(rs.getObject("id", UUID.class), rs.getString("title"), rs.getBoolean("title_manual"),
                (ObjectNode) mapper.readTree(rs.getString("context")), rs.getTimestamp("created_at").toInstant(),
                rs.getTimestamp("updated_at").toInstant(), rs.getLong("last_seq"), rs.getObject("lease_token", UUID.class), rs.getBoolean("lease_active"));
    }

    record Conversation(UUID id, String title, boolean titleManual, ObjectNode context, Instant createdAt,
                        Instant updatedAt, long lastSeq, UUID leaseToken, boolean leaseActive) {}
    record Cursor(Instant updatedAt, UUID id) {}
    record RequestRecord(String hash, String response) {}
}
