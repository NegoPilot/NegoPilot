#!/usr/bin/env bash
set -Eeuo pipefail

# Official PostgreSQL runs this only when its named volume is first initialized.
# Refuse an existing DB as an additional guard against a manual rerun.
: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${APP_DB_PASSWORD:?APP_DB_PASSWORD is required}"
: "${BUSINESS_DB_PASSWORD:?BUSINESS_DB_PASSWORD is required}"
: "${BUSINESS_READER_PASSWORD:?BUSINESS_READER_PASSWORD is required}"
: "${MAIL_READER_PASSWORD:?MAIL_READER_PASSWORD is required}"

psql --no-psqlrc --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --set ON_ERROR_STOP=1 <<'SQL'
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_database WHERE datname IN ('negopilot_app', 'negopilot_business')) THEN
        RAISE EXCEPTION 'Refusing to seed existing local databases. Existing data was not changed.';
    END IF;
END $$;
\getenv app_password APP_DB_PASSWORD
\getenv business_password BUSINESS_DB_PASSWORD
\getenv reader_password BUSINESS_READER_PASSWORD
\getenv mail_reader_password MAIL_READER_PASSWORD
CREATE ROLE negopilot_app LOGIN PASSWORD :'app_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE negopilot_business_owner LOGIN PASSWORD :'business_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE negopilot_business_reader LOGIN PASSWORD :'reader_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE negopilot_business_reader SET default_transaction_read_only = on;
CREATE ROLE negopilot_mail_reader LOGIN PASSWORD :'mail_reader_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE negopilot_mail_reader SET default_transaction_read_only = on;
CREATE DATABASE negopilot_app OWNER negopilot_app;
CREATE DATABASE negopilot_business OWNER negopilot_business_owner;
REVOKE ALL ON DATABASE negopilot_app FROM PUBLIC;
REVOKE ALL ON DATABASE negopilot_business FROM PUBLIC;
GRANT CONNECT ON DATABASE negopilot_app TO negopilot_app;
GRANT CONNECT ON DATABASE negopilot_business TO negopilot_business_owner, negopilot_business_reader,
    negopilot_mail_reader;
SQL

psql --no-psqlrc --username "$POSTGRES_USER" --dbname negopilot_app --set ON_ERROR_STOP=1 <<'SQL'
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SQL

psql --no-psqlrc --username "$POSTGRES_USER" --dbname negopilot_business --set ON_ERROR_STOP=1 <<'SQL'
BEGIN;
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
    ) THEN
        RAISE EXCEPTION 'Business schema is not empty. Refusing to run seed.';
    END IF;
END $$;
SET ROLE negopilot_business_owner;
\i /opt/negopilot/business-seed.sql
\i /opt/negopilot/V1__supplier_management.sql
\i /opt/negopilot/V2__supplier_contacts.sql
\i /opt/negopilot/supplier-contacts-demo.sql
RESET ROLE;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO negopilot_business_reader;
GRANT SELECT ON public.suppliers, public.financials, public.capabilities, public.quality,
    public.contracts, public.quotes, public.oems, public.rfqs, public.supplier_overview
    TO negopilot_business_reader;
-- 담당자 이메일은 mail-mcp만 읽는다. business MCP(모델이 보는 결과)에는 주지 않는다 (#237)
GRANT USAGE ON SCHEMA public TO negopilot_mail_reader;
GRANT SELECT ON public.suppliers, public.supplier_contacts TO negopilot_mail_reader;
-- Do not expose the administrator audit table or writable sequences to MCP.
COMMIT;
SQL

# This marker is deliberately last. It is absent if any preparation step fails.
psql --no-psqlrc --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --set ON_ERROR_STOP=1 <<'SQL'
CREATE TABLE public.negopilot_local_init (
    name TEXT PRIMARY KEY,
    completed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
INSERT INTO public.negopilot_local_init(name) VALUES ('business-seed-v1');
SQL
