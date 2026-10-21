package com.negopilot.backend;

import java.util.UUID;
import org.springframework.http.HttpStatus;

final class ApiException extends RuntimeException {
    final UUID requestId;
    final HttpStatus status;
    final String code;
    final boolean retryable;

    private ApiException(UUID requestId, HttpStatus status, String code, String message, boolean retryable) {
        super(message);
        this.requestId = requestId;
        this.status = status;
        this.code = code;
        this.retryable = retryable;
    }

    static ApiException validation(UUID id) {
        return new ApiException(id, HttpStatus.BAD_REQUEST, "VALIDATION_ERROR", "Invalid query request", false);
    }

    static ApiException unavailable(UUID id) {
        return new ApiException(id, HttpStatus.SERVICE_UNAVAILABLE, "UPSTREAM_UNAVAILABLE", "Agent service unavailable", true);
    }

    static ApiException timeout(UUID id) {
        return new ApiException(id, HttpStatus.GATEWAY_TIMEOUT, "UPSTREAM_TIMEOUT", "Agent service timed out", true);
    }

    static ApiException invalidResponse(UUID id) {
        return new ApiException(id, HttpStatus.BAD_GATEWAY, "UPSTREAM_INVALID_RESPONSE", "Invalid agent response", false);
    }

    static ApiException internal(UUID id) {
        return new ApiException(id, HttpStatus.INTERNAL_SERVER_ERROR, "INTERNAL_ERROR", "Internal server error", false);
    }

    static ApiException demoRequired(UUID id) {
        return new ApiException(id, HttpStatus.SERVICE_UNAVAILABLE, "DEMO_MODE_REQUIRED", "Stub mode is required", false);
    }

    static ApiException supplierNotFound(UUID id) {
        return new ApiException(id, HttpStatus.NOT_FOUND, "SUPPLIER_NOT_FOUND", "Supplier not found", false);
    }

    static ApiException conversationNotFound(UUID id) {
        return new ApiException(id, HttpStatus.NOT_FOUND, "CONVERSATION_NOT_FOUND", "Conversation not found", false);
    }
}
