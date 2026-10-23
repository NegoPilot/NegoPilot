package com.negopilot.backend.common.api;

import java.util.UUID;
import org.springframework.http.HttpStatus;

public final class ApiException extends RuntimeException {
    public final UUID requestId;
    public final HttpStatus status;
    public final String code;
    public final boolean retryable;
    public final Object details;

    private ApiException(UUID requestId, HttpStatus status, String code, String message, boolean retryable) {
        this(requestId, status, code, message, retryable, null);
    }

    private ApiException(UUID requestId, HttpStatus status, String code, String message, boolean retryable,
                         Object details) {
        super(message);
        this.requestId = requestId;
        this.status = status;
        this.code = code;
        this.retryable = retryable;
        this.details = details;
    }

    public static ApiException of(UUID id, int status, String code, String message, boolean retryable) {
        return new ApiException(id, HttpStatus.valueOf(status), code, message, retryable);
    }

    public static ApiException of(UUID id, int status, String code, String message, boolean retryable,
                                  Object details) {
        return new ApiException(id, HttpStatus.valueOf(status), code, message, retryable, details);
    }

    public static ApiException validation(UUID id) {
        return new ApiException(id, HttpStatus.BAD_REQUEST, "VALIDATION_ERROR", "Invalid query request", false);
    }

    public static ApiException unavailable(UUID id) {
        return new ApiException(id, HttpStatus.SERVICE_UNAVAILABLE, "UPSTREAM_UNAVAILABLE", "Agent service unavailable", true);
    }

    public static ApiException timeout(UUID id) {
        return new ApiException(id, HttpStatus.GATEWAY_TIMEOUT, "UPSTREAM_TIMEOUT", "Agent service timed out", true);
    }

    public static ApiException invalidResponse(UUID id) {
        return new ApiException(id, HttpStatus.BAD_GATEWAY, "UPSTREAM_INVALID_RESPONSE", "Invalid agent response", false);
    }

    public static ApiException internal(UUID id) {
        return new ApiException(id, HttpStatus.INTERNAL_SERVER_ERROR, "INTERNAL_ERROR", "Internal server error", false);
    }

    public static ApiException supplierNotFound(UUID id) {
        return new ApiException(id, HttpStatus.NOT_FOUND, "SUPPLIER_NOT_FOUND", "Supplier not found", false);
    }

    public static ApiException conversationNotFound(UUID id) {
        return new ApiException(id, HttpStatus.NOT_FOUND, "CONVERSATION_NOT_FOUND", "Conversation not found", false);
    }
}
