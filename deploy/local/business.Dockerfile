# syntax=docker/dockerfile:1
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11.31 /uv /bin/uv
WORKDIR /app
COPY mcp-servers/business-mcp/pyproject.toml mcp-servers/business-mcp/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --python /usr/local/bin/python

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    DATABASE_URL="dbname=negopilot_business" \
    PGHOST=postgres PGPORT=5432 PGUSER=negopilot_business_reader
WORKDIR /app
RUN useradd --system --uid 10001 --no-create-home negopilot
COPY --from=builder /app/.venv /app/.venv
COPY --chown=10001:10001 mcp-servers/business-mcp/server.py mcp-servers/business-mcp/supplier_directory.py mcp-servers/business-mcp/rules.py ./
COPY --chown=10001:10001 mcp-servers/business-mcp/company_size.py ./company_size.py
COPY --chown=10001:10001 mcp-servers/business-mcp/data/legal_size_rules.json ./data/legal_size_rules.json
USER 10001
EXPOSE 9102
CMD ["python", "server.py", "--http", "9102"]
