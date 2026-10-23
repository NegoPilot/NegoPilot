# syntax=docker/dockerfile:1
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11.31 /uv /bin/uv
WORKDIR /app
COPY mcp-servers/mail-mcp/pyproject.toml mcp-servers/mail-mcp/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --python /usr/local/bin/python

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    DATABASE_URL="dbname=negopilot_business" \
    PGHOST=postgres PGPORT=5432 PGUSER=negopilot_mail_reader \
    MAIL_RUNTIME_DIR=/app/runtime
WORKDIR /app
RUN useradd --system --uid 10001 --no-create-home negopilot && mkdir -p /app/runtime && chown 10001:10001 /app/runtime
COPY --from=builder /app/.venv /app/.venv
COPY --chown=10001:10001 mcp-servers/mail-mcp/server.py mcp-servers/mail-mcp/offer_mail.py ./
USER 10001
EXPOSE 9104
CMD ["python", "server.py", "--http", "9104"]
