package com.negopilot.backend;

import java.util.ArrayDeque;
import java.util.Deque;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

@Service
final class SupplierChatService {
    private final ConcurrentHashMap<String, Conversation> conversations = new ConcurrentHashMap<>();
    private final SupplierChatGateway gateway;
    private final JsonMapper mapper;
    private final String mode;

    SupplierChatService(SupplierChatGateway gateway, JsonMapper mapper,
                        @Value("${agent.execution-mode:live}") String mode) {
        this.gateway = gateway;
        this.mapper = mapper;
        this.mode = mode;
    }

    void requireDemo(UUID requestId) {
        if (!"stub".equals(mode)) throw ApiException.demoRequired(requestId);
    }

    // Lookups read the fixture in stub mode and business-mcp in live mode. Conversations are stub-only.
    JsonNode catalog(UUID id) { return gateway.catalog(id); }
    JsonNode search(ObjectNode filters, UUID id) { return gateway.search(filters, id); }
    JsonNode detail(String supplierId, UUID id) { return gateway.detail(supplierId, id); }

    JsonNode create(ObjectNode context, UUID requestId) {
        requireDemo(requestId);
        validateSupplier(context, requestId);
        Conversation conversation = new Conversation(UUID.randomUUID(), context.deepCopy());
        conversations.put(conversation.id.toString(), conversation);
        return snapshot(conversation);
    }

    JsonNode get(String conversationId, UUID requestId) {
        requireDemo(requestId);
        Conversation conversation = find(conversationId, requestId);
        synchronized (conversation) { return snapshot(conversation); }
    }

    JsonNode message(String conversationId, SupplierChatRequestValidator.MessageRequest input, UUID requestId) {
        requireDemo(requestId);
        Conversation conversation = find(conversationId, requestId);
        // Keep validation and the upstream call under this conversation's lock only.
        synchronized (conversation) {
            ObjectNode context = input.context() == null ? conversation.context.deepCopy() : input.context().deepCopy();
            if (input.context() != null) validateSupplier(context, requestId);
            ObjectNode request = mapper.createObjectNode().put("request_id", requestId.toString())
                    .put("conversation_id", conversation.id.toString()).put("query", input.query());
            request.set("context", context);
            var history = request.putArray("history");
            int skip = Math.max(0, conversation.messages.size() - 20);
            for (ObjectNode stored : conversation.messages) {
                if (skip-- > 0) continue;
                history.addObject().put("role", stored.get("role").stringValue())
                        .put("content", stored.get("content").stringValue());
            }
            JsonNode response = gateway.chat(request, requestId, conversation.id, context, input.query());
            // Construct both entries before changing state. HTTP failures never commit a partial turn.
            ObjectNode user = mapper.createObjectNode().put("message_id", UUID.randomUUID().toString())
                    .put("role", "user").put("content", input.query()).putNull("response");
            ObjectNode assistant = mapper.createObjectNode().put("message_id", response.get("response_id").stringValue())
                    .put("role", "assistant").put("content", response.get("presentation").get("text").stringValue());
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

    private void validateSupplier(ObjectNode context, UUID requestId) {
        JsonNode ids = context.get("selected_supplier_ids");
        if (!ids.isEmpty()) gateway.detail(ids.get(0).stringValue(), requestId);
    }

    private Conversation find(String id, UUID requestId) {
        Conversation conversation = conversations.get(id);
        if (conversation == null) throw ApiException.conversationNotFound(requestId);
        return conversation;
    }

    private ObjectNode snapshot(Conversation conversation) {
        ObjectNode snapshot = mapper.createObjectNode().put("schema_version", "0.2").put("mode", "stub")
                .put("conversation_id", conversation.id.toString()).put("storage", "memory");
        snapshot.set("context", conversation.context.deepCopy());
        var messages = snapshot.putArray("messages");
        conversation.messages.forEach(message -> messages.add(message.deepCopy()));
        return snapshot;
    }

    private static final class Conversation {
        final UUID id;
        ObjectNode context;
        final Deque<ObjectNode> messages = new ArrayDeque<>();
        Conversation(UUID id, ObjectNode context) { this.id = id; this.context = context; }
    }
}
