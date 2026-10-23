package com.negopilot.backend.negotiation;

import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ObjectNode;
import static com.negopilot.backend.negotiation.NegotiationFixtures.*;

/** DB 연결 없이 서비스의 저장 경계와 응답을 검증한다. */
final class MemoryNegotiationRepository extends NegotiationRepository {
    final Map<String, List<ObjectNode>> tables = new HashMap<>();
    ObjectNode member = object("{\"name\":\"세션 회원\",\"department\":\"구매팀\",\"job_title\":\"매니저\",\"email\":\"session@example.com\",\"status\":\"ACTIVE\"}");

    MemoryNegotiationRepository() { super(null, JSON); }
    List<ObjectNode> table(String name) { return tables.computeIfAbsent(name, ignored -> new ArrayList<>()); }

    @Override List<ObjectNode> rows(String sql, Object... args) {
        if (sql.contains("FROM app_users")) return List.of(member.deepCopy());
        if (sql.contains("JOIN neg_export_bundles")) {
            return table("neg_export_files").stream().filter(f -> eq(f, "id", args[0])).filter(f -> {
                ObjectNode bundle = find("neg_export_bundles", "id", f.get("bundle_id"));
                ObjectNode proposal = find("neg_proposals", "id", bundle.get("proposal_id"));
                ObjectNode workspace = find("neg_workspaces", "id", proposal.get("workspace_id"));
                return eq(workspace, "owner_id", args[1]) && eq(bundle, "status", "ready");
            }).map(ObjectNode::deepCopy).toList();
        }
        String table = tablesIn(sql);
        List<ObjectNode> matches = new ArrayList<>();
        for (ObjectNode row : table(table)) {
            boolean match = switch (table) {
                case "neg_workspaces" -> sql.contains("WHERE owner_id") ? eq(row, "owner_id", args[0])
                        : eq(row, "id", args[0]) && eq(row, "owner_id", args[1]);
                case "neg_review_revisions" -> eq(row, "workspace_id", args[0]) && eq(row, "revision", args[1]);
                case "neg_proposals" -> eq(row, "id", args[0]) && eq(row, "workspace_id", args[1]);
                case "neg_proposal_revisions" -> eq(row, "proposal_id", args[0])
                        && (args.length < 2 || eq(row, "revision", args[1]));
                case "neg_validations" -> sql.contains("WHERE id=?") ? eq(row, "id", args[0])
                        : eq(row, "proposal_id", args[0]) && (args.length < 2 || eq(row, "revision", args[1]))
                        && (!sql.contains("status='checking'") || eq(row, "status", "checking"));
                case "neg_export_bundles" -> sql.contains("WHERE id=?") ? eq(row, "id", args[0])
                        : eq(row, "proposal_id", args[0]) && eq(row, "revision", args[1]) && eq(row, "doc_status", args[2])
                        && row.get("formats").equals(JSON.readTree(args[3].toString()))
                        && Set.of("ready", "processing").contains(row.path("status").asText());
                case "neg_export_files" -> eq(row, "bundle_id", args[0]);
                case "neg_requests" -> eq(row, "user_id", args[0]) && eq(row, "idempotency_key", args[1])
                        && (args.length < 3 || eq(row, "lease_until", args[2]) && active(row));
                default -> throw new AssertionError(sql);
            };
            if (match) {
                ObjectNode copy = row.deepCopy();
                if (table.equals("neg_requests")) copy.put("lease_active", active(row));
                matches.add(copy);
            }
        }
        if (sql.contains("ORDER BY revision")) matches.sort(Comparator.comparingInt(row -> row.path("revision").intValue()));
        if (sql.contains("ORDER BY started_at DESC")) matches.sort(Comparator.comparing((ObjectNode row) -> row.path("started_at").asText()).reversed());
        return matches;
    }

    @Override void insert(String table, ObjectNode input) {
        ObjectNode row = input.deepCopy();
        String now = OffsetDateTime.now().toString();
        if (!row.has("created_at")) row.put("created_at", now);
        if (!row.has("updated_at")) row.put("updated_at", now);
        if (table.equals("neg_workspaces")) {
            row.put("status", "OPEN").put("row_version", 0).put("review_head_revision", 0)
                    .putNull("rfq_verified_at").putNull("current_proposal_id");
        }
        if (table.equals("neg_proposals")) row.put("status", "ACTIVE");
        if (table.equals("neg_proposal_revisions")) {
            if (!row.has("changed_scopes")) row.putArray("changed_scopes");
            if (!row.has("change_set")) row.putArray("change_set");
        }
        if (table.equals("neg_validations")) {
            if (!row.has("started_at")) row.put("started_at", now);
            for (String key : List.of("scope", "limitations", "warnings", "violations", "unresolved_issue_ids"))
                if (!row.has(key)) row.putArray(key);
        }
        table(table).add(row);
    }

    @Override int update(String table, ObjectNode values, String where, Object... args) {
        int count = 0;
        for (ObjectNode row : table(table)) if (eq(row, "id", args[0])) {
            if (where.contains("status='checking'") && !eq(row, "status", "checking")) continue;
            if (where.contains("status='processing'") && !eq(row, "status", "processing")) continue;
            if (where.contains("lease_until>") && !active(row)) continue;
            row.setAll(values.deepCopy()); count++;
        }
        return count;
    }

    @Override int execute(String sql, Object... args) {
        if (sql.startsWith("INSERT INTO neg_requests")) {
            if (request(args[0], args[1]) != null) return 0;
            table("neg_requests").add(JSON.createObjectNode().put("user_id", args[0].toString())
                    .put("idempotency_key", args[1].toString()).put("operation", args[2].toString())
                    .put("body_hash", args[3].toString()).put("state", "IN_PROGRESS")
                    .put("lease_until", OffsetDateTime.now().plusSeconds(((Number) args[4]).longValue()).toString()));
            return 1;
        }
        if (sql.startsWith("UPDATE neg_requests SET state='DONE'")) {
            ObjectNode row = request(args[2], args[3]);
            if (row == null || !active(row) || !eq(row, "lease_until", args[4])) return 0;
            row.put("state", "DONE").putNull("lease_until").put("response_status", ((Number) args[0]).intValue());
            row.set("response", JSON.readTree(args[1].toString())); return 1;
        }
        if (sql.startsWith("UPDATE neg_requests SET lease_until=")) {
            request(args[1], args[2]).put("lease_until", OffsetDateTime.now().plusSeconds(((Number) args[0]).longValue()).toString());
            return 1;
        }
        if (sql.startsWith("DELETE FROM neg_requests")) {
            return table("neg_requests").removeIf(row -> eq(row, "user_id", args[0]) && eq(row, "idempotency_key", args[1])
                    && eq(row, "state", "IN_PROGRESS") && eq(row, "lease_until", args[2])) ? 1 : 0;
        }
        if (sql.startsWith("UPDATE neg_proposals SET status='SUPERSEDED'")) {
            int count = 0;
            for (ObjectNode row : table("neg_proposals")) if (eq(row, "workspace_id", args[0]) && eq(row, "status", "ACTIVE")) {
                row.put("status", "SUPERSEDED"); count++;
            }
            return count;
        }
        if (sql.startsWith("UPDATE neg_validations SET status='failed'")) {
            int count = 0;
            for (ObjectNode row : table("neg_validations")) if (eq(row, "proposal_id", args[0]) && eq(row, "revision", args[1])
                    && eq(row, "status", "checking") && !active(row)) {
                row.put("status", "failed").put("error_code", "STALE_CHECK").put("finished_at", OffsetDateTime.now().toString()).putNull("lease_until"); count++;
            }
            return count;
        }
        throw new AssertionError(sql);
    }

    private ObjectNode request(Object user, Object key) {
        return table("neg_requests").stream().filter(row -> eq(row, "user_id", user) && eq(row, "idempotency_key", key)).findFirst().orElse(null);
    }
    private ObjectNode find(String table, String key, Object value) {
        return table(table).stream().filter(row -> eq(row, key, value)).findFirst().orElseThrow();
    }
    private static String tablesIn(String sql) {
        for (String table : List.of("neg_workspaces", "neg_review_revisions", "neg_proposal_revisions", "neg_proposals",
                "neg_validations", "neg_export_bundles", "neg_export_files", "neg_requests")) if (sql.contains("FROM " + table + " ")) return table;
        throw new AssertionError(sql);
    }
    private static boolean eq(ObjectNode row, String key, Object value) {
        return row.path(key).asText().equals(value instanceof JsonNode node ? node.asText() : String.valueOf(value));
    }
    private static boolean active(ObjectNode row) {
        return row.hasNonNull("lease_until") && OffsetDateTime.parse(row.path("lease_until").asText()).isAfter(OffsetDateTime.now());
    }
}
