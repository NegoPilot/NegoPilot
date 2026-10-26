package com.negopilot.backend.legalreview;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.UUID;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

/**
 * 업로드 건과 엑셀 행 저장. 검토 결과는 연결한 협상 작업의 neg_review_revisions에서 읽는다(review_token 열은 읽지 않는다).
 * 행은 to_jsonb로 읽고 jsonb_populate_record로 쓴다.
 */
@Repository
class LegalReviewUploadStore {
    record Key(UUID uploadId, int rowNumber) { }

    private static final String KEY = "upload_id=? AND row_number=?";
    // 행 + 이 업로드로 저장한 검토 + 작업의 현재 최신 검토 버전
    private static final String VIEW = "SELECT to_jsonb(r) || jsonb_build_object("
            + "'review', CASE WHEN v.workspace_id IS NULL THEN NULL ELSE jsonb_build_object('response', v.response,"
            + " 'eligibility', v.eligibility, 'assessment_status', v.assessment_status, 'execution_status', v.execution_status,"
            + " 'input_snapshot', v.input_snapshot, 'created_at', v.created_at) END,"
            + " 'review_head_revision', w.review_head_revision, 'file_name', u.file_name)"
            + " FROM legal_review_rows r JOIN legal_review_uploads u ON u.id = r.upload_id"
            + " LEFT JOIN neg_review_revisions v ON v.workspace_id = r.negotiation_id AND v.revision = r.review_revision"
            + " LEFT JOIN neg_workspaces w ON w.id = r.negotiation_id";
    private final JdbcTemplate jdbc;
    private final JsonMapper mapper;

    LegalReviewUploadStore(JdbcTemplate jdbc, JsonMapper mapper) { this.jdbc = jdbc; this.mapper = mapper; }

    void insertUpload(ObjectNode upload) { insert("legal_review_uploads", upload); }

    void insertRow(ObjectNode row) { insert("legal_review_rows", row); }

    ObjectNode upload(UUID id, UUID owner) {
        return one("SELECT to_jsonb(u) FROM legal_review_uploads u WHERE id=? AND owner_id=?", id, owner);
    }

    List<ObjectNode> uploads(UUID owner, int limit) {
        return rows("SELECT to_jsonb(u) FROM legal_review_uploads u WHERE owner_id=? ORDER BY created_at DESC, id DESC LIMIT ?",
                owner, limit);
    }

    List<ObjectNode> rows(UUID uploadId) {
        return rows(VIEW + " WHERE r.upload_id=? ORDER BY r.row_number", uploadId);
    }

    ObjectNode row(Key key) {
        return one(VIEW + " WHERE r.upload_id=? AND r.row_number=?", key.uploadId(), key.rowNumber());
    }

    /** QUEUED → RUNNING. 이번 실행의 request_id를 남긴다. 다른 실행기가 먼저 가져갔으면 false. */
    boolean claim(Key key, OffsetDateTime leaseUntil, UUID requestId) {
        return jdbc.update("UPDATE legal_review_rows SET status='RUNNING', started_at=clock_timestamp(), finished_at=NULL,"
                + " lease_until=?, request_id=?, attempts=attempts+1, error_code=NULL, error_retryable=NULL, execution_status=NULL"
                + " WHERE " + KEY + " AND status='QUEUED'", leaseUntil, requestId, key.uploadId(), key.rowNumber()) == 1;
    }

    /** 만든 협상 작업을 바로 남겨, 검토가 실패해 다시 시도해도 작업을 또 만들지 않게 한다. */
    void attachWorkspace(Key key, UUID negotiationId) {
        jdbc.update("UPDATE legal_review_rows SET negotiation_id=? WHERE " + KEY + " AND status='RUNNING'",
                negotiationId, key.uploadId(), key.rowNumber());
    }

    /** RUNNING 행에만 결과를 쓴다. 임대가 끝나 STALE로 정리된 행은 바꾸지 않는다. */
    boolean finish(Key key, ObjectNode result) {
        result.putNull("lease_until");
        // 다른 시각과 같이 DB 시계로 적는다
        String assignments = String.join(", ", result.propertyNames().stream().map(k -> column(k) + " = v." + k).toList())
                + ", finished_at = clock_timestamp()";
        return jdbc.update("UPDATE legal_review_rows t SET " + assignments
                + " FROM jsonb_populate_record(NULL::legal_review_rows, ?::jsonb) v"
                + " WHERE t.upload_id=? AND t.row_number=? AND t.status='RUNNING'",
                mapper.writeValueAsString(result), key.uploadId(), key.rowNumber()) == 1;
    }

    /** FAILED·SKIPPED → QUEUED. 실패한 행은 같은 입력으로 다시 시도하고, 건너뛴 행은 검토를 시작한다. */
    boolean requeue(Key key) {
        return jdbc.update("UPDATE legal_review_rows SET status='QUEUED', error_code=NULL, error_retryable=NULL,"
                + " execution_status=NULL, started_at=NULL, finished_at=NULL WHERE " + KEY + " AND status IN ('FAILED', 'SKIPPED')",
                key.uploadId(), key.rowNumber()) == 1;
    }

    /** 트랜잭션이 끝날 때까지 이 사용자의 대기 행 수 확인과 추가를 한 줄로 세운다. */
    void lockOwner(UUID owner) {
        jdbc.queryForList("SELECT pg_advisory_xact_lock(hashtextextended(?, 0))", "legal-review-upload:" + owner);
    }

    /** 이 사용자의 QUEUED + RUNNING 행 수. */
    int pending(UUID owner) {
        return jdbc.queryForObject("SELECT count(*) FROM legal_review_rows WHERE created_by=? AND status IN ('QUEUED', 'RUNNING')",
                Integer.class, owner);
    }

    /** 임대가 끝난 RUNNING을 다시 시도할 수 있는 실패로 정리한다. 임대가 남은 행은 다른 서버가 처리 중일 수 있어 그대로 둔다. */
    int expireRunning() {
        return jdbc.update("UPDATE legal_review_rows SET status='FAILED', error_code='STALE_REVIEW', error_retryable=true,"
                + " finished_at=clock_timestamp(), lease_until=NULL WHERE status='RUNNING' AND lease_until < clock_timestamp()");
    }

    List<Key> queued() {
        return jdbc.query("SELECT upload_id, row_number FROM legal_review_rows WHERE status='QUEUED' ORDER BY created_at, row_number",
                (rs, n) -> new Key(rs.getObject(1, UUID.class), rs.getInt(2)));
    }

    /** 실행기에 넣지 못한 행. */
    void reject(Key key) {
        jdbc.update("UPDATE legal_review_rows SET status='FAILED', error_code='QUEUE_FULL', error_retryable=true,"
                + " finished_at=clock_timestamp() WHERE " + KEY + " AND status='QUEUED'", key.uploadId(), key.rowNumber());
    }

    private List<ObjectNode> rows(String sql, Object... args) {
        return jdbc.query(sql, (rs, n) -> (ObjectNode) mapper.readTree(rs.getString(1)), args);
    }

    private ObjectNode one(String sql, Object... args) {
        List<ObjectNode> found = rows(sql, args);
        return found.isEmpty() ? null : found.getFirst();
    }

    private void insert(String table, ObjectNode row) {
        String columns = String.join(", ", row.propertyNames().stream().map(LegalReviewUploadStore::column).toList());
        jdbc.update("INSERT INTO " + table + " (" + columns + ") SELECT " + columns
                + " FROM jsonb_populate_record(NULL::" + table + ", ?::jsonb)", mapper.writeValueAsString(row));
    }

    // 열 이름은 서비스가 만든 저장 행에서만 온다. 그래도 SQL에 붙이기 전에 모양을 확인한다.
    private static String column(String name) {
        if (!name.matches("[a-z_][a-z0-9_]*")) throw new IllegalArgumentException("저장할 열을 확인하세요");
        return name;
    }
}
