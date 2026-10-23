-- 엑셀 거래·계약 조건 업로드와 행별 법규 검토 (#284). 계약: docs/contracts/legal-review-upload.md
-- 검토 결과·review_token·검토 버전은 협상 작업(neg_workspaces / neg_review_revisions)에 저장한다. 그래야 같은 검토로
-- 협상안을 만들 수 있다. 여기에는 업로드 건과 엑셀 행 번호, 원본 칸 값, 그리고 협상 작업에 저장되지 않는
-- 입력 오류·실행 실패 상태만 둔다.

-- 1. 업로드 건. 원본 파일은 저장하지 않는다.
CREATE TABLE legal_review_uploads (
    id           UUID PRIMARY KEY,
    owner_id     UUID NOT NULL REFERENCES app_users(id),
    file_name    VARCHAR(120) NOT NULL,
    file_sha256  CHAR(64) NOT NULL,
    row_count    INT NOT NULL CHECK (row_count >= 0),
    sheet_errors JSONB NOT NULL DEFAULT '[]'::jsonb,   -- 행에 붙일 수 없는 오류 (연결할 거래가 없는 연동 상세 행 등)
    created_at   TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX legal_review_uploads_owner_idx ON legal_review_uploads (owner_id, created_at DESC, id DESC);

-- 2. 엑셀 행. 정상 행은 행마다 새 협상 작업을 만들고 그 작업의 검토 버전과 연결한다.
--    이후 조건을 바꾼 재검토는 협상 작업 API(POST /api/negotiations/{id}/reviews)로 같은 작업에 버전을 쌓는다.
CREATE TABLE legal_review_rows (
    upload_id        UUID NOT NULL REFERENCES legal_review_uploads(id) ON DELETE CASCADE,
    row_number       INT  NOT NULL CHECK (row_number > 1),        -- 엑셀 행 번호 (머리글이 1행)
    status           VARCHAR(9) NOT NULL CHECK (status IN ('INVALID', 'QUEUED', 'RUNNING', 'COMPLETED', 'FAILED')),
    source_cells     JSONB NOT NULL,      -- 엑셀 원본 {머리글: 칸 값}과 연결된 연동 상세 행
    input_errors     JSONB,               -- INVALID일 때 [{column, message}]
    offer            JSONB,               -- 엑셀에서 변환한 검토 조건 (transaction 포함, null 보존). INVALID면 NULL
    supplier_id      VARCHAR(20),
    rfq_id           VARCHAR(20),
    negotiation_id   UUID REFERENCES neg_workspaces(id),          -- 이 행으로 만든 협상 작업
    review_revision  INT,                                         -- 이 업로드로 저장한 검토 버전
    request_id       UUID,                                        -- 마지막 실행의 Agent request_id
    attempts         INT NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    execution_status VARCHAR(11) CHECK (execution_status IN ('COMPLETED', 'NEEDS_INPUT', 'FAILED')),
    error_code       VARCHAR(40),
    error_retryable  BOOLEAN,
    lease_until      TIMESTAMPTZ,         -- RUNNING이 이 시각을 넘기면 실패(STALE_REVIEW)로 정리한다
    created_by       UUID NOT NULL REFERENCES app_users(id),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    started_at       TIMESTAMPTZ,
    finished_at      TIMESTAMPTZ,
    PRIMARY KEY (upload_id, row_number),
    FOREIGN KEY (negotiation_id, review_revision) REFERENCES neg_review_revisions(workspace_id, revision),
    CHECK ((status = 'INVALID') = (offer IS NULL)),
    CHECK ((status = 'INVALID') = (input_errors IS NOT NULL)),
    CHECK (status <> 'RUNNING' OR lease_until IS NOT NULL),
    CHECK (status <> 'FAILED' OR error_code IS NOT NULL),
    CHECK ((status = 'COMPLETED') = (review_revision IS NOT NULL)),
    CHECK (review_revision IS NULL OR negotiation_id IS NOT NULL)
);
CREATE INDEX legal_review_rows_pending_idx ON legal_review_rows (status) WHERE status IN ('QUEUED', 'RUNNING');
CREATE INDEX legal_review_rows_negotiation_idx ON legal_review_rows (negotiation_id) WHERE negotiation_id IS NOT NULL;
