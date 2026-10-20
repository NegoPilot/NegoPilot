"""쿼리 분해기. Claude 구조화 출력으로 DecompositionResult를 받는다.

계약(docs/contracts/query-decomposition.md)의 실패 처리:
- 형식 검증에 실패하면 오류 내용을 알려 주고 1회 재시도한다
- 그래도 실패하거나 모델이 거절하면 status "failed"를 돌려준다. 추측해서 넘기지 않는다
- API 장애(네트워크, 5xx, 429)는 SDK가 재시도한 뒤에도 실패하면 예외로 올려 Harness의 Recovery가 처리한다
"""

import logging
import os
from dataclasses import dataclass, field

import anthropic
from pydantic import ValidationError

from negopilot_shared import DecompositionResult

from negopilot_agent.decomposition.prompt import build_system_prompt, build_user_message

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
# 구조화 출력 스키마. SDK가 API에서 지원하는 형태로 바꿔 준다
OUTPUT_FORMAT = {"type": "json_schema",
                 "schema": anthropic.transform_schema(DecompositionResult.model_json_schema())}


@dataclass(frozen=True)
class DecomposerSettings:
    model: str = field(default_factory=lambda: os.getenv("NEGOPILOT_DECOMPOSER_MODEL", "claude-opus-5-5"))
    # 분류에 가까운 작업이라 low로 시작한다. 평가 결과를 보고 조정한다
    effort: str = field(default_factory=lambda: os.getenv("NEGOPILOT_DECOMPOSER_EFFORT", "low"))
    max_tokens: int = 16000
    max_attempts: int = 2  # 첫 시도 + 형식 오류 시 재시도 1회
    # 안전 분류기가 거절하면 서버가 다른 모델로 다시 실행하게 한다
    use_fallbacks: bool = field(
        default_factory=lambda: os.getenv("NEGOPILOT_DECOMPOSER_FALLBACKS", "1") != "0")


def failed_result(query: str) -> DecompositionResult:
    return DecompositionResult(status="failed", original_query=query)


def _format_errors(err: ValidationError) -> str:
    lines = []
    for e in err.errors()[:10]:
        loc = ".".join(str(p) for p in e["loc"]) or "(전체)"
        lines.append(f"- {loc}: {e['msg']}")
    return "\n".join(lines)


class QueryDecomposer:
    def __init__(self, client: anthropic.AsyncAnthropic | None = None,
                 settings: DecomposerSettings | None = None):
        self.client = client or anthropic.AsyncAnthropic()
        self.settings = settings or DecomposerSettings()
        # 요청마다 바뀌지 않는 부분이라 캐시한다
        self.system = [{"type": "text", "text": build_system_prompt(),
                        "cache_control": {"type": "ephemeral"}}]

    async def decompose(self, query: str, history: list[dict] | None = None) -> DecompositionResult:
        user_message = build_user_message(query, history)
        feedback = ""
        for attempt in range(1, self.settings.max_attempts + 1):
            content = user_message + feedback
            try:
                result = await self._call(content)
            except ValidationError as err:
                log.warning("분해 결과 형식 오류 (시도 %d): %s", attempt, err.error_count())
                feedback = ("\n\n직전 출력이 계약 형식을 어겼다. 아래 오류를 고쳐서 다시 출력해라.\n"
                            + _format_errors(err))
                continue
            if result is None:
                return failed_result(query)
            # original_query는 사용자 원문이어야 한다. 모델이 다듬었으면 원문으로 되돌린다
            if result.original_query != query:
                result = result.model_copy(update={"original_query": query})
            return result
        return failed_result(query)

    async def _call(self, content: str) -> DecompositionResult | None:
        """한 번 호출한다. 거절·잘림이면 None, 형식이 계약에 안 맞으면 ValidationError."""
        kwargs = {}
        if self.settings.use_fallbacks:
            kwargs = {"betas": [FALLBACK_BETA], "fallbacks": "default"}
        response = await self.client.beta.messages.create(
            model=self.settings.model,
            max_tokens=self.settings.max_tokens,
            system=self.system,
            messages=[{"role": "user", "content": content}],
            output_config={"effort": self.settings.effort, "format": OUTPUT_FORMAT},
            **kwargs,
        )
        # 거절이나 잘림은 본문을 읽기 전에 확인한다 (이때 본문은 비었거나 미완성 JSON이다)
        if response.stop_reason == "refusal":
            details = response.stop_details
            log.warning("분해 요청 거절: %s", getattr(details, "category", None))
            return None
        if response.stop_reason == "max_tokens":
            log.warning("분해 출력이 max_tokens에서 잘림")
            return None
        text = next((b.text for b in response.content if b.type == "text"), "")
        # 스키마 형식은 API가 맞춰 주고, 필드 사이 규칙(상태, 의존 관계, intent별 엔티티)은 여기서 검증한다
        return DecompositionResult.model_validate_json(text)
