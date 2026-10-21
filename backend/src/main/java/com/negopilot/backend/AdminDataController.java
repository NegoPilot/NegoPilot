package com.negopilot.backend;

import jakarta.servlet.http.HttpServletRequest;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;
import org.springframework.context.annotation.Profile;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@Profile("!legacy-test")
final class AdminDataController {
    private final CurrentUser currentUser;
    private final AdminSupplierService suppliers;
    private final AdminLawGateway laws;
    AdminDataController(CurrentUser currentUser, AdminSupplierService suppliers, AdminLawGateway laws) {
        this.currentUser = currentUser; this.suppliers = suppliers; this.laws = laws;
    }
    @GetMapping("/api/v1/admin/data/summary")
    Map<String, Object> summary(HttpServletRequest request) {
        currentUser.requireAdmin();
        UUID id = UUID.randomUUID();
        request.setAttribute("request_id", id);
        if (!request.getParameterMap().isEmpty()) throw ApiException.validation(id);
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("checked_at", Instant.now().toString());
        try { result.put("suppliers", available(suppliers.summary())); }
        catch (ApiException error) { result.put("suppliers", unavailable(error.code)); }
        try { result.put("laws", available(laws.read("/internal/v1/admin/laws", id))); }
        catch (ApiException error) { result.put("laws", unavailable(error.code)); }
        return result;
    }
    private Map<String, Object> available(Object data) {
        return Map.of("status", "AVAILABLE", "data", data);
    }
    private Map<String, Object> unavailable(String code) {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("status", "UNAVAILABLE"); result.put("data", null); result.put("error_code", code);
        return result;
    }
}
