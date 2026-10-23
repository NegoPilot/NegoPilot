# syntax=docker/dockerfile:1
FROM python:3.12-slim AS seed-builder
WORKDIR /seed
# Same adapter version as business-mcp/uv.lock. No database or external data API is used.
RUN pip install --no-cache-dir "psycopg[binary]==3.3.6"
COPY mcp-servers/business-mcp/scripts/seed.py business/scripts/seed.py
COPY mcp-servers/business-mcp/schema.sql business/schema.sql
COPY mcp-servers/business-mcp/data/dart_companies.csv business/data/dart_companies.csv
COPY mcp-servers/business-mcp/data/dart_financials.csv business/data/dart_financials.csv
COPY deploy/local/postgres/export_seed.py export_seed.py
RUN python export_seed.py /seed/business /seed/business-seed.sql

FROM postgres:17-bookworm
COPY --from=seed-builder /seed/business-seed.sql /opt/negopilot/business-seed.sql
COPY backend/src/main/resources/db/business/V1__supplier_management.sql /opt/negopilot/V1__supplier_management.sql
COPY backend/src/main/resources/db/business/V2__supplier_contacts.sql /opt/negopilot/V2__supplier_contacts.sql
COPY deploy/local/postgres/supplier-contacts-demo.sql /opt/negopilot/supplier-contacts-demo.sql
COPY --chmod=755 deploy/local/postgres/10-init.sh /docker-entrypoint-initdb.d/10-negopilot.sh
COPY --chmod=755 deploy/local/postgres/healthcheck.sh /opt/negopilot/healthcheck.sh
HEALTHCHECK --interval=5s --timeout=5s --start-period=30s --retries=20 CMD ["/opt/negopilot/healthcheck.sh"]
