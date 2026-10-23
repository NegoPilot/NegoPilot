"""답변 생성기: DecompositionResult + RouteResult → Answer.

status별 처리는 Routing 결과 계약(docs/contracts/routing.md) 3절을 따른다.

모델을 부를 필요가 없는 경우(되묻기, 실패, 거절, 결과 없음)는 정해진 문구로 답한다.
비용이 없고, 같은 상황에 늘 같은 안내가 나가며, 평가할 때도 모델 품질과 섞이지 않는다.

모델로 답할 때는
1. Routing 결과를 근거 목록(E1, E2 ...)으로 펼친다 (evidence.py)
2. 모델이 근거 id를 붙여 초안을 쓴다
3. 근거에 없는 인용·숫자·조문이 있으면 오류를 알려 주고 1회 다시 쓰게 한다 (verify.py)
4. 그래도 남으면 그 값을 "[확인 필요]"로 가린다
5. [E3] → [1] 번호로 바꾸고 인용 목록을 붙인다

사용자에게 보일 문장은 전부 여기서 쓴다. Routing의 message·halt_reason·error는 로그로만 남긴다 (계약 1절 7).
"""

import logging
import os
from dataclasses import dataclass, field
from typing import Literal

import anthropic
import openai
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from negopilot_shared import DecompositionResult, RouteResult, SubQueryOutcome

from negopilot_agent.generation import verify
from negopilot_agent.generation.answer import Answer, Evidence, GenerationDraft
from negopilot_agent.generation.backends import Backend, GenerationAnthropicBackend, GenerationOpenAIBackend, GenerationOllamaBackend
from negopilot_agent.generation.evidence import build_evidence
from negopilot_agent.generation.prompt import SYSTEM_PROMPT, build_user_message

log = logging.getLogger(__name__)

DEFAULT_MODELS = {"anthropic": "claude-opus-5-5", "openai": "gpt-4.1-mini", "ollama": "qwen3:8b"}

# missing_args일 때 되물을 항목 이름 (엔티티 사전 → 사람 말). 계약 5절 미정 항목: 위치는 Routing과 상의
ENTITY_LABELS = {
    "rfq_id": "RFQ 번호(예: RFQ-2026-001)",
    "supplier": "협력사 이름이나 ID(예: SUP-0003)",
    "oem_id": "완성차 업체",
    "process": "공정(다이캐스팅·프레스·사출·단조·CNC가공)",
    "material": "소재",
    "region": "지역",
    "unit_price": "제안 단가(원)",
    "payment_days": "결제 기간(일)",
    "price_linkage": "납품대금 연동 약정을 넣을지 여부",
    "raw_material_ratio": "단가 중 원재료 비중",
    "contract_days": "거래 기간(일)",
    "law": "법령 이름",
    "article_no": "조문 번호(예: 제13조)",
}

CAPABILITIES = ("협력사 검색·순위 추천, 협력사 상세 정보, RFQ 조회, 협상안(단가·결제 기간·연동)의 하도급법 검사, "
                "하도급법·상생협력법 조문 조회")

FAILED_TEXT = "질문을 처리하는 중에 문제가 생겼습니다. 잠시 후 다시 시도해 주세요."
GENERATION_FAILED_TEXT = "답변을 만드는 중에 문제가 생겼습니다. 잠시 후 다시 시도해 주세요."
REFUSAL_TEXT = f"죄송하지만 그 질문은 NegoPilot이 도와드릴 수 있는 범위 밖입니다. 제가 할 수 있는 일은 {CAPABILITIES}입니다."
NO_RESULT_TEXT = "요청하신 내용을 확인하지 못했습니다."
SUPPLIER_SELECTION_TEXT = "이름이 일치하는 협력사가 여러 곳입니다. 조회할 협력사를 선택해 주세요."
ARTICLE_LOOKUP_NOTICE = "검색된 법령 근거로 답변했습니다. 일부 조문 전문은 가져오지 못했습니다."
SUPPLIER_LOOKUP_NOTICE = "일부 협력사 상세 정보를 가져오지 못했습니다. 확인된 자료로 답변했습니다."
MAX_SUPPLIER_OPTIONS = 10


class _SupplierCandidate(BaseModel):
    model_config = ConfigDict(strict=True)

    supplier_id: str = Field(pattern=r"^SUP-[0-9]{4,}$")
    name: str = Field(min_length=1, max_length=200, pattern=r"^[^\x00-\x1f\x7f]+$")


class _SupplierMatches(BaseModel):
    model_config = ConfigDict(strict=True)

    matches: list[_SupplierCandidate]
    total: int = Field(ge=0)
    match_type: Literal["id", "exact", "partial"]


def supplier_options(outcome: SubQueryOutcome) -> list[str]:
    """Read selection labels only from a successful structured supplier lookup."""
    if outcome.intent != "supplier_detail" or outcome.status != "missing_args" or "supplier" not in outcome.missing:
        return []
    options: dict[str, str] = {}
    for call in outcome.calls:
        if call.server != "business-mcp" or call.tool != "find_suppliers" or call.error is not None:
            continue
        try:
            result = _SupplierMatches.model_validate(call.result)
        except ValidationError:
            continue
        if result.total < max(2, len(result.matches)):
            continue
        for candidate in result.matches:
            name = candidate.name.strip()
            if name and candidate.supplier_id not in options:
                options[candidate.supplier_id] = f"{name} ({candidate.supplier_id})"
            if len(options) == MAX_SUPPLIER_OPTIONS:
                return list(options.values())
    return list(options.values()) if len(options) > 1 else []


def _env_backend() -> str:
    return os.getenv("NEGOPILOT_GENERATOR_BACKEND", "openai")


@dataclass(frozen=True)
class GeneratorSettings:
    backend: str = field(default_factory=_env_backend)  # anthropic | openai | ollama
    model: str = field(default_factory=lambda: os.getenv(
        "NEGOPILOT_GENERATOR_MODEL", DEFAULT_MODELS.get(_env_backend(), DEFAULT_MODELS["openai"])))
    # 근거를 엮어 쓰는 작업이라 분해(low)보다 높게 시작한다. 평가 결과를 보고 조정한다 (anthropic 전용)
    effort: str = field(default_factory=lambda: os.getenv("NEGOPILOT_GENERATOR_EFFORT", "medium"))
    max_tokens: int = 16000
    max_attempts: int = 2  # 첫 시도 + 근거 검사·형식 오류 시 재시도 1회
    use_fallbacks: bool = field(
        default_factory=lambda: os.getenv("NEGOPILOT_GENERATOR_FALLBACKS", "1") != "0")


def make_backend(settings: GeneratorSettings, client: anthropic.AsyncAnthropic | None = None) -> Backend:
    if settings.backend == "ollama":
        return GenerationOllamaBackend.from_env(settings.model, settings.max_tokens)
    if settings.backend == "openai":
        return GenerationOpenAIBackend(openai.AsyncOpenAI(), settings.model, settings.max_tokens)  # OPENAI_API_KEY
    if settings.backend == "anthropic":
        return GenerationAnthropicBackend(client or anthropic.AsyncAnthropic(), settings.model, settings.effort,
                                          settings.max_tokens, settings.use_fallbacks)
    raise ValueError(f"알 수 없는 백엔드 '{settings.backend}'. anthropic 또는 openai 또는 ollama")


@dataclass
class Plan:
    """Routing 결과를 보고 정한 답변 방향."""

    notices: list[str] = field(default_factory=list)       # 답변 끝에 모을 안내 (계약 1절 3)
    missing: list[str] = field(default_factory=list)       # 되물을 엔티티
    options: list[str] = field(default_factory=list)       # 구조화된 협력사 검색 결과에서 만든 선택지
    out_of_scope: bool = False
    general_knowledge: bool = False
    answerable: int = 0                                     # 근거로 답할 수 있는 하위 질문 수


def plan(decomposition: DecompositionResult, routing: RouteResult) -> Plan:
    p = Plan(notices=[f"가정: {a}" for a in decomposition.assumptions])
    texts = {sq.id: sq.original_text for sq in decomposition.sub_queries}
    statuses = {o.status for o in routing.outcomes}
    for o in routing.outcomes:
        part = f"'{texts.get(o.id, o.id)}'"
        if o.message:
            log.info("하위 질문 %s %s: %s", o.id, o.status, o.message)
        match o.status:
            case "ok":
                p.answerable += 1
                if (o.intent == "law_check"
                        and any(c.server == "law-mcp" and c.tool == "search_regulation"
                                and c.error is None and isinstance(c.result, list) and c.result for c in o.calls)
                        and any(c.server == "law-mcp" and c.tool == "get_article" and c.error is not None
                                for c in o.calls)
                        and ARTICLE_LOOKUP_NOTICE not in p.notices):
                    p.notices.append(ARTICLE_LOOKUP_NOTICE)
                if (o.intent == "supplier_detail"
                        and any(c.server == "business-mcp" and c.tool == "get_supplier"
                                and c.error is None and isinstance(c.result, dict)
                                and all(isinstance(c.result.get(key), str) and c.result[key].strip()
                                        for key in ("supplier_id", "name")) for c in o.calls)
                        and any(c.server == "business-mcp" and c.tool == "get_supplier" and c.error is not None
                                for c in o.calls)
                        and SUPPLIER_LOOKUP_NOTICE not in p.notices):
                    p.notices.append(SUPPLIER_LOOKUP_NOTICE)
            case "general_knowledge":
                p.general_knowledge = True
            case "out_of_scope":
                p.out_of_scope = True
            case "unsupported":
                p.notices.append(f"{part}: 메일 작성은 아직 지원하지 않습니다." if o.intent == "draft_email"
                                 else f"{part}: 아직 지원하지 않는 기능입니다.")
            case "missing_args":
                p.missing += [m for m in o.missing if m not in p.missing]
                p.options += [option for option in supplier_options(o) if option not in p.options]
                p.options = p.options[:MAX_SUPPLIER_OPTIONS]
            case "empty":
                p.notices.append(f"{part}: 조건에 맞는 결과가 없습니다.")
            case "error":
                # 오류 원문은 로그로만. 내부 주소나 예외 이름이 화면에 나가지 않게
                log.warning("하위 질문 %s 도구 오류: %s", o.id, [c.error for c in o.calls if c.error])
                p.notices.append(f"{part} 부분은 자료를 가져오지 못했습니다.")
            case "skipped":
                # 원인(empty, error)을 이미 안내했으면 따로 말하지 않는다. 그 밖의 경우만 짧게
                if not statuses & {"empty", "error"}:
                    p.notices.append(f"{part} 부분은 앞 단계 결과가 없어 확인하지 못했습니다.")
    if routing.halt_reason:
        log.info("Routing 중단: %s", routing.halt_reason)
    return p


def ask_missing(missing: list[str]) -> str:
    items = ", ".join(ENTITY_LABELS.get(m, m) for m in missing)
    return f"정확히 답하려면 {items} 정보가 더 필요합니다. 알려 주시면 이어서 확인하겠습니다."


def clarification_text(p: Plan) -> str:
    if not p.options:
        return ask_missing(p.missing)
    other_missing = [item for item in p.missing if item != "supplier"]
    return SUPPLIER_SELECTION_TEXT + (" " + ask_missing(other_missing) if other_missing else "")


def with_notices(text: str, notices: list[str]) -> str:
    return text if not notices else text + "\n\n" + "\n".join(f"※ {n}" for n in notices)


class AnswerGenerator:
    def __init__(self, client: anthropic.AsyncAnthropic | None = None,
                 settings: GeneratorSettings | None = None, backend: Backend | None = None):
        self.settings = settings or GeneratorSettings()
        self._client = client
        self._backend = backend

    @property
    def backend(self) -> Backend:
        # 모델 없이 끝나는 경우(되묻기·거절 등)에는 API 클라이언트를 만들지 않는다
        if self._backend is None:
            self._backend = make_backend(self.settings, self._client)
        return self._backend

    async def generate(self, decomposition: DecompositionResult, routing: RouteResult) -> Answer:
        # not_routed면 Routing 결과는 보지 않고 분해 status로 처리한다 (계약 3절)
        if routing.status == "not_routed":
            c = decomposition.clarification
            if decomposition.status == "clarify" and c:
                return Answer(kind="clarify", text=c.question, options=list(c.options))
            return Answer(kind="failed", text=FAILED_TEXT)

        p = plan(decomposition, routing)
        if p.out_of_scope and not (p.answerable or p.general_knowledge):
            return Answer(kind="refusal", text=with_notices(REFUSAL_TEXT, p.notices), notices=p.notices)
        if p.out_of_scope:
            p.notices.append(f"질문 중 일부는 범위 밖이라 답하지 않았습니다. 제가 할 수 있는 일은 {CAPABILITIES}입니다.")

        if not (p.answerable or p.general_knowledge):
            if p.missing:
                return Answer(kind="clarify", text=with_notices(clarification_text(p), p.notices),
                              options=p.options, notices=p.notices)
            return Answer(kind="no_result", text=with_notices(NO_RESULT_TEXT, p.notices), notices=p.notices)

        if p.missing:
            p.notices.append(clarification_text(p))
        evidence = build_evidence(routing, {sq.id: sq.question for sq in decomposition.sub_queries})
        return await self._write(decomposition, evidence, p)

    async def _write(self, decomposition: DecompositionResult, evidence: list[Evidence], p: Plan) -> Answer:
        user = build_user_message(decomposition, evidence, p.notices)
        allowed = [decomposition.original_query, *decomposition.assumptions,
                   *(sq.question for sq in decomposition.sub_queries)]
        extra = ""
        draft: GenerationDraft | None = None
        report = None
        for attempt in range(1, self.settings.max_attempts + 1):
            text = await self.backend.generate(SYSTEM_PROMPT, user + extra)
            if text is None:   # 거절 또는 잘림
                break
            try:
                draft = GenerationDraft.model_validate_json(text)
            except ValidationError as err:
                log.warning("생성 결과 형식 오류 (시도 %d): %s", attempt, err.error_count())
                extra = "\n\n직전 출력이 JSON 형식을 어겼다. {\"answer\": ..., \"used_evidence\": [...]} 형식으로 다시 출력해라."
                draft = None
                continue
            report = verify.check(draft.answer, evidence, allowed)
            report.attempts = attempt
            if any(e.kind == "company_size" for e in evidence) and not verify.CITE_RE.search(draft.answer):
                # Size decisions must retain inspectable evidence in the web UI.
                if attempt == self.settings.max_attempts:
                    return Answer(kind="failed", text=GENERATION_FAILED_TEXT, evidence=evidence)
                extra = "\n\n기업 규모 판정의 근거 인용이 빠졌다. 각 판정 문장 끝에 실제 근거 id [E1] 등의 인용을 붙여 다시 작성해라."
                continue
            if report.ok:
                break
            log.info("근거 검사 실패 (시도 %d): %s", attempt, report.model_dump(exclude={"attempts", "masked"}))
            extra = "\n\n" + verify.feedback(report)

        if draft is None:
            return Answer(kind="failed", text=GENERATION_FAILED_TEXT, evidence=evidence)

        body = draft.answer
        if report and not report.ok:
            body = verify.mask(body, report)
            report.masked = True
        body, citations = verify.render_citations(body, evidence)
        return Answer(kind="answer", text=with_notices(body, p.notices), citations=citations,
                      options=p.options, grounded=bool(citations), notices=p.notices,
                      evidence=evidence, verification=report)
