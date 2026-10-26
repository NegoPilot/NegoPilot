package com.negopilot.backend.legalreview;

import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

/**
 * DB 없이 서비스와 실행기의 저장 경계를 검증한다. 상태 전이 조건은 JDBC 저장소의 WHERE 절과 같다.
 * reviews와 heads는 협상 작업의 neg_review_revisions와 review_head_revision을 흉내 낸다.
 */
final class MemoryUploadStore extends LegalReviewUploadStore {
    final Map<UUID, ObjectNode> uploads = new LinkedHashMap<>();
    final Map<Key, ObjectNode> rows = new LinkedHashMap<>();
    final Map<String, ObjectNode> reviews = new HashMap<>();
    final Map<UUID, Integer> heads = new HashMap<>();

    MemoryUploadStore(JsonMapper mapper) { super(null, mapper); }

    @Override void insertUpload(ObjectNode upload) {
        ObjectNode copy = upload.deepCopy();
        copy.put("created_at", OffsetDateTime.now().toString());
        uploads.put(UUID.fromString(copy.get("id").stringValue()), copy);
    }

    @Override void insertRow(ObjectNode row) {
        Key key = new Key(UUID.fromString(row.get("upload_id").stringValue()), row.get("row_number").intValue());
        ObjectNode copy = row.deepCopy();
        copy.put("created_at", OffsetDateTime.now().toString()).put("attempts", 0);
        rows.put(key, copy);
    }

    @Override ObjectNode upload(UUID id, UUID owner) {
        ObjectNode upload = uploads.get(id);
        return upload != null && upload.get("owner_id").stringValue().equals(owner.toString()) ? upload.deepCopy() : null;
    }

    @Override List<ObjectNode> uploads(UUID owner, int limit) {
        return uploads.values().stream().filter(u -> u.get("owner_id").stringValue().equals(owner.toString()))
                .map(ObjectNode::deepCopy).toList().reversed().stream().limit(limit).toList();
    }

    @Override List<ObjectNode> rows(UUID uploadId) {
        return rows.keySet().stream().filter(k -> k.uploadId().equals(uploadId))
                .sorted((a, b) -> Integer.compare(a.rowNumber(), b.rowNumber())).map(this::row).toList();
    }

    @Override ObjectNode row(Key key) {
        ObjectNode row = rows.get(key);
        if (row == null) return null;
        ObjectNode view = row.deepCopy();
        view.put("file_name", uploads.get(key.uploadId()).get("file_name").stringValue());
        if (row.path("negotiation_id").isString()) {
            UUID workspace = UUID.fromString(row.get("negotiation_id").stringValue());
            view.put("review_head_revision", heads.getOrDefault(workspace, 0));
            ObjectNode review = row.path("review_revision").isNumber() ? reviews.get(workspace + "|" + row.get("review_revision").intValue()) : null;
            if (review == null) view.putNull("review"); else view.set("review", review.deepCopy());
        } else {
            view.putNull("review_head_revision");
            view.putNull("review");
        }
        return view;
    }

    @Override boolean claim(Key key, OffsetDateTime leaseUntil, UUID requestId) {
        ObjectNode row = rows.get(key);
        if (row == null || !row.get("status").stringValue().equals("QUEUED")) return false;
        row.put("status", "RUNNING").put("lease_until", leaseUntil.toString()).put("request_id", requestId.toString())
                .put("attempts", row.get("attempts").intValue() + 1).put("started_at", OffsetDateTime.now().toString());
        row.remove(List.of("error_code", "error_retryable", "execution_status", "finished_at"));
        return true;
    }

    @Override void attachWorkspace(Key key, UUID negotiationId) {
        ObjectNode row = rows.get(key);
        if (row != null && row.get("status").stringValue().equals("RUNNING")) row.put("negotiation_id", negotiationId.toString());
    }

    @Override boolean finish(Key key, ObjectNode result) {
        ObjectNode row = rows.get(key);
        if (row == null || !row.get("status").stringValue().equals("RUNNING")) return false;
        row.setAll(result.deepCopy());
        row.put("finished_at", OffsetDateTime.now().toString());
        row.remove("lease_until");
        return true;
    }

    @Override boolean requeue(Key key) {
        ObjectNode row = rows.get(key);
        if (row == null || !row.get("status").stringValue().equals("FAILED")) return false;
        row.put("status", "QUEUED");
        row.remove(List.of("error_code", "error_retryable", "execution_status", "started_at", "finished_at"));
        return true;
    }

    @Override int expireRunning() {
        int count = 0;
        for (ObjectNode row : rows.values()) {
            if (!row.get("status").stringValue().equals("RUNNING")) continue;
            if (OffsetDateTime.parse(row.get("lease_until").stringValue()).isAfter(OffsetDateTime.now())) continue;
            row.put("status", "FAILED").put("error_code", "STALE_REVIEW").put("error_retryable", true);
            row.remove("lease_until");
            count++;
        }
        return count;
    }

    @Override List<Key> queued() {
        List<Key> keys = new ArrayList<>();
        rows.forEach((k, v) -> { if (v.get("status").stringValue().equals("QUEUED")) keys.add(k); });
        return keys;
    }

    @Override void reject(Key key) {
        ObjectNode row = rows.get(key);
        if (row != null && row.get("status").stringValue().equals("QUEUED")) {
            row.put("status", "FAILED").put("error_code", "QUEUE_FULL").put("error_retryable", true);
        }
    }
}
