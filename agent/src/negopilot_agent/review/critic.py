"""모델 검토자: 질문과 답을 비교해 근거가 없어 빠진 부분을 찾고, 그것을 찾을 보충 질문을 낸다.

규칙 검토(reviewer.plan)는 정해진 실패(0건, 도구 오류)만 본다. 검토자는 "질문에 답했는가"를 본다.
- 사용자가 직접 물었는데 답이 다루지 않은 부분
- 판단에 꼭 필요한데 근거 자료에 없어 빠진 법적 조건·예외 (예: 단가 질문의 연동 약정, 지급기한의 어음 할인료)
검토자는 보충 질문(자연어)만 낸다. 보충 질문은 기존 분해기가 하위 질문으로 바꾸고 Routing이 실행하므로,
검토자가 도구 인자를 직접 지어내지 않고 분해 계약의 검증을 그대로 거친다.

보충할 것이 없거나 확실하지 않으면 complete=true로 끝낸다. 보충 질문은 최대 MAX_FOLLOW_UPS개.
"""

import asyncio
import json
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

import anthropic
import openai
from openai.lib._pydantic import to_strict_json_schema
from pydantic import BaseModel, Field, ValidationError

from negopilot_shared import DecompositionResult, load_intents

from negopilot_agent.decomposition.backends import AnthropicBackend, OpenAIBackend, OllamaBackend
from negopilot_agent.generation.answer import Answer

log = logging.getLogger(__name__)

MAX_FOLLOW_UPS = 2
DEFAULT_MODELS = {"anthropic": "claude-opus-5-5", "openai": "gpt-4.1-mini", "ollama": "qwen3:8b"}
ANSWER_CHARS = 4000     # 검토자에게 보여 줄 답 본문 길이 (긴 표·목록은 뒤가 잘려도 판단에 지장이 적다)


class FollowUp(BaseModel):
    aspect: str = Field(description="답에서 빠진 부분을 한 문장으로 (예: 원재료 비중이 높은데 연동 약정 의무를 다루지 않음)")
    question: str = Field(description="빠진 부분을 찾을 독립 질문. 회사 ID·RFQ 번호·법령 이름 같은 구체 값을 넣는다")


class Critique(BaseModel):
    complete: bool = Field(description="보충할 것이 없으면 true")
    missing: list[FollowUp] = Field(description="보충할 부분. complete가 true면 빈 목록. 최대 2개")


def _tool_capabilities() -> str:
    return "\n".join(f"- {spec.intent}: {spec.description}" for spec in load_intents().values()
                     if spec.server and spec.tools and spec.status == "active")


SYSTEM_PROMPT = f"""\
너는 자동차 부품 구매 협상 Agent의 답변 검토자다. 사용자 질문, Agent가 실행한 하위 질문, 근거 목록, 답변을 보고
근거 자료가 없어서 답에서 빠진 부분이 있는지 판단한다. 답을 고쳐 쓰지 않는다. 더 찾아볼 보충 질문만 낸다.

보충 질문을 내는 경우 (둘 중 하나)
1. 사용자가 직접 물었는데 답이 다루지 않았거나 "확인하지 못했다"고 한 부분
2. 사용자의 판단에 꼭 필요한 법적 조건·예외가 근거 목록에 없어 답에서 빠진 경우
   예) 단가·원재료 질문인데 납품대금 연동 약정을 다루지 않음, 지급 기한 질문인데 어음·지연이자 규정이 없음,
       대금 인하 질문인데 감액 금지 규정이 없음

보충 질문을 내지 않는 경우
- 이미 근거 목록에 있는 내용이 답에서 빠졌을 뿐인 경우 (검색 문제가 아니다)
- 아래 도구로 찾을 수 없는 것: 일반 상식, 의견, 시장 전망, 사용자가 알려 줘야 하는 사실(계약 날짜, 합의 여부 등)
- 질문과 관계가 먼 내용, 있으면 좋은 정도의 덧붙임
- 확실하지 않으면 내지 않는다. complete=true가 기본이다

찾을 수 있는 도구 (보충 질문은 이 중 하나로 답할 수 있어야 한다)
{_tool_capabilities()}

보충 질문 작성
- 최대 {MAX_FOLLOW_UPS}개. 같은 내용을 두 번 묻지 않는다
- 질문 하나만 보고도 검색할 수 있게 쓴다. 협력사 ID(SUP-0003), RFQ 번호(RFQ-2026-001), 법령 이름처럼 원래 질문에 있던 구체 값을 넣는다
- 이미 실행한 하위 질문과 같은 질문을 다시 내지 않는다
JSON으로만 답한다."""


def build_user_message(decomposition: DecompositionResult, answer: Answer, statuses: dict[str, str]) -> str:
    done = [{"id": sq.id, "intent": sq.intent, "question": sq.question, "status": statuses.get(sq.id, "-")}
            for sq in decomposition.sub_queries]
    evidence = "\n".join(f"- {e.id} {e.label}" for e in answer.evidence) or "(근거 없음)"
    return (f"<question>{decomposition.original_query}</question>\n"
            f"<sub_queries>{json.dumps(done, ensure_ascii=False)}</sub_queries>\n"
            f"<evidence>\n{evidence}\n</evidence>\n"
            f"<answer>\n{answer.text[:ANSWER_CHARS]}\n</answer>")


class Backend(Protocol):
    async def generate(self, system: str, user: str) -> str | None: ...


class CriticAnthropicBackend(AnthropicBackend):
    OUTPUT_FORMAT = {"type": "json_schema", "schema": anthropic.transform_schema(Critique.model_json_schema())}


class CriticOpenAIBackend(OpenAIBackend):
    OUTPUT_MODEL = Critique
    OUTPUT_NAME = Critique.__name__
    OUTPUT_SCHEMA = to_strict_json_schema(Critique)
    COMPACT_OUTPUT = False

    @staticmethod
    def to_contract(text: str) -> str:
        return text


class CriticOllamaBackend(OllamaBackend):
    OUTPUT_SCHEMA = Critique.model_json_schema()


def _env_backend() -> str:
    return os.getenv("NEGOPILOT_CRITIC_BACKEND", os.getenv("NEGOPILOT_GENERATOR_BACKEND", "openai"))


@dataclass(frozen=True)
class CriticSettings:
    backend: str = field(default_factory=_env_backend)
    model: str = field(default_factory=lambda: os.getenv(
        "NEGOPILOT_CRITIC_MODEL", DEFAULT_MODELS.get(_env_backend(), DEFAULT_MODELS["openai"])))
    effort: str = field(default_factory=lambda: os.getenv("NEGOPILOT_CRITIC_EFFORT", "low"))
    max_tokens: int = 2000
    use_fallbacks: bool = True


def critic_enabled() -> bool:
    return os.getenv("NEGOPILOT_REVIEW_CRITIC", "1") != "0"


def make_backend(settings: CriticSettings) -> tuple[Backend, Callable[[], Awaitable[None]]]:
    """(백엔드, 클라이언트를 닫는 함수)"""
    if settings.backend == "ollama":
        backend = CriticOllamaBackend.from_env(settings.model, settings.max_tokens)
        return backend, backend.http.aclose
    if settings.backend == "openai":
        client = openai.AsyncOpenAI(max_retries=0)
        return CriticOpenAIBackend(client, settings.model, settings.max_tokens), client.close
    if settings.backend == "anthropic":
        client = anthropic.AsyncAnthropic(max_retries=0)
        return (CriticAnthropicBackend(client, settings.model, settings.effort, settings.max_tokens,
                                       settings.use_fallbacks), client.close)
    raise ValueError(f"알 수 없는 백엔드 '{settings.backend}'. anthropic 또는 openai 또는 ollama")


class Critic:
    def __init__(self, backend: Backend | None = None, settings: CriticSettings | None = None):
        self.settings = settings or CriticSettings()
        self._backend = backend
        self._close: Callable[[], Awaitable[None]] | None = None

    @property
    def backend(self) -> Backend:
        # 검토할 답이 없으면(되묻기·거절 등) API 클라이언트를 만들지 않는다
        if self._backend is None:
            self._backend, self._close = make_backend(self.settings)
        return self._backend

    async def aclose(self) -> None:
        """이 검토자가 만든 클라이언트만 닫는다."""
        close, self._close = self._close, None
        if close is not None:
            try:
                async with asyncio.timeout(5):
                    await close()
            except Exception:  # noqa: BLE001 - 닫기 실패는 답에 영향이 없다
                pass

    async def critique(self, decomposition: DecompositionResult, answer: Answer,
                       statuses: dict[str, str]) -> Critique:
        """실패(거절, 형식 오류, 모델 오류)는 보충 없음으로 본다. 검토자가 답 자체를 망가뜨리지 않게 한다."""
        try:
            text = await self.backend.generate(SYSTEM_PROMPT, build_user_message(decomposition, answer, statuses))
            critique = Critique.model_validate_json(text) if text else Critique(complete=True, missing=[])
        except (ValidationError, ValueError, openai.OpenAIError, anthropic.AnthropicError) as error:
            log.warning("검토자 실패, 보충 없이 끝냄: %s", type(error).__name__)
            return Critique(complete=True, missing=[])
        missing = [m for m in critique.missing if m.question.strip()][:MAX_FOLLOW_UPS]
        return Critique(complete=critique.complete or not missing, missing=[] if critique.complete else missing)
