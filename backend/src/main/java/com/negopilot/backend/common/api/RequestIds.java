package com.negopilot.backend.common.api;

import java.util.UUID;
import tools.jackson.databind.JsonNode;

public final class RequestIds {
    private RequestIds() {}

    public static UUID parse(JsonNode node) {
        if (node == null || !node.isString()) return null;
        String value = node.stringValue();
        // Match the UUID textual forms accepted by Python's Pydantic UUID field.
        if (value.matches("[0-9a-fA-F]{32}")) {
            value = value.substring(0, 8) + "-" + value.substring(8, 12) + "-" + value.substring(12, 16)
                    + "-" + value.substring(16, 20) + "-" + value.substring(20);
        } else if (value.startsWith("urn:uuid:")) {
            value = value.substring(9);
        } else if (value.startsWith("{") && value.endsWith("}")) {
            value = value.substring(1, value.length() - 1);
        }
        if (!value.matches("[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")) return null;
        return UUID.fromString(value);
    }
}
