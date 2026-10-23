package com.negopilot.backend.negotiation;

import java.util.ArrayList;
import java.util.List;
import java.util.Set;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

@Repository
class NegotiationRepository {
    private static final Set<String> TABLES = Set.of("neg_workspaces", "neg_review_revisions", "neg_proposals",
            "neg_proposal_revisions", "neg_validations", "neg_export_bundles", "neg_export_files", "neg_requests", "neg_mail_sends");
    private final JdbcTemplate jdbc;
    private final JsonMapper mapper;

    NegotiationRepository(JdbcTemplate jdbc, JsonMapper mapper) { this.jdbc = jdbc; this.mapper = mapper; }

    List<ObjectNode> rows(String sql, Object... args) {
        return jdbc.query(sql, (rs, n) -> (ObjectNode) mapper.readTree(rs.getString(1)), args);
    }

    ObjectNode one(String sql, Object... args) {
        var result = rows(sql, args);
        return result.isEmpty() ? null : result.getFirst();
    }

    int execute(String sql, Object... args) { return jdbc.update(sql, args); }

    // 열 이름은 서비스에서 만든 저장 행만 사용한다. 값 변환은 PostgreSQL 행 타입에 맡긴다.
    void insert(String table, ObjectNode row) {
        String columns = columns(table, row);
        execute("INSERT INTO " + table + " (" + columns + ") SELECT " + columns
                + " FROM jsonb_populate_record(NULL::" + table + ", ?::jsonb)", mapper.writeValueAsString(row));
    }

    int update(String table, ObjectNode row, String where, Object... args) {
        columns(table, row);
        List<String> assignments = row.propertyNames().stream().map(key -> key + " = v." + key).toList();
        List<Object> values = new ArrayList<>();
        values.add(mapper.writeValueAsString(row));
        values.addAll(List.of(args));
        return execute("UPDATE " + table + " t SET " + String.join(", ", assignments)
                + " FROM jsonb_populate_record(NULL::" + table + ", ?::jsonb) v WHERE " + where, values.toArray());
    }

    private String columns(String table, ObjectNode row) {
        if (!TABLES.contains(table) || row.isEmpty() || row.propertyNames().stream().anyMatch(k -> !k.matches("[a-z_][a-z0-9_]*"))) {
            throw new IllegalArgumentException("저장할 테이블과 열을 확인하세요");
        }
        return String.join(", ", row.propertyNames());
    }
}
