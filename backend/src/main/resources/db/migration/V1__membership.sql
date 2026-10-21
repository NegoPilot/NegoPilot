CREATE TABLE app_users (
    id uuid PRIMARY KEY,
    email varchar(254) NOT NULL UNIQUE CHECK (email = lower(btrim(email))),
    name varchar(100) NOT NULL,
    department varchar(100) NOT NULL,
    job_title varchar(100) NOT NULL DEFAULT '',
    password_hash varchar(100) NOT NULL,
    role varchar(10) NOT NULL CHECK (role IN ('ADMIN', 'USER')),
    status varchar(12) NOT NULL CHECK (status IN ('PENDING', 'ACTIVE', 'REJECTED', 'DISABLED')),
    created_at timestamptz NOT NULL DEFAULT current_timestamp,
    updated_at timestamptz NOT NULL DEFAULT current_timestamp
);
CREATE INDEX app_users_listing_idx ON app_users (created_at DESC, id);
CREATE TABLE app_membership_guard (id smallint PRIMARY KEY CHECK (id = 1));
INSERT INTO app_membership_guard(id) VALUES (1);
CREATE TABLE app_access_logs (
    id uuid PRIMARY KEY,
    user_id uuid REFERENCES app_users(id),
    event varchar(20) NOT NULL CHECK (event IN ('LOGIN_SUCCESS', 'LOGIN_FAILURE', 'LOGOUT')),
    occurred_at timestamptz NOT NULL DEFAULT current_timestamp,
    ip varchar(64) NOT NULL,
    user_agent varchar(512) NOT NULL
);
CREATE INDEX app_access_logs_time_idx ON app_access_logs (occurred_at DESC, id);
CREATE TABLE app_membership_audit (
    id uuid PRIMARY KEY,
    actor_id uuid REFERENCES app_users(id),
    target_id uuid NOT NULL REFERENCES app_users(id),
    action varchar(30) NOT NULL,
    old_value varchar(30),
    new_value varchar(30) NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT current_timestamp
);

CREATE TABLE SPRING_SESSION (
    PRIMARY_ID char(36) PRIMARY KEY,
    SESSION_ID char(36) NOT NULL,
    CREATION_TIME bigint NOT NULL,
    LAST_ACCESS_TIME bigint NOT NULL,
    MAX_INACTIVE_INTERVAL integer NOT NULL,
    EXPIRY_TIME bigint NOT NULL,
    PRINCIPAL_NAME varchar(100)
);
CREATE UNIQUE INDEX SPRING_SESSION_IX1 ON SPRING_SESSION (SESSION_ID);
CREATE INDEX SPRING_SESSION_IX2 ON SPRING_SESSION (EXPIRY_TIME);
CREATE INDEX SPRING_SESSION_IX3 ON SPRING_SESSION (PRINCIPAL_NAME);
CREATE TABLE SPRING_SESSION_ATTRIBUTES (
    SESSION_PRIMARY_ID char(36) NOT NULL REFERENCES SPRING_SESSION(PRIMARY_ID) ON DELETE CASCADE,
    ATTRIBUTE_NAME varchar(200) NOT NULL,
    ATTRIBUTE_BYTES bytea NOT NULL,
    PRIMARY KEY (SESSION_PRIMARY_ID, ATTRIBUTE_NAME)
);
