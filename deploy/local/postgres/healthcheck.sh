#!/usr/bin/env bash
set -Eeuo pipefail

# TCP excludes the official entrypoint's socket-only initialization server.
export PGPASSWORD="${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"
result="$(psql --no-psqlrc --host 127.0.0.1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    --set ON_ERROR_STOP=1 --tuples-only --no-align \
    --command "SELECT 1 FROM public.negopilot_local_init WHERE name = 'business-seed-v1'" 2>/dev/null)"
test "$result" = 1
