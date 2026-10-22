package com.negopilot.backend.auth;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.util.UUID;
import tools.jackson.databind.json.JsonMapper;

final class SecurityJsonErrors {
    private SecurityJsonErrors() { }
    static void write(HttpServletRequest request, HttpServletResponse response, JsonMapper mapper, int status, String code, String message) throws IOException {
        UUID id = request.getAttribute("request_id") instanceof UUID existing ? existing : UUID.randomUUID();
        request.setAttribute("request_id", id);
        var body = mapper.createObjectNode().put("request_id", id.toString());
        body.putObject("error").put("code", code).put("message", message).put("retryable", false);
        response.setStatus(status);
        response.setContentType("application/json");
        response.getOutputStream().write(mapper.writeValueAsBytes(body));
    }
}
