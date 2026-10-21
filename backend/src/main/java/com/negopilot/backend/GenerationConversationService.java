package com.negopilot.backend;

import java.util.ArrayDeque;
import java.util.Deque;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import org.springframework.stereotype.Service;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

@Service
final class GenerationConversationService {
    private final ConcurrentHashMap<String, Conversation> conversations = new ConcurrentHashMap<>();
    private final GenerationGateway gateway;
    private final JsonMapper mapper;

    GenerationConversationService(GenerationGateway gateway, JsonMapper mapper) {
        this.gateway = gateway;
        this.mapper = mapper;
    }

    JsonNode create(ObjectNode context, UUID requestId) {
        Conversation conversation = new Conversation(UUID.randomUUID(), context.deepCopy());
        conversations.put(conversation.id.toString(), conversation);
        return snapshot(conversation);
    }

    JsonNode get(String conversationId, UUID requestId) {
        Conversation conversation = find(conversationId, requestId);
        synchronized (conversation) { return snapshot(conversation); }
    }

    JsonNode message(String conversationId, GenerationRequestValidator.MessageRequest input, UUID requestId) {
        Conversation conversation = find(conversationId, requestId);
        synchronized (conversation) {
            ObjectNode context = input.context() == null ? conversation.context.deepCopy() : input.context().deepCopy();
            ObjectNode request = mapper.createObjectNode().put("request_id", requestId.toString()).put("query", input.query());
            request.set("context", context.deepCopy());
            var history = request.putArray("history");
            int skip = Math.max(0, conversation.messages.size() - 20);
            for (ObjectNode stored : conversation.messages) {
                if (skip-- > 0) continue;
                String role = stored.get("role").stringValue();
                String content = stored.get("content").stringValue();
                history.addObject().put("role", role)
                        .put("content", "assistant".equals(role) ? historyContent(content) : content);
            }
            JsonNode upstream = gateway.execute(requestId, request);
            if (upstream == null || !upstream.isObject()) throw ApiException.invalidResponse(requestId);
            ObjectNode response = ((ObjectNode) upstream).deepCopy();
            response.put("conversation_id", conversation.id.toString()).put("response_id", UUID.randomUUID().toString());
            String status = response.path("execution_status").stringValue();
            if ("FAILED".equals(status)) return response;
            if (status == null || !Set.of("COMPLETED", "NEEDS_INPUT").contains(status)
                    || !response.path("answer").path("text").isString()) {
                throw ApiException.invalidResponse(requestId);
            }

            // Prepare both messages before committing either message or the new context.
            ObjectNode user = mapper.createObjectNode().put("message_id", UUID.randomUUID().toString())
                    .put("role", "user").put("content", input.query()).putNull("response");
            ObjectNode assistant = mapper.createObjectNode().put("message_id", response.get("response_id").stringValue())
                    .put("role", "assistant").put("content", response.get("answer").get("text").stringValue());
            assistant.set("response", response.deepCopy());
            conversation.messages.addLast(user);
            conversation.messages.addLast(assistant);
            conversation.context = context;
            while (conversation.messages.size() > 100) {
                conversation.messages.removeFirst();
                conversation.messages.removeFirst();
            }
            return response;
        }
    }

    private static String historyContent(String content) {
        if (content.codePointCount(0, content.length()) <= 4000) return content;
        int prefixEnd = content.offsetByCodePoints(0, 4000);
        String prefix = content.substring(0, prefixEnd);
        if (prefix.codePoints().anyMatch(codePoint -> !whitespace(codePoint))) return prefix;

        // A valid full Answer must not become a blank history entry after clipping.
        int start = prefixEnd;
        while (start < content.length() && whitespace(content.codePointAt(start))) {
            start += Character.charCount(content.codePointAt(start));
        }
        int length = Math.min(4000, content.codePointCount(start, content.length()));
        return content.substring(start, content.offsetByCodePoints(start, length));
    }

    private static boolean whitespace(int codePoint) {
        return Character.isWhitespace(codePoint) || Character.isSpaceChar(codePoint) || codePoint == 0x85;
    }

    private Conversation find(String id, UUID requestId) {
        Conversation conversation = conversations.get(id);
        if (conversation == null) throw ApiException.conversationNotFound(requestId);
        return conversation;
    }

    private ObjectNode snapshot(Conversation conversation) {
        ObjectNode result = mapper.createObjectNode().put("schema_version", "0.3")
                .put("conversation_id", conversation.id.toString()).put("storage", "memory");
        result.set("context", conversation.context.deepCopy());
        var messages = result.putArray("messages");
        conversation.messages.forEach(message -> messages.add(message.deepCopy()));
        return result;
    }

    private static final class Conversation {
        final UUID id;
        ObjectNode context;
        final Deque<ObjectNode> messages = new ArrayDeque<>();
        Conversation(UUID id, ObjectNode context) { this.id = id; this.context = context; }
    }
}
