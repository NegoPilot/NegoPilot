package com.negopilot.backend.admin.supplier;

import com.negopilot.backend.common.api.ApiException;
import java.sql.Timestamp;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import org.springframework.stereotype.Repository;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

@Repository
class AdminSupplierRepository {
    private static final Set<String> CERTIFICATIONS = Set.of("iatf16949", "iso14001");
    private final BusinessDataStore store;
    private final JsonMapper mapper;

    AdminSupplierRepository(BusinessDataStore store, JsonMapper mapper) {
        this.store = store;
        this.mapper = mapper;
    }

    List<Map<String, Object>> summaryCounts() {
        return store.jdbc().queryForList("SELECT source, status, count(*) AS n FROM suppliers GROUP BY source, status");
    }

    Map<String, Object> list(AdminSupplierValidator.Search filters) {
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
    }

    long nextManualSequence() {
        return store.jdbc().queryForObject("SELECT nextval('supplier_manual_id_seq')", Long.class);
    }

    int insertManualSupplier(String id, JsonNode profile, JsonNode cert) {
        return store.jdbc().update("""
                INSERT INTO suppliers (supplier_id,source,name,region,employees,revenue_eok,founded_year,
                    biz_no_masked,stock_code,induty_code,iatf16949,iso14001,profile_origin_source,
                    certification_source,capability_source,quality_source)
                VALUES (?,'MANUAL',?,?,?,?,?,?,?,?,?,?,'MANUAL','MANUAL','MANUAL','MANUAL')
                ON CONFLICT (supplier_id) DO NOTHING
                """, id, value(profile.get("name")), value(profile.get("region")), value(profile.get("employees")),
                value(profile.get("revenue_eok")), value(profile.get("founded_year")), value(profile.get("biz_no_masked")),
                value(profile.get("stock_code")), value(profile.get("induty_code")),
                value(cert.get("iatf16949")), value(cert.get("iso14001")));
    }

    void captureOriginalProfile(String id) {
        store.jdbc().update("UPDATE suppliers SET profile_original=to_jsonb(s)-'profile_original' FROM suppliers s WHERE suppliers.supplier_id=? AND s.supplier_id=suppliers.supplier_id", id);
    }

    String profileOriginSource(String id) {
        return store.jdbc().queryForObject("SELECT profile_origin_source FROM suppliers WHERE supplier_id=?", String.class, id);
    }

    void updateProfile(String id, JsonNode profile) {
        updateFields(id, profile);
        store.jdbc().update("UPDATE suppliers SET source='MANUAL' WHERE supplier_id=?", id);
    }

    void updateCertifications(String id, JsonNode certifications) {
        updateFields(id, certifications);
        store.jdbc().update("UPDATE suppliers SET certification_source='MANUAL' WHERE supplier_id=?", id);
    }

    void markCapabilitiesManual(String id) {
        store.jdbc().update("UPDATE suppliers SET capability_source='MANUAL' WHERE supplier_id=?", id);
    }

    void markQualityManual(String id) {
        store.jdbc().update("UPDATE suppliers SET quality_source='MANUAL' WHERE supplier_id=?", id);
    }

    void updateStatus(String id, String status) {
        store.jdbc().update("UPDATE suppliers SET status=? WHERE supplier_id=?", status, id);
    }

    Long lockVersion(String id) {
        List<Long> versions = store.jdbc().queryForList("SELECT version FROM suppliers WHERE supplier_id=? FOR UPDATE", Long.class, id);
        return versions.isEmpty() ? null : versions.getFirst();
    }

    void incrementVersion(String id) {
        store.jdbc().update("UPDATE suppliers SET version=version+1,updated_at=CURRENT_TIMESTAMP WHERE supplier_id=?", id);
    }

    private void updateFields(String id, JsonNode node) {
        List<String> assignments = new ArrayList<>(); List<Object> args = new ArrayList<>();
        for (String field : node.propertyNames()) {
            // Defense in depth: SQL identifiers come only from fixed server-side allowlists.
            if (!AdminSupplierValidator.PROFILE.contains(field) && !CERTIFICATIONS.contains(field))
                throw ApiException.validation(null);
            assignments.add(field + "=?"); args.add(value(node.get(field)));
        }
        args.add(id);
        store.jdbc().update("UPDATE suppliers SET " + String.join(",", assignments) + " WHERE supplier_id=?", args.toArray());
    }

    void replaceCapabilities(String id, JsonNode capabilities) {
        store.jdbc().update("DELETE FROM capabilities WHERE supplier_id=?", id);
        for (JsonNode row : capabilities) store.jdbc().update("INSERT INTO capabilities VALUES (?,?,?,?)", id,
                row.get("process").stringValue(), row.get("material").stringValue(), row.get("monthly_capacity").intValue());
    }

    void replaceQuality(String id, JsonNode quality) {
        store.jdbc().update("""
                INSERT INTO quality (supplier_id,defect_ppm,on_time_rate,audit_grade,years_dealing) VALUES (?,?,?,?,?)
                ON CONFLICT (supplier_id) DO UPDATE SET defect_ppm=EXCLUDED.defect_ppm,on_time_rate=EXCLUDED.on_time_rate,
                    audit_grade=EXCLUDED.audit_grade,years_dealing=EXCLUDED.years_dealing
                """, id, quality.get("defect_ppm").intValue(), quality.get("on_time_rate").decimalValue(),
                quality.get("audit_grade").stringValue(), quality.get("years_dealing").intValue());
    }

    Map<String, Object> findSnapshot(String id, boolean includeAudit) {
        List<Map<String, Object>> suppliers = rows("SELECT * FROM suppliers WHERE supplier_id=?", id);
        if (suppliers.isEmpty()) return null;
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

    void appendAudit(String id, UUID actor, String action, Map<String, Object> before, Map<String, Object> after) {
        store.jdbc().update("INSERT INTO supplier_change_events(supplier_id,actor_user_id,action,before_data,after_data) VALUES (?,?,?,?::jsonb,?::jsonb)",
                id, actor, action, before == null ? null : mapper.writeValueAsString(before), mapper.writeValueAsString(after));
    }

    private List<Map<String, Object>> rows(String sql, Object... args) {
        return store.jdbc().queryForList(sql, args).stream().map(raw -> {
            Map<String, Object> result = new LinkedHashMap<>();
            raw.forEach((key, value) -> result.put(key, value != null && Set.of("profile_original", "before_data", "after_data").contains(key) ? mapper.readTree(value.toString())
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
}
