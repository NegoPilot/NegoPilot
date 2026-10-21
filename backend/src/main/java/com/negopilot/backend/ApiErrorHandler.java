package com.negopilot.backend;

import jakarta.servlet.http.HttpServletRequest;
import java.util.UUID;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.HttpMediaTypeNotSupportedException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

@RestControllerAdvice
final class ApiErrorHandler {
    record ErrorDetail(String code, String message, boolean retryable) {}
    record ErrorResponse(UUID request_id, ErrorDetail error) {}

    @ExceptionHandler(ApiException.class)
    ResponseEntity<ErrorResponse> apiError(ApiException exception) {
        return ResponseEntity.status(exception.status).body(new ErrorResponse(exception.requestId,
                new ErrorDetail(exception.code, exception.getMessage(), exception.retryable)));
    }

    @ExceptionHandler({HttpMessageNotReadableException.class, HttpMediaTypeNotSupportedException.class})
    ResponseEntity<ErrorResponse> invalidBody(Exception ignored) {
        return apiError(ApiException.validation(null));
    }

    @ExceptionHandler(Exception.class)
    ResponseEntity<ErrorResponse> unexpected(Exception ignored, HttpServletRequest request) {
        return apiError(ApiException.internal((UUID) request.getAttribute("request_id")));
    }
}
