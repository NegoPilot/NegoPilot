-- 엑셀 업로드에서 필요한 협력사 행만 검토하고, 사용자별 대기 행 수를 제한한다 (#307 5번). 계약: docs/contracts/legal-review-upload.md
-- 업로드에 supplier_id를 주면 그 협력사의 정상 행만 QUEUED로 두고 나머지 정상 행은 SKIPPED로 저장한다.
-- SKIPPED 행은 Agent를 부르지 않았으며 retry로 검토를 요청할 수 있다. offer는 그대로 저장한다.
ALTER TABLE legal_review_rows DROP CONSTRAINT legal_review_rows_status_check;
ALTER TABLE legal_review_rows ADD CONSTRAINT legal_review_rows_status_check
    CHECK (status IN ('INVALID', 'SKIPPED', 'QUEUED', 'RUNNING', 'COMPLETED', 'FAILED'));

-- 업로드·재시도 때 그 사용자의 QUEUED + RUNNING 행 수를 센다
CREATE INDEX legal_review_rows_owner_pending_idx ON legal_review_rows (created_by) WHERE status IN ('QUEUED', 'RUNNING');
