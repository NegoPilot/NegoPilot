package com.negopilot.backend.admin.data;

import com.negopilot.backend.admin.law.AdminLawGateway;
import com.negopilot.backend.admin.supplier.AdminSupplierService;
import com.negopilot.backend.common.api.ApiException;
import com.negopilot.backend.common.api.ApiPaths;
import com.negopilot.backend.common.http.InternalPaths;
import com.negopilot.backend.auth.CurrentUser;

import jakarta.servlet.http.HttpServletRequest;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;
import java.util.function.Supplier;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public final class AdminDataController {
    private final CurrentUser currentUser;
    private final AdminSupplierService suppliers;
    private final AdminLawGateway laws;
    AdminDataController(CurrentUser currentUser, AdminSupplierService suppliers, AdminLawGateway laws) {
        this.currentUser = currentUser; this.suppliers = suppliers; this.laws = laws;
    }
    @GetMapping(ApiPaths.ADMIN + "/data/summary")
    Map<String, Object> summary(HttpServletRequest request) {
        currentUser.requireAdmin();
        UUID id = UUID.randomUUID();
        request.setAttribute("request_id", id);
        if (!request.getParameterMap().isEmpty()) throw ApiException.validation(id);
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("checked_at", Instant.now().toString());
        result.put("suppliers", sourceSummary(suppliers::summary));
        result.put("laws", sourceSummary(() -> laws.read(InternalPaths.ADMIN_LAWS, id)));
        return result;
    }
    private Map<String, Object> sourceSummary(Supplier<?> source) {
        try {
            return Map.of("status", "AVAILABLE", "data", source.get());
        } catch (ApiException error) {
            Map<String, Object> result = new LinkedHashMap<>();
            result.put("status", "UNAVAILABLE"); result.put("data", null); result.put("error_code", error.code);
            return result;
        }
    }
}
