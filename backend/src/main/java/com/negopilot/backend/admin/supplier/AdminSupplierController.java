package com.negopilot.backend.admin.supplier;

import com.negopilot.backend.common.api.ApiException;
import com.negopilot.backend.auth.CurrentUser;
import com.negopilot.backend.UserIdentity;

import jakarta.servlet.http.HttpServletRequest;
import java.util.Map;
import java.util.UUID;
import org.springframework.context.annotation.Profile;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

@RestController
@Profile("!legacy-test")
@RequestMapping(value = "/api/v1/admin/suppliers", produces = MediaType.APPLICATION_JSON_VALUE)
final class AdminSupplierController {
    private final CurrentUser currentUser;
    private final AdminSupplierValidator validator;
    private final AdminSupplierService service;
    AdminSupplierController(CurrentUser currentUser, AdminSupplierValidator validator, AdminSupplierService service) {
        this.currentUser = currentUser; this.validator = validator; this.service = service;
    }

    @GetMapping
    Map<String, Object> list(HttpServletRequest request) {
        currentUser.requireAdmin(); UUID id = begin(request, false);
        return service.list(validator.search(request.getParameterMap(), id), id);
    }
    @GetMapping("/{supplierId}")
    Map<String, Object> detail(@PathVariable String supplierId, HttpServletRequest request) {
        currentUser.requireAdmin(); UUID id = begin(request, true);
        return service.detail(validator.supplierId(supplierId, id), id);
    }
    @PostMapping(consumes = MediaType.APPLICATION_JSON_VALUE)
    ResponseEntity<Map<String, Object>> create(@RequestBody(required = false) String raw, HttpServletRequest request) {
        UserIdentity actor = currentUser.requireAdmin(); UUID id = begin(request, true);
        return ResponseEntity.status(201).body(service.create(validator.create(validator.parse(raw, id), id), actor.userId(), id));
    }
    @PatchMapping(value = "/{supplierId}", consumes = MediaType.APPLICATION_JSON_VALUE)
    Map<String, Object> patch(@PathVariable String supplierId, @RequestBody(required = false) String raw, HttpServletRequest request) {
        UserIdentity actor = currentUser.requireAdmin(); UUID id = begin(request, true);
        return service.patch(validator.supplierId(supplierId, id), validator.patch(validator.parse(raw, id), id), actor.userId(), id);
    }
    @PutMapping(value = "/{supplierId}/capabilities", consumes = MediaType.APPLICATION_JSON_VALUE)
    Map<String, Object> capabilities(@PathVariable String supplierId, @RequestBody(required = false) String raw, HttpServletRequest request) {
        UserIdentity actor = currentUser.requireAdmin(); UUID id = begin(request, true);
        return service.capabilities(validator.supplierId(supplierId, id), validator.capabilitiesRequest(validator.parse(raw, id), id), actor.userId(), id);
    }
    @PutMapping(value = "/{supplierId}/quality", consumes = MediaType.APPLICATION_JSON_VALUE)
    Map<String, Object> quality(@PathVariable String supplierId, @RequestBody(required = false) String raw, HttpServletRequest request) {
        UserIdentity actor = currentUser.requireAdmin(); UUID id = begin(request, true);
        return service.quality(validator.supplierId(supplierId, id), validator.qualityRequest(validator.parse(raw, id), id), actor.userId(), id);
    }
    @PatchMapping(value = "/{supplierId}/status", consumes = MediaType.APPLICATION_JSON_VALUE)
    Map<String, Object> status(@PathVariable String supplierId, @RequestBody(required = false) String raw, HttpServletRequest request) {
        UserIdentity actor = currentUser.requireAdmin(); UUID id = begin(request, true);
        return service.status(validator.supplierId(supplierId, id), validator.statusRequest(validator.parse(raw, id), id), actor.userId(), id);
    }
    private UUID begin(HttpServletRequest request, boolean noParameters) {
        UUID id = UUID.randomUUID(); request.setAttribute("request_id", id);
        if (noParameters && !request.getParameterMap().isEmpty()) throw ApiException.validation(id);
        return id;
    }
}
