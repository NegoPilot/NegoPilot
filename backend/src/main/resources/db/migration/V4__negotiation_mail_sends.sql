-- 협력사 발송용 제안서 메일: 미리보기 한 번과 그 발송 결과.
-- 원본 발송 기록은 mail-mcp(SQLite)에 있고, send_offer_mail의 client_ref에 이 행의 id를 넘겨 정확히 연결한다.
-- 확인 토큰은 응답에 내보내지 않고 이 테이블에만 둔다. 발송이 끝나면(성공·실패) 지운다.

CREATE TABLE neg_mail_sends (
    id                 UUID PRIMARY KEY,                 -- = mail-mcp client_ref
    proposal_id        UUID NOT NULL,
    revision           INT  NOT NULL,                    -- 미리보기 때 협상안 head
    bundle_id          UUID NOT NULL REFERENCES neg_export_bundles(id),
    mail_document_id   VARCHAR(60) NOT NULL,             -- OfferDocument.document_id
    document_version   INT NOT NULL CHECK (document_version > 0),  -- OfferDocument.version (= external_content_rev)
    content_hash       VARCHAR(71) NOT NULL CHECK (content_hash ~ '^sha256:[0-9a-f]{64}$'),
    contact_id         BIGINT NOT NULL,                  -- business supplier_contacts.contact_id (논리 참조)
    recipient          JSONB NOT NULL,                   -- 미리보기 응답 to {contact_id, name, email, supplier_id, supplier_name}
    sender             TEXT NOT NULL,                    -- 미리보기 응답 from
    reply_to           TEXT,
    subject            TEXT NOT NULL,
    body               TEXT NOT NULL,
    message            TEXT CHECK (char_length(message) <= 2000),
    attachments        JSONB NOT NULL,                   -- [{filename, content_type, bytes, sha256}]
    dry_run            BOOLEAN NOT NULL,
    acknowledgements   JSONB NOT NULL,                   -- {check_needed, unresolved_issue_ids}
    confirmation_token TEXT,
    expires_at         TIMESTAMPTZ NOT NULL,
    status             VARCHAR(10) NOT NULL
                       CHECK (status IN ('PREVIEWED', 'SENDING', 'SENT', 'DRY_RUN', 'FAILED', 'UNKNOWN', 'STALE')),
    send_id            VARCHAR(80) UNIQUE,               -- mail-mcp sends.send_id (논리 참조)
    message_id         TEXT,
    error_code         VARCHAR(40),
    created_by         UUID NOT NULL REFERENCES app_users(id),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    send_requested_at  TIMESTAMPTZ,
    finished_at        TIMESTAMPTZ,
    FOREIGN KEY (proposal_id, revision) REFERENCES neg_proposal_revisions(proposal_id, revision) ON DELETE CASCADE,
    CHECK (status IN ('PREVIEWED', 'STALE') OR send_requested_at IS NOT NULL),
    CHECK (status NOT IN ('SENT', 'DRY_RUN') OR send_id IS NOT NULL),
    CHECK (status NOT IN ('SENT', 'DRY_RUN', 'FAILED', 'STALE') OR confirmation_token IS NULL),
    CHECK (status <> 'FAILED' OR error_code IS NOT NULL)
);

-- 미리보기를 두 번 받아도 같은 문서 버전을 같은 담당자에게 두 번 보내지 않는다.
-- UNKNOWN(발송 여부 확인 필요)도 막는다. DRY_RUN은 실제 발송이 아니라서 막지 않는다.
CREATE UNIQUE INDEX neg_mail_sends_once ON neg_mail_sends (mail_document_id, document_version, contact_id)
    WHERE status IN ('SENDING', 'SENT', 'UNKNOWN');
CREATE INDEX neg_mail_sends_proposal ON neg_mail_sends (proposal_id, created_at DESC);
