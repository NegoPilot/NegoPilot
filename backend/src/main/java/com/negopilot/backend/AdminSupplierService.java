package com.negopilot.backend;

import java.sql.Timestamp;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.function.Supplier;
import org.springframework.dao.DataAccessException;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.stereotype.Service;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

@Service
final class AdminSupplierService {
    private final BusinessDataStore store;
    private final JsonMapper mapper;
    AdminSupplierService(BusinessDataStore store, JsonMapper mapper) { this.store = store; this.mapper = mapper; }

    Map<String, Object> summary() {
        return safe(null, () -> {
            Map<String, Long> sources = new LinkedHashMap<>(Map.of("DART", 0L, "MOCK", 0L, "MANUAL", 0L));
            Map<String, Long> statuses = new LinkedHashMap<>(Map.of("ACTIVE", 0L, "INACTIVE", 0L));
            long total = 0;
            for (Map<String, Object> row : store.jdbc().queryForList("SELECT source, status, count(*) AS n FROM suppliers GROUP BY source, status")) {
                long n = ((Number) row.get("n")).longValue(); total += n;
                sources.merge((String) row.get("source"), n, Long::sum);
                statuses.merge((String) row.get("status"), n, Long::sum);
            }
            return Map.of("total", total, "by_source", sources, "by_status", statuses);
        });
    }

    Map<String, Object> list(AdminSupplierValidator.Search filters, UUID requestId) {
        return safe(requestId, () -> {
            StringBuilder where = new StringBuilder(" WHERE true");
            List<Object> args = new ArrayList<>();
            if (filters.q() != null) {
                where.append(" AND (s.name ILIKE ? ESCAPE '!' OR s.supplier_id ILIKE ? ESCAPE '!')");
                String literal = "%" + filters.q().replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%";
                args.add(literal); args.add(literal);
            }
            if (filters.source() != null) { where.append(" AND s.source=?"); args.add(filters.source()); }
            if (filters.status() != null) { where.append(" AND s.status=?"); args.add(filters.status()); }
            long total = store.jdbc().queryForObject("SELECT count(*) FROM suppliers s" + where, Long.class, args.toArray());
            args.add(filters.size()); args.add((long) filters.page() * filters.size());
            List<Map<String, Object>> items = rows("""
                    SELECT s.supplier_id,s.name,s.region,s.source,s.status,s.version,s.iatf16949,s.iso14001,
                           s.certification_source,s.capability_source,s.quality_source,s.updated_at,
                           q.defect_ppm,q.on_time_rate,
                           (SELECT count(*) FROM capabilities c WHERE c.supplier_id=s.supplier_id) AS capability_count
                    FROM suppliers s LEFT JOIN quality q USING (supplier_id)
                    """ + where + " ORDER BY s.supplier_id LIMIT ? OFFSET ?", args.toArray());
            return Map.of("items", items, "total", total, "page", filters.page(), "size", filters.size());
        });
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
                long number = store.jdbc().queryForObject("SELECT nextval('supplier_manual_id_seq')", Long.class);
                id = "SUP-" + String.format(java.util.Locale.ROOT, "%04d", number);
                int inserted = store.jdbc().update("""
                        INSERT INTO suppliers (supplier_id,source,name,region,employees,revenue_eok,founded_year,
                            biz_no_masked,stock_code,induty_code,iatf16949,iso14001,profile_origin_source,
                            certification_source,capability_source,quality_source)
                        VALUES (?,'MANUAL',?,?,?,?,?,?,?,?,?,?,'MANUAL','MANUAL','MANUAL','MANUAL')
                        ON CONFLICT (supplier_id) DO NOTHING
                        """, id, value(profile.get("name")), value(profile.get("region")), value(profile.get("employees")),
                        value(profile.get("revenue_eok")), value(profile.get("founded_year")), value(profile.get("biz_no_masked")),
                        value(profile.get("stock_code")), value(profile.get("induty_code")),
                        value(cert.get("iatf16949")), value(cert.get("iso14001")));
                if (inserted == 1) break;
            }
            replaceCapabilities(id, body.get("capabilities"));
            if (body.hasNonNull("quality")) replaceQuality(id, body.get("quality"));
            store.jdbc().update("UPDATE suppliers SET profile_original=to_jsonb(s)-'profile_original' FROM suppliers s WHERE suppliers.supplier_id=? AND s.supplier_id=suppliers.supplier_id", id);
            Map<String, Object> after = snapshot(id, requestId, false);
            audit(id, actor, "CREATE", null, after);
            return snapshot(id, requestId, true);
        }));
    }

    Map<String, Object> patch(String id, ObjectNode body, UUID actor, UUID requestId) {
        return mutate(id, body.get("version").longValue(), actor, requestId, "PROFILE_CERTIFICATIONS", () -> {
            if (body.has("profile")) {
                String origin = store.jdbc().queryForObject("SELECT profile_origin_source FROM suppliers WHERE supplier_id=?", String.class, id);
                if ("DART".equals(origin)) throw ApiException.of(requestId, 403, "READ_ONLY_SOURCE", "DART profile data cannot be edited.", false);
                updateFields(id, body.get("profile"));
                store.jdbc().update("UPDATE suppliers SET source='MANUAL' WHERE supplier_id=?", id);
            }
            if (body.has("certifications")) {
                updateFields(id, body.get("certifications"));
                store.jdbc().update("UPDATE suppliers SET certification_source='MANUAL' WHERE supplier_id=?", id);
            }
        });
    }

    Map<String, Object> capabilities(String id, ObjectNode body, UUID actor, UUID requestId) {
        return mutate(id, body.get("version").longValue(), actor, requestId, "CAPABILITIES", () -> {
            replaceCapabilities(id, body.get("capabilities"));
            store.jdbc().update("UPDATE suppliers SET capability_source='MANUAL' WHERE supplier_id=?", id);
        });
    }

    Map<String, Object> quality(String id, ObjectNode body, UUID actor, UUID requestId) {
        return mutate(id, body.get("version").longValue(), actor, requestId, "QUALITY", () -> {
            replaceQuality(id, body.get("quality"));
            store.jdbc().update("UPDATE suppliers SET quality_source='MANUAL' WHERE supplier_id=?", id);
        });
    }

    Map<String, Object> status(String id, ObjectNode body, UUID actor, UUID requestId) {
        return mutate(id, body.get("version").longValue(), actor, requestId, "STATUS", () ->
                store.jdbc().update("UPDATE suppliers SET status=? WHERE supplier_id=?", body.get("status").stringValue(), id));
    }

    private Map<String, Object> mutate(String id, long version, UUID actor, UUID requestId, String action, Runnable change) {
        return safe(requestId, () -> store.transaction().execute(status -> {
            List<Long> versions = store.jdbc().queryForList("SELECT version FROM suppliers WHERE supplier_id=? FOR UPDATE", Long.class, id);
            if (versions.isEmpty()) throw ApiException.supplierNotFound(requestId);
            if (versions.getFirst() != version) throw ApiException.of(requestId, 409, "VERSION_CONFLICT", "Supplier has changed. Reload it before saving.", false);
            Map<String, Object> before = snapshot(id, requestId, false);
            change.run();
            store.jdbc().update("UPDATE suppliers SET version=version+1,updated_at=CURRENT_TIMESTAMP WHERE supplier_id=?", id);
            Map<String, Object> after = snapshot(id, requestId, false);
            audit(id, actor, action, before, after);
            return snapshot(id, requestId, true);
        }));
    }

    private void updateFields(String id, JsonNode node) {
        List<String> assignments = new ArrayList<>(); List<Object> args = new ArrayList<>();
        for (String field : node.propertyNames()) {
            // Defense in depth: SQL identifiers come only from fixed server-side allowlists.
            if (!AdminSupplierValidator.PROFILE.contains(field) && !SetHolder.CERTIFICATIONS.contains(field))
                throw ApiException.validation(null);
            assignments.add(field + "=?"); args.add(value(node.get(field)));
        }
        args.add(id);
        store.jdbc().update("UPDATE suppliers SET " + String.join(",", assignments) + " WHERE supplier_id=?", args.toArray());
    }

    private void replaceCapabilities(String id, JsonNode capabilities) {
        store.jdbc().update("DELETE FROM capabilities WHERE supplier_id=?", id);
        for (JsonNode row : capabilities) store.jdbc().update("INSERT INTO capabilities VALUES (?,?,?,?)", id,
                row.get("process").stringValue(), row.get("material").stringValue(), row.get("monthly_capacity").intValue());
    }
    private void replaceQuality(String id, JsonNode quality) {
        store.jdbc().update("""
                INSERT INTO quality (supplier_id,defect_ppm,on_time_rate,audit_grade,years_dealing) VALUES (?,?,?,?,?)
                ON CONFLICT (supplier_id) DO UPDATE SET defect_ppm=EXCLUDED.defect_ppm,on_time_rate=EXCLUDED.on_time_rate,
                    audit_grade=EXCLUDED.audit_grade,years_dealing=EXCLUDED.years_dealing
                """, id, quality.get("defect_ppm").intValue(), quality.get("on_time_rate").decimalValue(),
                quality.get("audit_grade").stringValue(), quality.get("years_dealing").intValue());
    }

    private Map<String, Object> snapshot(String id, UUID requestId, boolean includeAudit) {
        List<Map<String, Object>> suppliers = rows("SELECT * FROM suppliers WHERE supplier_id=?", id);
        if (suppliers.isEmpty()) throw ApiException.supplierNotFound(requestId);
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("supplier", suppliers.getFirst());
        result.put("capabilities", rows("SELECT process,material,monthly_capacity FROM capabilities WHERE supplier_id=? ORDER BY process,material", id));
        List<Map<String, Object>> quality = rows("SELECT defect_ppm,on_time_rate,audit_grade,years_dealing FROM quality WHERE supplier_id=?", id);
        result.put("quality", quality.isEmpty() ? null : quality.getFirst());
        result.put("financials", rows("SELECT bsns_year,fs_div,source,revenue,operating_income,net_income,total_assets,total_liabilities,total_equity FROM financials WHERE supplier_id=? ORDER BY bsns_year DESC", id));
        List<Map<String, Object>> contracts = rows("SELECT payment_days,price_linkage,raw_material_ratio,auto_renewal FROM contracts WHERE supplier_id=?", id);
        result.put("contract", contracts.isEmpty() ? null : contracts.getFirst());
        result.put("quotes", rows("SELECT quote_id,process,material,unit_price,quoted_at FROM quotes WHERE supplier_id=? ORDER BY quoted_at DESC,quote_id DESC", id));
        if (includeAudit) result.put("audit", rows("SELECT event_id,actor_user_id,action,before_data,after_data,created_at FROM supplier_change_events WHERE supplier_id=? ORDER BY event_id DESC LIMIT 100", id));
        return result;
    }

    private void audit(String id, UUID actor, String action, Map<String, Object> before, Map<String, Object> after) {
        store.jdbc().update("INSERT INTO supplier_change_events(supplier_id,actor_user_id,action,before_data,after_data) VALUES (?,?,?,?::jsonb,?::jsonb)",
                id, actor, action, before == null ? null : mapper.writeValueAsString(before), mapper.writeValueAsString(after));
    }
    private List<Map<String, Object>> rows(String sql, Object... args) {
        return store.jdbc().queryForList(sql, args).stream().map(raw -> {
            Map<String, Object> result = new LinkedHashMap<>();
            raw.forEach((key, value) -> result.put(key, value != null && java.util.Set.of("profile_original", "before_data", "after_data").contains(key) ? mapper.readTree(value.toString())
                    : value instanceof Timestamp ts ? ts.toInstant().toString()
                    : value instanceof java.sql.Date date ? date.toLocalDate().toString()
                    : value instanceof UUID uuid ? uuid.toString() : value));
            return result;
        }).toList();
    }
    private Object value(JsonNode node) {
        if (node == null || node.isNull()) return null;
        if (node.isBoolean()) return node.booleanValue();
        if (node.isIntegralNumber()) return node.longValue();
        return node.stringValue();
    }
    private <T> T safe(UUID requestId, Supplier<T> work) {
        try { return work.get(); }
        catch (DataIntegrityViolationException ex) { throw ApiException.of(requestId, 409, "DATA_CONFLICT", "Supplier data conflicts with an existing record or constraint.", false); }
        catch (DataAccessException ex) { throw ApiException.of(requestId, 503, "BUSINESS_DATA_UNAVAILABLE", "Business data is unavailable.", true); }
    }
    private static final class SetHolder { static final java.util.Set<String> CERTIFICATIONS = java.util.Set.of("iatf16949", "iso14001"); }
}
