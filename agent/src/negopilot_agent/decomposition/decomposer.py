"""쿼리 분해기. 모델에서 분해 결과 JSON을 받아 계약(DecompositionResult)으로 검증한다.

계약(docs/contracts/query-decomposition.md)의 실패 처리:
- 형식 검증에 실패하면 오류 내용을 알려 주고 1회 재시도한다
- 그래도 실패하거나 모델이 거절하면 status "failed"를 돌려준다. 추측해서 넘기지 않는다
- API 장애(네트워크, 5xx, 429)는 재시도한 뒤에도 실패하면 예외로 올려 Harness의 Recovery가 처리한다
"""

import json
import logging
import re
import os
from dataclasses import dataclass, field
from datetime import date

import anthropic
import openai
from pydantic import ValidationError

from negopilot_shared import DecompositionResult, load_intents

from negopilot_agent.decomposition.backends import AnthropicBackend, Backend, OpenAIBackend, OllamaBackend
from negopilot_agent.decomposition.prompt import build_system_prompt, build_user_message

log = logging.getLogger(__name__)

DEFAULT_MODELS = {"anthropic": "claude-opus-5-5", "openai": "gpt-4.1-mini", "ollama": "qwen3:8b"}


def _env_backend() -> str:
    return os.getenv("NEGOPILOT_DECOMPOSER_BACKEND", "openai")


@dataclass(frozen=True)
class DecomposerSettings:
    backend: str = field(default_factory=_env_backend)  # anthropic | openai | ollama
    model: str = field(default_factory=lambda: os.getenv(
        "NEGOPILOT_DECOMPOSER_MODEL", DEFAULT_MODELS.get(_env_backend(), DEFAULT_MODELS["openai"])))
    # 분류에 가까운 작업이라 low로 시작한다. 평가 결과를 보고 조정한다 (anthropic 전용)
    effort: str = field(default_factory=lambda: os.getenv("NEGOPILOT_DECOMPOSER_EFFORT", "low"))
    max_tokens: int = 16000
    max_attempts: int = 2  # 첫 시도 + 형식 오류 시 재시도 1회
    # 안전 분류기가 거절하면 서버가 다른 모델로 다시 실행하게 한다 (anthropic 전용)
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


def make_backend(settings: DecomposerSettings, client: anthropic.AsyncAnthropic | None = None) -> Backend:
    if settings.backend == "ollama":
        return OllamaBackend.from_env(settings.model, settings.max_tokens)
    if settings.backend == "openai":
        return OpenAIBackend(openai.AsyncOpenAI(), settings.model, settings.max_tokens)  # OPENAI_API_KEY
    if settings.backend == "anthropic":
        return AnthropicBackend(client or anthropic.AsyncAnthropic(), settings.model, settings.effort,
                                settings.max_tokens, settings.use_fallbacks)
    raise ValueError(f"알 수 없는 백엔드 '{settings.backend}'. anthropic 또는 openai 또는 ollama")


class QueryDecomposer:
    def __init__(self, client: anthropic.AsyncAnthropic | None = None,
                 settings: DecomposerSettings | None = None, backend: Backend | None = None):
        self.settings = settings or DecomposerSettings()
        self.backend = backend or make_backend(self.settings, client)
        self.system = build_system_prompt()

    async def decompose(self, query: str, history: list[dict] | None = None, *,
                        context: dict[str, list[str]] | None = None) -> DecompositionResult:
        today = date.today()
        user_message = (build_user_message(query, history, today) if context is None
                        else build_user_message(query, history, today, context=context))
        feedback = ""
        for attempt in range(1, self.settings.max_attempts + 1):
            text = await self.backend.generate(self.system, user_message + feedback)
            if text is None:  # 거절 또는 잘림
                return failed_result(query)
            try:
                # 스키마 형식은 모델 쪽 구조화 출력이 맞추고, 필드 사이 규칙은 여기서 검증한다
                cleaned = drop_redundant_as_of(
                    clean_entity_values(drop_disallowed_entities(drop_disallowed_inputs(text))), today)
                result = DecompositionResult.model_validate_json(cleaned)
            except ValidationError as err:
                log.warning("분해 결과 형식 오류 (시도 %d): %s", attempt, err.error_count())
                feedback = ("\n\n직전 출력이 계약 형식을 어겼다. 아래 오류를 고쳐서 다시 출력해라.\n"
                            + _format_errors(err))
                continue
            # original_query는 사용자 원문이어야 한다. 모델이 다듬었으면 원문으로 되돌린다
            if result.original_query != query:
                result = result.model_copy(update={"original_query": query})
            return drop_unused_dependencies(result)
        return failed_result(query)


def drop_disallowed_inputs(text: str) -> str:
    """intent가 허용하지 않는 slot의 inputs를 검증 전에 지운다.

    예: law_check에 supplier를 앞 결과에서 받겠다는 inputs. 법 검색 도구는 협력사를 입력으로 받지 않아
    쓸모가 없는 값이고, 이 하나 때문에 재시도하면 호출만 늘어난다. 남은 depends_on은 drop_unused_dependencies가 정리한다.
    JSON이 아니거나 모양이 다르면 그대로 돌려줘 검증 오류로 처리되게 한다.
    """
    try:
        data = json.loads(text)
        sub_queries = data["sub_queries"]
    except (ValueError, TypeError, KeyError):
        return text
    intents = load_intents()
    changed = False
    for sq in sub_queries if isinstance(sub_queries, list) else []:
        spec = intents.get(sq.get("intent")) if isinstance(sq, dict) else None
        if spec is None or not isinstance(sq.get("inputs"), list):
            continue
        allowed = set(spec.entities) | {"price_change_pct"}
        kept = [i for i in sq["inputs"] if not isinstance(i, dict) or i.get("slot") in allowed]
        if len(kept) != len(sq["inputs"]):
            changed = True
            dropped = [i.get("slot") for i in sq["inputs"] if i not in kept]
            log.info("%s(%s)의 inputs에서 허용되지 않는 slot %s 제거", sq.get("id"), spec.intent, dropped)
            sq["inputs"] = kept
    return json.dumps(data, ensure_ascii=False) if changed else text


# intent가 받지 않아도 지워도 되는 엔티티: 대상(누구, 어느 RFQ)과 기준일만 가리키고 intent의 뜻을 바꾸지 않는다.
# 협상 조건(payment_days, unit_price ...)이나 검색 조건이 엉뚱한 intent에 붙으면 intent를 잘못 고른 신호일 수 있어
# 지우지 않고 검증 오류로 재시도한다 (test_wrong_legal_entity_slot_is_still_rejected_then_retried).
DROPPABLE_ENTITIES = {"rfq_id", "supplier", "oem_id", "as_of"}


def drop_disallowed_entities(text: str) -> str:
    """intent가 받지 않는 대상·기준일 엔티티(DROPPABLE_ENTITIES)를 검증 전에 지운다.

    strict 구조화 출력(openai)은 모든 필드를 채우게 해서, law_check에 rfq_id처럼 그 intent의 도구가 쓰지 않는
    값이 들어간다. 이 하나 때문에 재시도하면 호출만 늘어난다 (#58).
    JSON이 아니거나 모양이 다르면 그대로 돌려줘 검증 오류로 처리되게 한다.
    """
    try:
        data = json.loads(text)
        sub_queries = data["sub_queries"]
    except (ValueError, TypeError, KeyError):
        return text
    intents = load_intents()
    changed = False
    for sq in sub_queries if isinstance(sub_queries, list) else []:
        entities = sq.get("entities") if isinstance(sq, dict) else None
        spec = intents.get(sq.get("intent")) if isinstance(sq, dict) else None
        if not isinstance(entities, dict) or spec is None:
            continue
        allowed = set(spec.entities) | {"price_change_pct"}
        extra = [k for k, v in entities.items() if v is not None and k in DROPPABLE_ENTITIES and k not in allowed]
        if extra:
            changed = True
            log.info("%s(%s)의 엔티티 %s 제거 (intent가 받지 않음)", sq.get("id"), spec.intent, extra)
            for k in extra:
                del entities[k]
    return json.dumps(data, ensure_ascii=False) if changed else text


_JSON_JUNK = re.compile(r'^[\s"\'{}\[\],]+|[\s"\'{}\[\],]+$')


def clean_entity_values(text: str) -> str:
    """엔티티 값을 검증 전에 다듬는다 (#72).

    - 문자열 값 앞뒤에 붙은 JSON 기호를 지운다. strict 구조화 출력이 가끔 `"SUP-0015},"`처럼 값에 `},`를 섞는다.
      협력사 ID·이름·지역 같은 값은 이런 기호로 시작하거나 끝나지 않는다.
    - price_change_pct는 지운다. 계약상 허용하지만 지금 Routing·Generation 어디서도 쓰지 않고(#62), 프롬프트로
      막아도 모델이 계속 넣어 부호 오류(-100 등)만 생긴다. 증감률은 question에 남아 있다.
    JSON이 아니거나 모양이 다르면 그대로 돌려줘 검증 오류로 처리되게 한다.
    """
    try:
        data = json.loads(text)
        sub_queries = data["sub_queries"]
    except (ValueError, TypeError, KeyError):
        return text
    changed = False
    for sq in sub_queries if isinstance(sub_queries, list) else []:
        entities = sq.get("entities") if isinstance(sq, dict) else None
        if not isinstance(entities, dict):
            continue
        if entities.get("price_change_pct") is not None:
            del entities["price_change_pct"]
            changed = True
        for k, v in entities.items():
            if isinstance(v, str) and (fixed := _JSON_JUNK.sub("", v)) != v and fixed:
                log.info("%s의 %s 값 %r → %r (JSON 기호 제거)", sq.get("id"), k, v, fixed)
                entities[k] = fixed
                changed = True
    return json.dumps(data, ensure_ascii=False) if changed else text


def drop_redundant_as_of(text: str, today: date) -> str:
    """entities.as_of가 오늘 날짜이면 검증 전에 지운다.

    as_of는 생략하면 오늘 기준이라, 사용자 메시지에 넣은 오늘 날짜를 모델이 채워도 조회 결과는 같다.
    strict 구조화 출력(openai)은 모든 필드를 채우게 해 이런 값이 자주 생긴다. 사용자가 적은 다른 날짜는 그대로 둔다.
    as_of를 받지 않는 intent의 as_of는 drop_disallowed_entities가 먼저 지운다.
    JSON이 아니거나 모양이 다르면 그대로 돌려줘 검증 오류로 처리되게 한다.
    """
    try:
        data = json.loads(text)
        sub_queries = data["sub_queries"]
    except (ValueError, TypeError, KeyError):
        return text
    changed = False
    for sq in sub_queries if isinstance(sub_queries, list) else []:
        entities = sq.get("entities") if isinstance(sq, dict) else None
        if not isinstance(entities, dict) or entities.get("as_of") != today.isoformat():
            continue
        changed = True
        log.info("%s의 as_of %s 제거 (오늘 날짜)", sq.get("id"), entities["as_of"])
        del entities["as_of"]
    return json.dumps(data, ensure_ascii=False) if changed else text


def drop_unused_dependencies(result: DecompositionResult) -> DecompositionResult:
    """inputs로 값을 받지 않는 depends_on을 지운다.

    계약상 depends_on은 앞 결과가 도구 입력으로 필요할 때만 쓰고, 값을 넘기는 방법은 inputs뿐이다.
    값을 받지 않는 의존은 병렬로 돌 수 있는 질문을 순서대로 묶어 느려지게만 하므로 코드로 정리한다.
    """
    changed = False
    sub_queries = []
    for sq in result.sub_queries:
        used = {i.from_ for i in sq.inputs}
        kept = [d for d in sq.depends_on if d in used]
        if kept != sq.depends_on:
            changed = True
            log.info("%s의 depends_on %s → %s (inputs로 받는 값 없음)", sq.id, sq.depends_on, kept)
        sub_queries.append(sq.model_copy(update={"depends_on": kept}))
    return result.model_copy(update={"sub_queries": sub_queries}) if changed else result
