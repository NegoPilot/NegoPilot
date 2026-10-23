-- 협상 제안서 메일(#237)의 수신자. 협력사마다 담당자 여러 명을 두고, 기본 수신자는 한 명이다.
-- Existing data only. 실제 연락처를 넣지 않는다. 로컬 시연 주소는 deploy/local/postgres에서만 넣는다.
CREATE TABLE IF NOT EXISTS supplier_contacts (
    contact_id BIGSERIAL PRIMARY KEY,
    supplier_id TEXT NOT NULL REFERENCES suppliers(supplier_id) ON DELETE CASCADE,
    name TEXT NOT NULL CHECK (char_length(btrim(name)) BETWEEN 1 AND 100),
    email TEXT NOT NULL CHECK (char_length(email) <= 254 AND email ~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$'),
    role TEXT CHECK (role IS NULL OR char_length(btrim(role)) BETWEEN 1 AND 50),
    is_primary BOOLEAN NOT NULL DEFAULT FALSE,
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'INACTIVE')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS supplier_contacts_email_unique ON supplier_contacts(supplier_id, lower(email));
-- 활성 기본 수신자는 협력사마다 하나다
CREATE UNIQUE INDEX IF NOT EXISTS supplier_contacts_one_primary ON supplier_contacts(supplier_id)
    WHERE is_primary AND status = 'ACTIVE';
