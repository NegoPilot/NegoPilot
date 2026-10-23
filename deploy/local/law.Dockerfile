FROM ghcr.io/astral-sh/uv:0.11.31 AS uv
FROM python:3.12-slim
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1
WORKDIR /app/law-mcp
COPY mcp-servers/law-mcp/ ./
RUN uv sync --locked --no-dev \
    && useradd --uid 10001 --create-home app
ENV PATH="/app/law-mcp/.venv/bin:$PATH"
USER app
EXPOSE 9103
CMD ["python", "server.py", "--http", "9103"]
