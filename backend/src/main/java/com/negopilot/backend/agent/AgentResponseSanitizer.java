package com.negopilot.backend.agent;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ObjectNode;

/** Removes internal routing and tool diagnostics from validated public responses. */
final class AgentResponseSanitizer {
    private AgentResponseSanitizer() { }

    static JsonNode publicResponse(JsonNode validatedResponse, String fallbackMessage) {
        ObjectNode response = (ObjectNode) validatedResponse.deepCopy();
        ObjectNode routing = (ObjectNode) response.get("routing");
        for (JsonNode node : routing.get("outcomes")) {
            ObjectNode outcome = (ObjectNode) node;
            sanitizeToolErrors(outcome.get("calls"));
            sanitizeToolErrors(outcome.get("referenced_articles"));
            if (!outcome.get("message").isNull()) {
                outcome.put("message", "Routing details are unavailable.");
            }
        }
        if (!routing.get("halt_reason").isNull()) {
            routing.put("halt_reason", "Routing halted because a dependency returned no results.");
        }
        if (!response.get("error").isNull()) {
            ObjectNode error = (ObjectNode) response.get("error");
            error.put("message", error.get("code").stringValue().equals("DECOMPOSITION_FAILED")
                    ? "Query decomposition failed." : fallbackMessage);
        }
        return response;
    }

    private static void sanitizeToolErrors(JsonNode calls) {
        for (JsonNode call : calls) {
            if (!call.get("error").isNull()) {
                ((ObjectNode) call).put("error", "Tool execution failed.");
            }
        }
    }
}
