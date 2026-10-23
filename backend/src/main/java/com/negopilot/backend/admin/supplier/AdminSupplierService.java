package com.negopilot.backend.admin.supplier;

import com.negopilot.backend.common.api.ApiException;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;
import java.util.function.Supplier;
import org.springframework.dao.DataAccessException;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.stereotype.Service;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ObjectNode;

@Service
public final class AdminSupplierService {
    private final BusinessDataStore store;
    private final AdminSupplierRepository repository;

    AdminSupplierService(BusinessDataStore store, AdminSupplierRepository repository) {
        this.store = store;
        this.repository = repository;
    }

    public Map<String, Object> summary() {
        return safe(null, () -> {
            Map<String, Long> sources = new LinkedHashMap<>(Map.of("DART", 0L, "MOCK", 0L, "MANUAL", 0L));
            Map<String, Long> statuses = new LinkedHashMap<>(Map.of("ACTIVE", 0L, "INACTIVE", 0L));
            long total = 0;
            for (Map<String, Object> row : repository.summaryCounts()) {
                long n = ((Number) row.get("n")).longValue(); total += n;
                sources.merge((String) row.get("source"), n, Long::sum);
                statuses.merge((String) row.get("status"), n, Long::sum);
            }
            return Map.of("total", total, "by_source", sources, "by_status", statuses);
        });
    }

    Map<String, Object> list(AdminSupplierValidator.Search filters, UUID requestId) {
        return safe(requestId, () -> repository.list(filters));
    }

    Map<String, Object> detail(String supplierId, UUID requestId) {
        return safe(requestId, () -> snapshot(supplierId, requestId, true));
    }

    Map<String, Object> create(ObjectNode body, UUID actor, UUID requestId) {
        return safe(requestId, () -> store.transaction().execute(status -> {
            JsonNode profile = body.get("profile"), cert = body.get("certifications");
            String id;
            // Sequence values are never reused. Retry IDs imported after migration without resetting the sequence.
            while (true) {
                long number = repository.nextManualSequence();
                id = "SUP-" + String.format(java.util.Locale.ROOT, "%04d", number);
                int inserted = repository.insertManualSupplier(id, profile, cert);
                if (inserted == 1) break;
            }
            repository.replaceCapabilities(id, body.get("capabilities"));
            if (body.hasNonNull("quality")) repository.replaceQuality(id, body.get("quality"));
            repository.captureOriginalProfile(id);
            Map<String, Object> after = snapshot(id, requestId, false);
            repository.appendAudit(id, actor, "CREATE", null, after);
            return snapshot(id, requestId, true);
        }));
    }

    Map<String, Object> patch(String id, ObjectNode body, UUID actor, UUID requestId) {
        return mutate(id, body.get("version").longValue(), actor, requestId, "PROFILE_CERTIFICATIONS", () -> {
            if (body.has("profile")) {
                String origin = repository.profileOriginSource(id);
                if ("DART".equals(origin)) throw ApiException.of(requestId, 403, "READ_ONLY_SOURCE", "DART profile data cannot be edited.", false);
                repository.updateProfile(id, body.get("profile"));
            }
            if (body.has("certifications")) {
                repository.updateCertifications(id, body.get("certifications"));
            }
        });
    }

    Map<String, Object> capabilities(String id, ObjectNode body, UUID actor, UUID requestId) {
        return mutate(id, body.get("version").longValue(), actor, requestId, "CAPABILITIES", () -> {
            repository.replaceCapabilities(id, body.get("capabilities"));
            repository.markCapabilitiesManual(id);
        });
    }

    Map<String, Object> quality(String id, ObjectNode body, UUID actor, UUID requestId) {
        return mutate(id, body.get("version").longValue(), actor, requestId, "QUALITY", () -> {
            repository.replaceQuality(id, body.get("quality"));
            repository.markQualityManual(id);
        });
    }

    Map<String, Object> status(String id, ObjectNode body, UUID actor, UUID requestId) {
        return mutate(id, body.get("version").longValue(), actor, requestId, "STATUS", () ->
                repository.updateStatus(id, body.get("status").stringValue()));
    }

    private Map<String, Object> mutate(String id, long version, UUID actor, UUID requestId, String action, Runnable change) {
        // Use the repository's business pool so data changes and audit remain in the same transaction.
        return safe(requestId, () -> store.transaction().execute(status -> {
            Long currentVersion = repository.lockVersion(id);
            if (currentVersion == null) throw ApiException.supplierNotFound(requestId);
            if (currentVersion != version) throw ApiException.of(requestId, 409, "VERSION_CONFLICT", "Supplier has changed. Reload it before saving.", false);
            Map<String, Object> before = snapshot(id, requestId, false);
            change.run();
            repository.incrementVersion(id);
            Map<String, Object> after = snapshot(id, requestId, false);
            repository.appendAudit(id, actor, action, before, after);
            return snapshot(id, requestId, true);
        }));
    }

    private Map<String, Object> snapshot(String id, UUID requestId, boolean includeAudit) {
        Map<String, Object> result = repository.findSnapshot(id, includeAudit);
        if (result == null) throw ApiException.supplierNotFound(requestId);
        return result;
    }

    private <T> T safe(UUID requestId, Supplier<T> work) {
        try { return work.get(); }
        catch (DataIntegrityViolationException ex) { throw ApiException.of(requestId, 409, "DATA_CONFLICT", "Supplier data conflicts with an existing record or constraint.", false); }
        catch (DataAccessException ex) { throw ApiException.of(requestId, 503, "BUSINESS_DATA_UNAVAILABLE", "Business data is unavailable.", true); }
    }
}
