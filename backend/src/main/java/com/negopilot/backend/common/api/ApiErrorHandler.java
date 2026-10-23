package com.negopilot.backend.common.api;

import jakarta.servlet.http.HttpServletRequest;
import java.util.Collections;
import java.util.IdentityHashMap;
import java.util.Set;
import java.util.UUID;
import org.springframework.core.annotation.AnnotatedElementUtils;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.HttpStatusCode;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.HttpMediaTypeNotSupportedException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.server.ResponseStatusException;

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
    ResponseEntity<ErrorResponse> unexpected(Exception exception, HttpServletRequest request) {
        UUID requestId = request.getAttribute("request_id") instanceof UUID id ? id : null;
        if (exception instanceof org.springframework.web.ErrorResponse error) {
            return httpError(error.getStatusCode(), error.getHeaders(), requestId);
        }

        // Match Spring's response-status resolver for annotated or wrapped HTTP exceptions.
        Set<Throwable> visited = Collections.newSetFromMap(new IdentityHashMap<>());
        Exception current = exception;
        while (visited.add(current)) {
            if (current instanceof ResponseStatusException error) {
                return httpError(error.getStatusCode(), error.getHeaders(), requestId);
            }
            ResponseStatus status = AnnotatedElementUtils.findMergedAnnotation(current.getClass(), ResponseStatus.class);
            if (status != null) {
                return httpError(status.code(), HttpHeaders.EMPTY, requestId);
            }
            if (!(current.getCause() instanceof Exception cause)) {
                break;
            }
            current = cause;
        }
        return apiError(ApiException.internal(requestId));
    }

    private ResponseEntity<ErrorResponse> httpError(HttpStatusCode statusCode, HttpHeaders headers, UUID requestId) {
        HttpStatus status = HttpStatus.resolve(statusCode.value());
        ErrorDetail detail = status == HttpStatus.INTERNAL_SERVER_ERROR
                ? new ErrorDetail("INTERNAL_ERROR", "Internal server error", false)
                : status == null
                        ? new ErrorDetail("HTTP_ERROR", "HTTP request failed", false)
                        : new ErrorDetail(status.name(), status.getReasonPhrase(), false);
        HttpHeaders responseHeaders = new HttpHeaders();
        responseHeaders.putAll(headers);
        // The original body metadata no longer describes the public JSON envelope.
        responseHeaders.remove(HttpHeaders.CONTENT_TYPE);
        responseHeaders.remove(HttpHeaders.CONTENT_LENGTH);
        return ResponseEntity.status(statusCode).headers(responseHeaders).contentType(MediaType.APPLICATION_JSON)
                .body(new ErrorResponse(requestId, detail));
    }
}
