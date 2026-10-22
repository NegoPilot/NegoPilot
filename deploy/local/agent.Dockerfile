FROM ghcr.io/astral-sh/uv:0.11.31 AS uv
FROM python:3.12-slim
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1
WORKDIR /app
COPY shared/ shared/
COPY agent/ agent/
COPY docs/contracts/fixtures/ docs/contracts/fixtures/
COPY deploy/local/agent_start.py /app/agent_start.py
WORKDIR /app/agent
RUN uv sync --locked --no-dev \
    && useradd --uid 10001 --create-home app
ENV PATH="/app/agent/.venv/bin:$PATH"
USER app
EXPOSE 9200
ENTRYPOINT ["python", "/app/agent_start.py"]
CMD ["uvicorn", "negopilot_agent.api.app:app", "--host", "0.0.0.0", "--port", "9200"]
