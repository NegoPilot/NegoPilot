"""분해에 쓰는 모델 백엔드. 모두 같은 일을 한다: 시스템 프롬프트와 질문을 받아 분해 결과 JSON 문자열을 돌려준다.

- anthropic: Claude 구조화 출력 (기본)
- ollama: 로컬 모델(Llama 등). 비용 없이 평가·개발할 때 쓴다

거절이나 출력 잘림이면 None을 돌려준다. 네트워크·서버 오류는 예외로 올린다.
"""

import copy
import logging
from typing import Protocol

import anthropic
import httpx2

from negopilot_shared import DecompositionResult

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class Backend(Protocol):
    async def generate(self, system: str, user: str) -> str | None: ...


class AnthropicBackend:
    # SDK가 API에서 지원하는 형태로 바꾼 구조화 출력 스키마
    OUTPUT_FORMAT = {"type": "json_schema",
                     "schema": anthropic.transform_schema(DecompositionResult.model_json_schema())}

    def __init__(self, client: anthropic.AsyncAnthropic, model: str, effort: str, max_tokens: int,
                 use_fallbacks: bool):
        self.client = client
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.use_fallbacks = use_fallbacks

    async def generate(self, system: str, user: str) -> str | None:
        kwargs = {"betas": [FALLBACK_BETA], "fallbacks": "default"} if self.use_fallbacks else {}
        response = await self.client.beta.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            # 요청마다 바뀌지 않는 부분이라 캐시한다
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            output_config={"effort": self.effort, "format": self.OUTPUT_FORMAT},
            **kwargs,
        )
        # 거절이나 잘림은 본문을 읽기 전에 확인한다 (이때 본문은 비었거나 미완성 JSON이다)
        if response.stop_reason == "refusal":
            log.warning("분해 요청 거절: %s", getattr(response.stop_details, "category", None))
            return None
        if response.stop_reason == "max_tokens":
            log.warning("분해 출력이 max_tokens에서 잘림")
            return None
        return next((b.text for b in response.content if b.type == "text"), "")


def inline_refs(schema: dict) -> dict:
    """$defs/$ref 를 풀어 하나의 스키마로 만든다. 로컬 모델의 문법 변환기가 $ref 를 못 다루는 경우가 있다."""
    defs = schema.get("$defs", {})

    def resolve(node):
        if isinstance(node, dict):
            if "$ref" in node:
                target = copy.deepcopy(defs[node["$ref"].split("/")[-1]])
                rest = {k: v for k, v in node.items() if k != "$ref"}
                return resolve({**target, **rest})
            return {k: resolve(v) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    return resolve(schema)


class OllamaBackend:
    """Ollama /api/chat. format 에 JSON 스키마를 넘겨 출력 형식을 강제한다."""

    OUTPUT_SCHEMA = inline_refs(DecompositionResult.model_json_schema())

    def __init__(self, model: str, base_url: str = "http://localhost:11434",
                 http_client: httpx2.AsyncClient | None = None, timeout: float = 300.0,
                 num_ctx: int = 16384, num_predict: int = 2048):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.http = http_client or httpx2.AsyncClient(timeout=timeout)
        self.num_ctx = num_ctx  # 시스템 프롬프트가 길어서 기본 컨텍스트(2~4K)로는 잘린다
        # 작은 모델이 JSON 문자열을 끝없이 이어 가는 경우가 있어 출력 길이에 상한을 둔다
        # (하위 질문 5개짜리 결과도 2천 토큰 안쪽이다). 넘으면 done_reason "length" → failed
        self.num_predict = num_predict

    async def generate(self, system: str, user: str) -> str | None:
        response = await self.http.post(f"{self.base_url}/api/chat", json={
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "format": self.OUTPUT_SCHEMA,
            "stream": False,
            "options": {"temperature": 0, "num_ctx": self.num_ctx, "num_predict": self.num_predict},
        })
        response.raise_for_status()
        data = response.json()
        if data.get("done_reason") == "length":
            log.warning("분해 출력이 길이 제한에서 잘림")
            return None
        return data["message"]["content"]
