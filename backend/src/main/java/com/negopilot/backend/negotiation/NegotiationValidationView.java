package com.negopilot.backend.negotiation;

import java.time.OffsetDateTime;
import java.util.Comparator;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.JsonNodeFactory;
import tools.jackson.databind.node.ObjectNode;

public final class NegotiationValidationView {
    private static final JsonNodeFactory JSON = JsonNodeFactory.instance;
    private static final List<String> ARRAY_FIELDS = List.of(
            "scope", "limitations", "warnings", "violations", "unresolved_issue_ids");
    private static final Comparator<ObjectNode> LATEST = Comparator
            .comparing((ObjectNode row) -> OffsetDateTime.parse(row.path("started_at").asString()).toInstant())
            .thenComparing(row -> row.path("id").asString(""));

    public static ObjectNode calculate(int head, List<ObjectNode> revisions,
                                       List<ObjectNode> validations, OffsetDateTime now) {
        ObjectNode checking = validations.stream()
                .filter(row -> row.path("revision").asInt() == head)
                .filter(row -> "checking".equals(row.path("status").asString()))
                .filter(row -> !row.path("lease_until").isNull()
                        && OffsetDateTime.parse(row.path("lease_until").asString()).isAfter(now))
                .max(LATEST).orElse(null);
        if (checking != null) return execution(checking, now);

        ObjectNode latest = validations.stream()
                .filter(row -> row.path("revision").asInt() == head).max(LATEST).orElse(null);
        if (latest != null) return execution(latest, now);

        // 이전 revision 중 실행 기록이 있는 가장 최근 것의 최신 실행만 본다. 그 실행이 checked가 아니면
        // 더 예전의 checked 결과로 거슬러 올라가 이어받지 않는다.
        ObjectNode lastRun = validations.stream()
                .filter(row -> row.path("revision").asInt() < head)
                .max(Comparator.comparingInt((ObjectNode row) -> row.path("revision").asInt()).thenComparing(LATEST))
                .orElse(null);
        if (lastRun == null) return empty("not_checked");
        if (!"checked".equals(lastRun.path("status").asString())) return empty("recheck_required");
        ObjectNode checked = lastRun;

        int checkedRevision = checked.path("revision").asInt();
        Set<String> scopes = new LinkedHashSet<>();
        Set<String> paths = new LinkedHashSet<>();
        revisions.stream().filter(row -> row.path("revision").asInt() > checkedRevision
                        && row.path("revision").asInt() <= head)
                .sorted(Comparator.comparingInt(row -> row.path("revision").asInt()))
                .forEach(row -> {
                    row.path("changed_scopes").forEach(scope -> scopes.add(scope.asString()));
                    row.path("change_set").forEach(change -> paths.add(change.path("path").asString()));
                });
        if (Set.of("INTERNAL_TEXT").containsAll(scopes)) {
            ObjectNode result = execution(checked, now);
            result.put("inherited_from", checkedRevision);
            ArrayNode limitations = (ArrayNode) result.get("limitations");
            String notice = "사용자가 수정한 내부 문구는 검사하지 않았습니다";
            if (!contains(limitations, notice)) limitations.add(notice);
            return result;
        }
        ObjectNode result = empty("recheck_required");
        result.put("checked_revision", checkedRevision);
        result.set("previous", execution(checked, now));
        ArrayNode changedFields = result.putArray("changed_fields");
        paths.forEach(changedFields::add);
        return result;
    }

    private static ObjectNode execution(ObjectNode row, OffsetDateTime now) {
        String status = row.path("status").asString();
        boolean stale = "checking".equals(status)
                && !OffsetDateTime.parse(row.path("lease_until").asString()).isAfter(now);
        ObjectNode result = empty(stale ? "failed" : status);
        result.put("checked_revision", row.path("revision").asInt());
        if ("checked".equals(status)) result.set("conclusion", row.path("conclusion").deepCopy());
        for (String field : ARRAY_FIELDS) {
            if (row.path(field).isArray()) result.set(field, row.get(field).deepCopy());
        }
        if (row.has("text_findings")) result.set("text_findings", row.get("text_findings").deepCopy());
        if (stale) result.put("error_code", "STALE_CHECK");
        else if (row.hasNonNull("error_code")) result.set("error_code", row.get("error_code").deepCopy());
        return result;
    }

    private static ObjectNode empty(String status) {
        ObjectNode result = JSON.objectNode().put("status", status);
        result.putNull("checked_revision");
        result.putNull("conclusion");
        ARRAY_FIELDS.forEach(result::putArray);
        result.putNull("text_findings");
        return result;
    }

    private static boolean contains(ArrayNode nodes, String value) {
        for (JsonNode node : nodes) if (value.equals(node.asString())) return true;
        return false;
    }
}
