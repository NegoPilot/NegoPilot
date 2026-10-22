# law-mcp 검색 방식 비교 평가(eval/eval_retrieval.py)를 한 번 돌리는 이미지. compose.retrieval-eval.yaml 이 쓴다.
# 서버 이미지(law.Dockerfile)와 같은 코드·색인에 numpy 와 임베딩용 패키지만 더한다.
FROM ghcr.io/astral-sh/uv:0.11.31 AS uv
FROM python:3.12-slim
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1
WORKDIR /app/law-mcp
COPY mcp-servers/law-mcp/pyproject.toml mcp-servers/law-mcp/uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
# torch 는 CPU 빌드만 받는다 (PyPI 기본 x86_64 빌드는 CUDA 를 끌고 와 수 GB 가 된다)
RUN uv pip install --python .venv/bin/python --index-url https://download.pytorch.org/whl/cpu torch==2.14.0 \
    && uv pip install --python .venv/bin/python numpy==2.3.5 transformers==5.14.1 openai==2.47.0
COPY mcp-servers/law-mcp/ ./
ENV PATH="/app/law-mcp/.venv/bin:$PATH" HF_HOME=/var/cache/huggingface
CMD ["python", "eval/eval_retrieval.py", "--embed-python", "python"]
