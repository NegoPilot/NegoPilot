-- 설계 v2 (1단계). 협상 작업·법규 검토 버전·협상안 버전·검사·내보내기·멱등 요청.
-- 대상: 서비스 DB negopilot_app (backend Flyway classpath:db/migration). 기준 main c984f7e.
-- 메일 연결 테이블(neg_mail_sends)은 2단계 V4에서 추가한다.
-- 기업 DB(negopilot_business)는 같은 PostgreSQL 서버의 다른 DB라 실제 FK를 걸 수 없다. "논리 참조"로 표시.

-- 1. 협상 작업. API의 review_id는 작업 ID와 같다(작업당 기반 검토 1개, 버전은 2의 revision).
CREATE TABLE neg_workspaces (
    id                   UUID PRIMARY KEY,
    owner_id             UUID NOT NULL REFERENCES app_users(id),
    title                VARCHAR(100) NOT NULL,
    supplier_id          VARCHAR(20) NOT NULL CHECK (supplier_id ~ '^SUP-[0-9]{4}$'),        -- 논리 참조: business.suppliers
    rfq_id               VARCHAR(20) NOT NULL CHECK (rfq_id ~ '^RFQ-[0-9]{4}-[0-9]{3}$'),   -- 논리 참조: business.rfqs
    supplier_verified_at TIMESTAMPTZ NOT NULL,     -- 생성 시 기존 협력사 상세 조회로 확인
    rfq_verified_at      TIMESTAMPTZ,              -- 협상안 생성(check_offer)이 성공한 시각으로 갱신
    status               VARCHAR(10) NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'ARCHIVED')),
    review_head_revision INT NOT NULL DEFAULT 0 CHECK (review_head_revision >= 0),
    current_proposal_id  UUID,
    row_version          BIGINT NOT NULL DEFAULT 0,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX neg_workspaces_owner_idx ON neg_workspaces (owner_id, updated_at DESC, id DESC);

-- 2. 법규 검토 버전. 새 revision만 추가하고 수정하지 않는다.
CREATE TABLE neg_review_revisions (
    workspace_id       UUID NOT NULL REFERENCES neg_workspaces(id) ON DELETE CASCADE,
    revision           INT  NOT NULL CHECK (revision > 0),
    base_revision      INT,
    as_of              DATE NOT NULL,
    unit_price         INT  NOT NULL CHECK (unit_price > 0),   -- 검토한 제안 단가. target_price와 별개
    payment_days       INT  NOT NULL CHECK (payment_days > 0),
    contract_days      INT  NOT NULL CHECK (contract_days > 0),
    price_linkage      BOOLEAN NOT NULL,
    input_snapshot     JSONB NOT NULL,     -- Agent에 보낸 offer 원본 (transaction 포함, null·{} 보존)
    response           JSONB NOT NULL,     -- 기존 generation 0.3 응답 (legal_review.review_token은 제거해서 저장)
    legal_review       JSONB,              -- 응답의 legal_review에서 review_token을 뺀 값. 실행 실패면 Agent가 주지 않는다
    agent_review_id    VARCHAR(64),        -- legal_review.review_id
    input_hash         CHAR(64),           -- legal_review.input_hash
    review_token       TEXT,               -- 서버 보관 전용. API 응답·로그에 넣지 않는다
    token_expires_at   TIMESTAMPTZ,
    assessment_status  VARCHAR(17) CHECK (assessment_status IN ('ready', 'needs_information', 'blocked')),
    eligibility        VARCHAR(11) CHECK (eligibility IN ('ready', 'conditional', 'blocked')),
    execution_status   VARCHAR(11) NOT NULL CHECK (execution_status IN ('COMPLETED', 'NEEDS_INPUT')),
    agent_request_id   UUID NOT NULL UNIQUE,
    created_by         UUID NOT NULL REFERENCES app_users(id),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (workspace_id, revision),
    CHECK (base_revision IS NULL OR base_revision = revision - 1),
    CHECK ((legal_review IS NULL) = (assessment_status IS NULL)),
    CHECK ((assessment_status IS NULL) = (eligibility IS NULL)),
    -- 노션 계약 상태로의 고정 매핑: needs_information → conditional
    CHECK (assessment_status IS NULL OR eligibility =
           CASE assessment_status WHEN 'needs_information' THEN 'conditional' ELSE assessment_status END),
    CHECK ((review_token IS NULL) = (token_expires_at IS NULL))
);

-- 3. 협상안. 생성할 때마다 새 행. 사용자 수정은 4의 revision. 작업당 ACTIVE 1개.
CREATE TABLE neg_proposals (
    id                UUID PRIMARY KEY,
    workspace_id      UUID NOT NULL,
    review_revision   INT  NOT NULL,
    status            VARCHAR(10) NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'SUPERSEDED')),
    generation_status VARCHAR(8)  NOT NULL CHECK (generation_status IN ('offer', 'no_offer')),
    generation_input  JSONB NOT NULL,      -- POST /internal/offer-proposals 에 보낸 본문 그대로 (sender 포함)
    mail_document_id  VARCHAR(60) UNIQUE,  -- 외부 제안서 OfferDocument.document_id. no_offer면 NULL
    head_revision     INT NOT NULL CHECK (head_revision > 0),
    created_by        UUID NOT NULL REFERENCES app_users(id),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, review_revision) REFERENCES neg_review_revisions(workspace_id, revision),
    CHECK ((generation_status = 'offer') = (mail_document_id IS NOT NULL))
);
CREATE UNIQUE INDEX neg_proposals_one_active ON neg_proposals (workspace_id) WHERE status = 'ACTIVE';
CREATE INDEX neg_proposals_workspace_idx ON neg_proposals (workspace_id, created_at DESC);

ALTER TABLE neg_workspaces
    ADD CONSTRAINT neg_workspaces_current_proposal_fk
    FOREIGN KEY (id, current_proposal_id) REFERENCES neg_proposals(workspace_id, id)
    DEFERRABLE INITIALLY DEFERRED;

-- 4. 협상안 버전 = 사용자 편집 이력. 이전 버전은 수정·삭제하지 않는다.
--    두 문서가 공유하는 거래 조건의 기준은 이 행의 관계형 열이다 (JSON 본문에서는 빼서 저장).
--    비율은 Agent가 float로 판정하므로 반올림하지 않도록 double precision으로 원값을 보존한다.
CREATE TABLE neg_proposal_revisions (
    proposal_id                 UUID NOT NULL REFERENCES neg_proposals(id) ON DELETE CASCADE,
    revision                    INT  NOT NULL CHECK (revision > 0),
    base_revision               INT,
    origin                      VARCHAR(10) NOT NULL CHECK (origin IN ('GENERATED', 'USER_EDIT')),
    unit_price                  INT CHECK (unit_price > 0),
    payment_days                INT CHECK (payment_days > 0),
    price_linkage               BOOLEAN,
    contract_days               INT CHECK (contract_days > 0),
    linkage_waived_by_agreement BOOLEAN,
    raw_material_ratio          DOUBLE PRECISION CHECK (raw_material_ratio BETWEEN 0 AND 1),
    energy_ratio                DOUBLE PRECISION CHECK (energy_ratio BETWEEN 0 AND 1),
    annual_volume               INT CHECK (annual_volume > 0),
    as_of                       DATE NOT NULL,
    internal_body               JSONB NOT NULL,   -- internal_briefing (proposal + writing). 공유 조건 키 제거
    external_body               JSONB,            -- OfferDocument 허용 필드만. 공유 조건·식별·상태·해시 키 제거
    internal_content_rev        INT NOT NULL CHECK (internal_content_rev > 0),
    external_content_rev        INT CHECK (external_content_rev > 0),   -- = OfferDocument.version
    terms_rev                   INT NOT NULL CHECK (terms_rev > 0),     -- TERMS가 바뀔 때만 증가
    changed_scopes              TEXT[] NOT NULL DEFAULT '{}',
    change_set                  JSONB NOT NULL DEFAULT '[]',            -- [{path, before, after}]
    change_note                 VARCHAR(500),
    created_by                  UUID NOT NULL REFERENCES app_users(id),
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (proposal_id, revision),
    CHECK ((origin = 'GENERATED') = (revision = 1)),
    CHECK (base_revision IS NULL OR base_revision = revision - 1),
    CHECK ((revision = 1) = (base_revision IS NULL)),
    CHECK (changed_scopes <@ ARRAY['TERMS', 'PRICE_RANGE', 'INTERNAL_TEXT', 'EXTERNAL_TEXT']::TEXT[]),
    CHECK ((external_body IS NULL) = (external_content_rev IS NULL))
);

-- 5. 검사 실행 기록. not_checked / recheck_required는 저장하지 않고 조회 시 계산한다.
CREATE TABLE neg_validations (
    id                   UUID PRIMARY KEY,
    proposal_id          UUID NOT NULL,
    revision             INT  NOT NULL,
    origin               VARCHAR(10) NOT NULL CHECK (origin IN ('GENERATION', 'RECHECK')),
    status               VARCHAR(8)  NOT NULL CHECK (status IN ('checking', 'checked', 'failed')),
    conclusion           VARCHAR(27) CHECK (conclusion IN ('no_risk_identified_in_scope', 'conditional', 'risk_identified')),
    scope                JSONB NOT NULL DEFAULT '[]',
    limitations          JSONB NOT NULL DEFAULT '[]',
    violations           JSONB NOT NULL DEFAULT '[]',
    warnings             JSONB NOT NULL DEFAULT '[]',
    text_findings        JSONB,                       -- NULL = 문구 미검사
    unresolved_issue_ids TEXT[] NOT NULL DEFAULT '{}',
    error_code           VARCHAR(40),
    agent_request_id     UUID UNIQUE,
    requested_by         UUID NOT NULL REFERENCES app_users(id),
    started_at           TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    lease_until          TIMESTAMPTZ,                 -- checking일 때만. 지나면 failed(STALE_CHECK)로 정리
    finished_at          TIMESTAMPTZ,
    FOREIGN KEY (proposal_id, revision) REFERENCES neg_proposal_revisions(proposal_id, revision) ON DELETE CASCADE,
    UNIQUE (proposal_id, revision, id),
    CHECK ((status = 'checked') = (conclusion IS NOT NULL)),
    CHECK ((status = 'checking') = (finished_at IS NULL)),
    CHECK ((status = 'checking') = (lease_until IS NOT NULL)),
    CHECK ((status = 'failed') = (error_code IS NOT NULL))
);
CREATE UNIQUE INDEX neg_validations_one_running ON neg_validations (proposal_id, revision) WHERE status = 'checking';
CREATE INDEX neg_validations_latest_idx ON neg_validations (proposal_id, revision, started_at DESC);

-- 6. 내보내기 묶음 = 한 번의 render 결과 (메일 첨부·재사용 단위). 해시는 묶음 전체에 걸린다.
CREATE TABLE neg_export_bundles (
    id                     UUID PRIMARY KEY,
    proposal_id            UUID NOT NULL,
    revision               INT  NOT NULL,
    doc_type               VARCHAR(8) NOT NULL CHECK (doc_type IN ('INTERNAL', 'EXTERNAL')),
    doc_status             VARCHAR(8) NOT NULL CHECK (doc_status IN ('draft', 'final', 'internal')),
    formats                TEXT[] NOT NULL,          -- 정렬된 형식 집합 ['docx','pdf']
    include_review_details BOOLEAN,                  -- INTERNAL만
    status                 VARCHAR(10) NOT NULL CHECK (status IN ('processing', 'ready', 'failed')),
    rendered_document      JSONB,                    -- EXTERNAL: render 응답 document 그대로 (version·status·sender·content_hash)
    content_hash           VARCHAR(71) CHECK (content_hash ~ '^sha256:[0-9a-f]{64}$'),
    render_check           JSONB,                    -- EXTERNAL: render 응답 check {verdict_code, warnings}
    validation_snapshot    JSONB NOT NULL,           -- 묶음에 반영한 검사 상태 {status, checked_revision, conclusion}
    error_code             VARCHAR(40),
    created_by             UUID NOT NULL REFERENCES app_users(id),
    created_at             TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (proposal_id, revision) REFERENCES neg_proposal_revisions(proposal_id, revision) ON DELETE CASCADE,
    UNIQUE (id, doc_type, doc_status),
    CHECK (cardinality(formats) BETWEEN 1 AND 2 AND formats <@ ARRAY['pdf', 'docx']::TEXT[]),
    CHECK ((doc_type = 'INTERNAL') = (doc_status = 'internal')),
    CHECK ((doc_type = 'INTERNAL') = (include_review_details IS NOT NULL)),
    CHECK (doc_type = 'EXTERNAL' OR (rendered_document IS NULL AND content_hash IS NULL AND render_check IS NULL)),
    CHECK (doc_type = 'INTERNAL' OR status <> 'ready' OR (rendered_document IS NOT NULL AND content_hash IS NOT NULL)),
    CHECK ((status = 'failed') = (error_code IS NOT NULL))
);
CREATE UNIQUE INDEX neg_export_bundles_reuse ON neg_export_bundles
    (proposal_id, revision, doc_type, doc_status, formats, COALESCE(include_review_details, FALSE))
    WHERE status IN ('processing', 'ready');

CREATE TABLE neg_export_files (
    id           UUID PRIMARY KEY,
    bundle_id    UUID NOT NULL REFERENCES neg_export_bundles(id) ON DELETE CASCADE,
    format       VARCHAR(4) NOT NULL CHECK (format IN ('pdf', 'docx')),
    storage_key  VARCHAR(300) NOT NULL UNIQUE,
    file_name    VARCHAR(200) NOT NULL,             -- Agent 렌더 응답의 filename 그대로 (해시에 포함됨)
    content_type VARCHAR(100) NOT NULL,
    byte_size    BIGINT NOT NULL CHECK (byte_size > 0),
    sha256       CHAR(64) NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (bundle_id, format)
);

-- 7. 멱등 요청. 기존 대화와 같은 오류 코드(IDEMPOTENCY_CONFLICT)를 쓰고, 진행 중 상태와 임대 시간을 둔다.
CREATE TABLE neg_requests (
    user_id         UUID NOT NULL REFERENCES app_users(id),
    idempotency_key UUID NOT NULL,
    operation       VARCHAR(40) NOT NULL,
    body_hash       CHAR(64) NOT NULL,
    state           VARCHAR(11) NOT NULL CHECK (state IN ('IN_PROGRESS', 'DONE')),
    lease_until     TIMESTAMPTZ,
    response_status INT,
    response        JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (user_id, idempotency_key),
    CHECK ((state = 'IN_PROGRESS') = (lease_until IS NOT NULL)),
    CHECK ((state = 'DONE') = (response_status IS NOT NULL))
);
CREATE INDEX neg_requests_created_idx ON neg_requests (created_at);
