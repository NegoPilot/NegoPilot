package com.negopilot.backend.chat;

import com.negopilot.backend.agent.AgentProperties;
import com.negopilot.backend.common.api.ApiException;
import com.negopilot.backend.agent.GenerationGateway;
import com.negopilot.backend.agent.GenerationRequestValidator;
import com.negopilot.backend.chat.JdbcConversationRepository.Conversation;
import com.negopilot.backend.chat.JdbcConversationRepository.Cursor;
import com.negopilot.backend.chat.JdbcConversationRepository.RequestRecord;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.util.Base64;
import java.util.Collections;
import java.util.HexFormat;
import java.util.List;
import java.util.Set;
import java.util.UUID;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

@Service
final class PersistentConversationService {
    private static final String OPTIONS_SEPARATOR = "\n\n선택지 데이터:\n";
    private final JdbcConversationRepository repository;
    private final TransactionTemplate transaction;
    private final GenerationGateway gateway;
    private final JsonMapper mapper;
    private final long leaseSeconds;

    PersistentConversationService(JdbcConversationRepository repository, TransactionTemplate transaction, GenerationGateway gateway,
                                  JsonMapper mapper, AgentProperties properties) {
        this.repository = repository;
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
            repository.insertConversation(id, owner, context);
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
            List<Conversation> records = repository.findPage(owner, before, size + 1);
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
            repository.rename(owner, id, title);
            return snapshot(find(owner, id, requestId, false));
        });
    }

    void delete(UUID owner, UUID id, UUID requestId) {
        transaction.executeWithoutResult(status -> {
            active(owner, requestId);
            Conversation conversation = find(owner, id, requestId, true);
            if (conversation.leaseActive()) throw busy(requestId);
            repository.delete(owner, id);
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
            return repository.findStoredResponse(id, responseId).orElseThrow(() ->
                    ApiException.of(requestId, 404, "RESPONSE_NOT_FOUND", "Response not found", false));
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
            try { repository.releaseLease(owner, id, claim.token); }
            catch (RuntimeException cleanupFailure) { failure.addSuppressed(cleanupFailure); }
            throw failure;
        }
    }

    private Claim claim(UUID owner, UUID id, GenerationRequestValidator.MessageRequest input, UUID key,
                        String hash, UUID requestId) {
        active(owner, requestId);
        Conversation conversation = find(owner, id, requestId, true);
        if (key != null) {
            var existing = repository.findRequest(id, key);
            if (existing.isPresent()) {
                RequestRecord previous = existing.get();
                if (!previous.hash().equals(hash)) throw ApiException.of(requestId, 409, "IDEMPOTENCY_CONFLICT", "Idempotency key was used for another request", false);
                JsonNode cached = repository.cachedResponse(previous);
                if (cached != null) return new Claim(null, null, null, cached);
            }
        }
        if (conversation.leaseActive()) throw busy(requestId);
        UUID token = UUID.randomUUID();
        repository.claimLease(owner, id, token, leaseSeconds);
        if (key != null) {
            repository.rememberRequest(id, key, hash);
        }
        ObjectNode context = input.context() == null ? conversation.context().deepCopy() : input.context().deepCopy();
        ObjectNode request = mapper.createObjectNode().put("request_id", requestId.toString()).put("query", input.query());
        request.set("context", context.deepCopy());
        var history = request.putArray("history");
        List<ObjectNode> messages = repository.readMessages(id, null, 20);
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
        if (!claim.token.equals(conversation.leaseToken()) || !conversation.leaseActive()) {
            throw ApiException.of(requestId, 409, "CONVERSATION_LEASE_EXPIRED", "Conversation execution lease expired", true);
        }
        if ("FAILED".equals(response.get("execution_status").stringValue())) {
            repository.releaseLease(owner, id, claim.token);
            return response;
        }
        long firstSeq = Math.addExact(conversation.lastSeq(), 1);
        long secondSeq = Math.addExact(firstSeq, 1);
        repository.insertUserMessage(id, firstSeq, UUID.randomUUID(), input.query());
        repository.insertAssistantMessage(id, secondSeq, UUID.fromString(response.get("response_id").stringValue()),
                response.get("answer").get("text").stringValue(), response);
        String title = conversation.lastSeq() == 0 && !conversation.titleManual() ? clip(input.query(), 50) : conversation.title();
        int changed = repository.completeConversation(owner, id, claim.token, claim.context, title, secondSeq);
        if (changed != 1) throw busy(requestId);
        if (key != null) {
            repository.cacheResponse(id, key, response);
        }
        return response;
    }

    private void active(UUID owner, UUID requestId) {
        if (!"ACTIVE".equals(repository.memberStatusForShare(owner))) {
            throw ApiException.of(requestId, 403, "ACCOUNT_NOT_ACTIVE", "Active membership is required", false);
        }
    }

    private Conversation find(UUID owner, UUID id, UUID requestId, boolean update) {
        return repository.find(owner, id, update).orElseThrow(() -> ApiException.conversationNotFound(requestId));
    }

    private ObjectNode envelope() { return mapper.createObjectNode().put("schema_version", "0.4").put("storage", "database"); }

    private ObjectNode metadata(Conversation record) {
        ObjectNode result = mapper.createObjectNode().put("conversation_id", record.id().toString()).put("title", record.title())
                .put("created_at", record.createdAt().toString()).put("updated_at", record.updatedAt().toString()).put("message_count", record.lastSeq());
        result.set("context", record.context().deepCopy());
        return result;
    }

    private ObjectNode snapshot(Conversation record) {
        ObjectNode result = metadata(record).put("schema_version", "0.4").put("storage", "database");
        addMessages(result, record.id(), null, 50);
        return result;
    }

    private void addMessages(ObjectNode result, UUID id, Long before, int size) {
        List<ObjectNode> messages = repository.readMessages(id, before, size + 1);
        boolean more = messages.size() > size;
        if (more) messages.removeLast();
        Collections.reverse(messages);
        var items = result.putArray("messages");
        messages.forEach(items::add);
        if (more) result.put("next_cursor", Long.toString(messages.getFirst().get("seq").longValue()));
        else result.putNull("next_cursor");
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
        return Base64.getUrlEncoder().withoutPadding().encodeToString((conversation.updatedAt() + "|" + conversation.id()).getBytes(StandardCharsets.UTF_8));
    }

    private ApiException busy(UUID requestId) { return ApiException.of(requestId, 409, "CONVERSATION_BUSY", "Conversation is processing another request", true); }
    private record Claim(UUID token, ObjectNode context, ObjectNode request, JsonNode cached) {}
}
