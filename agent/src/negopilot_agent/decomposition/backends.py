"""분해에 쓰는 모델 백엔드. 모두 같은 일을 한다: 시스템 프롬프트와 질문을 받아 분해 결과 JSON 문자열을 돌려준다.

- anthropic: Claude 구조화 출력
- openai: OpenAI Chat Completions (기본)
- ollama: 로컬 Ollama native /api/chat 구조화 출력

거절이나 출력 잘림이면 None을 돌려준다. 네트워크·서버 오류는 예외로 올린다.
"""

import logging
from typing import Protocol

import anthropic
import openai
from openai.lib._pydantic import to_strict_json_schema
from pydantic import BaseModel

from negopilot_shared import DecompositionResult
from negopilot_agent.ollama import OllamaBackend  # re-export alongside the other providers

from negopilot_agent import timing
from negopilot_agent.decomposition import wire

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
        with timing.span("llm", self.model) as meta:
            response = await self.client.beta.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                # 요청마다 바뀌지 않는 부분이라 캐시한다
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user}],
                output_config={"effort": self.effort, "format": self.OUTPUT_FORMAT},
                **kwargs,
            )
            if usage := getattr(response, "usage", None):
                meta.update(input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                            cached_tokens=usage.cache_read_input_tokens or 0)
        # 거절이나 잘림은 본문을 읽기 전에 확인한다 (이때 본문은 비었거나 미완성 JSON이다)
        if response.stop_reason == "refusal":
            log.warning("분해 요청 거절: %s", getattr(response.stop_details, "category", None))
            return None
        if response.stop_reason == "max_tokens":
            log.warning("분해 출력이 max_tokens에서 잘림")
            return None
        return next((b.text for b in response.content if b.type == "text"), "")


class OpenAIBackend:
    """OpenAI Chat Completions. strict 구조화 출력으로 스키마(허용 값 목록 포함)를 지키게 한다.

    strict 모드는 모든 필드를 required로, additionalProperties를 false로 바꾼 스키마가 필요하다.
    그 변환은 SDK의 to_strict_json_schema를 쓴다 (chat.completions.parse가 내부에서 쓰는 함수).
    parse를 직접 쓰지 않는 이유: parse는 응답을 바로 모델로 검증해서, 계약 위반이 우리 재시도(오류 알려 주고 다시 쓰기)
    전에 예외로 나간다. 다른 백엔드처럼 JSON 문자열을 돌려주고 검증은 분해기가 한다.

    분해는 계약 형식 대신 짧은 형식(wire.py)으로 출력받아 계약 형식으로 바꿔 돌려준다. strict 모드에서 계약 형식은
    쓰지 않는 엔티티까지 null로 모두 출력해 출력 토큰(=응답 시간)이 두 배쯤 된다.
    """

    OUTPUT_MODEL: type[BaseModel] = wire.DecompositionWire
    OUTPUT_NAME = DecompositionResult.__name__
    OUTPUT_SCHEMA = to_strict_json_schema(wire.DecompositionWire)
    COMPACT_OUTPUT = True   # 분해기가 프롬프트의 출력 규칙과 예시도 짧은 형식으로 만든다

    @staticmethod
    def to_contract(text: str) -> str:
        return wire.to_contract_json(text)

    def __init__(self, client: openai.AsyncOpenAI, model: str, max_tokens: int):
        self.client = client
        self.model = model
        self.max_tokens = max_tokens

    async def generate(self, system: str, user: str) -> str | None:
        with timing.span("llm", self.model) as meta:
            response = await self.client.chat.completions.create(
                model=self.model,
                # 시스템 프롬프트를 앞에 두면 OpenAI가 같은 앞부분을 자동으로 캐시한다
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=0,
                max_completion_tokens=self.max_tokens,
                response_format={"type": "json_schema", "json_schema": {
                    "name": self.OUTPUT_NAME, "schema": self.OUTPUT_SCHEMA, "strict": True}},
            )
            if usage := getattr(response, "usage", None):
                details = usage.prompt_tokens_details
                meta.update(input_tokens=usage.prompt_tokens, output_tokens=usage.completion_tokens,
                            cached_tokens=(details.cached_tokens or 0) if details else 0)
        choice = response.choices[0]
        if choice.message.refusal:
            log.warning("모델 요청 거절")
            return None
        if choice.finish_reason == "length":
            log.warning("분해 출력이 max_completion_tokens에서 잘림")
            return None
        return self.to_contract(choice.message.content or "")
