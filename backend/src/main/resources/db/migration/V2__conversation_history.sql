CREATE TABLE app_conversations (
    id UUID PRIMARY KEY,
    owner_id UUID NOT NULL REFERENCES app_users(id),
    title VARCHAR(100) NOT NULL DEFAULT '새 대화',
    title_manual BOOLEAN NOT NULL DEFAULT FALSE,
    context JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    last_seq BIGINT NOT NULL DEFAULT 0 CHECK (last_seq >= 0),
    lease_token UUID,
    lease_until TIMESTAMPTZ,
    CHECK ((lease_token IS NULL) = (lease_until IS NULL))
);
CREATE INDEX app_conversations_owner_updated_idx ON app_conversations(owner_id, updated_at DESC, id DESC);

CREATE TABLE app_conversation_messages (
    conversation_id UUID NOT NULL REFERENCES app_conversations(id) ON DELETE CASCADE,
    seq BIGINT NOT NULL CHECK (seq > 0),
    message_id UUID NOT NULL UNIQUE,
    role VARCHAR(9) NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    response JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (conversation_id, seq),
    CHECK ((role = 'user' AND response IS NULL) OR (role = 'assistant' AND response IS NOT NULL))
);

CREATE TABLE app_conversation_requests (
    conversation_id UUID NOT NULL REFERENCES app_conversations(id) ON DELETE CASCADE,
    idempotency_key UUID NOT NULL,
    body_hash CHAR(64) NOT NULL,
    response JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (conversation_id, idempotency_key)
);
