"""검토 에이전트: 생성한 답과 도구 결과를 보고, 근거를 더 찾으면 나아지는 경우에만 한 번 더 찾게 한다.

분해 → 라우트 → 생성 뒤에 붙는다. 판단은 모델 없이 규칙으로 한다.

보강 종류
1. retry_error: 도구 호출이 일시 오류로 실패한 하위 질문을 다시 실행하고 답을 다시 쓴다.
   RFQ 없음, 협력사 없음처럼 공개 오류 코드가 붙은 실패는 입력 문제라 다시 불러도 같으므로 제외한다.
2. suggest: 협력사 검색이 0건이면 조건을 넓힌 검색을 미리 해 보고, 결과가 있는 검색만 사용자에게 버튼으로 권한다.
   지역이 있으면 맞닿은 시·도부터, 그래도 없으면 지역 → 최소 월 생산능력 → IATF 인증 순으로 조건을 하나씩 뺀다.
   공정·소재는 질문의 핵심이라 빼지 않는다. 사용자가 고른 조건을 Agent가 마음대로 바꾸지 않고,
   바꿀지는 사용자가 버튼(answer.options)을 눌러 정한다. 답을 다시 쓰지 않으므로 모델 호출이 늘지 않는다.

라운드 수는 NEGOPILOT_REVIEW_ROUNDS(기본 1, 0이면 끔)를 넘지 않는다.
"""

import logging
import os
from typing import Any, Literal

from pydantic import BaseModel, Field

from negopilot_shared import DecompositionResult, RouteResult
from negopilot_shared.decomposition import SubQuery

from negopilot_agent.generation.answer import Answer
from negopilot_agent.routing.router import Router

log = logging.getLogger(__name__)

DEFAULT_ROUNDS = 1
# 빼도 되는 검색 조건 (뺄 순서). 값은 안내 문구에 쓸 이름
RELAXABLE = {"region": "지역", "min_monthly_capacity": "최소 월 생산능력", "iatf_required": "IATF 인증"}
# 시·도별 맞닿은 지역 (business-mcp 지역 약칭). 제주는 육지와 맞닿지 않아 뱃길이 가까운 곳을 둔다
ADJACENT = {
    "서울": ["경기", "인천"], "인천": ["서울", "경기"], "경기": ["서울", "인천", "강원", "충북", "충남"],
    "강원": ["경기", "충북", "경북"], "충북": ["경기", "강원", "경북", "대전", "세종", "충남", "전북"],
    "충남": ["경기", "충북", "세종", "대전", "전북"], "대전": ["충남", "충북", "세종"], "세종": ["충남", "충북", "대전"],
    "전북": ["충남", "충북", "경북", "경남", "전남"], "광주": ["전남"], "전남": ["광주", "전북", "경남"],
    "경북": ["강원", "충북", "전북", "경남", "대구", "울산"], "대구": ["경북", "경남"],
    "경남": ["부산", "울산", "대구", "경북", "전북", "전남"], "울산": ["부산", "경남", "경북"], "부산": ["울산", "경남"],
    "제주": ["전남", "부산"],
}
# 지역 약칭으로 바꿀 때 쓰는 긴 이름 (business-mcp resolve_region과 같은 규칙의 간단판)
REGION_NAMES = {"경상남": "경남", "경상북": "경북", "전라남": "전남", "전라북": "전북", "충청남": "충남", "충청북": "충북"}
MAX_OPTIONS = 3
BUTTON_HINT = "아래 버튼을 누르면 그 조건으로 다시 찾아볼 수 있습니다."


def configured_rounds() -> int:
    try:
        return max(0, int(os.getenv("NEGOPILOT_REVIEW_ROUNDS", DEFAULT_ROUNDS)))
    except ValueError:
        return DEFAULT_ROUNDS


ActionKind = Literal["retry_error", "suggest"]


class ReviewAction(BaseModel):
    kind: ActionKind
    sub_query: str
    reason: str                                         # 왜 보강하는지 (로그·트레이스용)
    detail: dict[str, Any] = Field(default_factory=dict)  # suggest: 넓힐 수 있는 조건, 결과가 있던 검색
    resolved: bool | None = None                        # 실행 뒤 결과가 나아졌는지. 실행 전에는 None

    def key(self) -> tuple[str, str]:
        return self.kind, self.sub_query


class ReviewRound(BaseModel):
    round: int
    actions: list[ReviewAction]
    regenerated: bool         # 보강 결과로 답을 다시 만들었는지


class Suggestion(BaseModel):
    """사용자에게 권할 다른 검색. 답 본문 끝 안내와 버튼(answer.options)으로 보인다."""

    note: str
    options: list[str]


class ReviewLog(BaseModel):
    rounds: list[ReviewRound] = Field(default_factory=list)
    pending: list[ReviewAction] = Field(default_factory=list)   # 검토가 정하고 아직 실행하지 않은 보강
    suggestions: list[Suggestion] = Field(default_factory=list)

    def tried(self) -> set[tuple[str, str]]:
        return {a.key() for r in self.rounds for a in r.actions}


# ---------- 판단 ----------

def plan(decomposition: DecompositionResult, routing: RouteResult | None, answer: Answer | None) -> list[ReviewAction]:
    """보강할 거리를 찾는다. 없으면 빈 목록."""
    if routing is None or routing.status != "routed":
        return []
    by_id = {sq.id: sq for sq in decomposition.sub_queries}
    actions: list[ReviewAction] = []
    for o in routing.outcomes:
        if o.status == "error" and not any(c.error_code for c in [*o.calls, *o.referenced_articles]):
            actions.append(ReviewAction(kind="retry_error", sub_query=o.id, reason="도구 호출 실패"))
        elif o.intent == "supplier_search" and o.status == "empty" and o.id in by_id:
            keys = [k for k in RELAXABLE if getattr(by_id[o.id].entities, k) is not None]
            if keys:
                actions.append(ReviewAction(kind="suggest", sub_query=o.id, reason="검색 결과 0건",
                                            detail={"relaxable": keys}))
    return actions


# ---------- 실행 ----------

async def reinforce(router: Router, decomposition: DecompositionResult, routing: RouteResult,
                    actions: list[ReviewAction]
                    ) -> tuple[RouteResult, list[ReviewAction], list[Suggestion], bool]:
    """보강을 실행한다. (라우팅, 결과를 적은 actions, 사용자에게 권할 검색, 답을 다시 쓸지)를 돌려준다."""
    done: list[ReviewAction] = []
    suggestions: list[Suggestion] = []
    changed = False

    retry = [a for a in actions if a.kind == "retry_error"]
    if retry:
        routing = await router.rerun(decomposition, routing, {a.sub_query for a in retry})
        status = {o.id: o.status for o in routing.outcomes}
        for a in retry:
            done.append(a.model_copy(update={"resolved": status[a.sub_query] != "error"}))
        # 다시 해도 실패면 답이 그대로이므로 다시 쓰지 않는다 (모델 호출 비용을 아낀다)
        changed = any(a.resolved for a in done)

    for a in (a for a in actions if a.kind == "suggest"):
        found = await _alternatives(router, decomposition, routing, a)
        if found:
            suggestions.append(found[0])
        done.append(a.model_copy(update={"resolved": bool(found), "detail": a.detail | (found[1] if found else {})}))

    return routing, done, suggestions, changed


async def _alternatives(router: Router, decomposition: DecompositionResult, routing: RouteResult,
                        action: ReviewAction) -> tuple[Suggestion, dict[str, Any]] | None:
    """조건을 넓힌 검색을 미리 해 보고, 결과가 있는 검색 문장만 돌려준다. 실제 답과 Routing 결과는 바꾸지 않는다."""
    sq = next(s for s in decomposition.sub_queries if s.id == action.sub_query)
    keys: list[str] = action.detail["relaxable"]

    async def has_results(update: dict[str, Any]) -> bool:
        result = await router.rerun(_with_entities(decomposition, sq, update), routing, {sq.id})
        return next(o for o in result.outcomes if o.id == sq.id).status == "ok"

    region = sq.entities.region
    if isinstance(region, str):
        nearby = [n for n in ADJACENT.get(_region_abbr(region), []) if await has_results({"region": n})]
        if nearby:
            nearby = nearby[:MAX_OPTIONS]
            # 앞 문장(결과 없음 안내)과 겹치지 않게 '없다'를 다시 말하지 않는다
            note = (f"{region}에는 없지만 인접 지역({', '.join(nearby)})에는 같은 조건의 협력사가 있습니다. "
                    f"{BUTTON_HINT}")
            return (Suggestion(note=note, options=[_search_sentence(sq, {"region": n}) for n in nearby]),
                    {"nearby": nearby})

    for n in range(1, len(keys) + 1):
        dropped = keys[:n]
        update = dict.fromkeys(dropped)
        if await has_results(update):
            removed = ", ".join(f"{RELAXABLE[k]}({_shown(getattr(sq.entities, k))})" for k in dropped)
            note = f"{removed} 조건을 빼면 찾을 수 있는 협력사가 있습니다. {BUTTON_HINT}"
            return Suggestion(note=note, options=[_search_sentence(sq, update)]), {"dropped": dropped}
    return None


def _with_entities(decomposition: DecompositionResult, sq: SubQuery, update: dict[str, Any]) -> DecompositionResult:
    changed = sq.model_copy(update={"entities": sq.entities.model_copy(update=update)})
    return decomposition.model_copy(update={
        "sub_queries": [changed if s.id == sq.id else s for s in decomposition.sub_queries]})


def _search_sentence(sq: SubQuery, update: dict[str, Any]) -> str:
    """버튼을 누르면 입력창에 들어갈 검색 문장. 분해기가 같은 조건으로 다시 나눌 수 있게 조건을 모두 적는다."""
    e = sq.entities.model_copy(update=update)
    parts = [e.region,
             f"월 생산능력 {e.min_monthly_capacity:,}개 이상" if e.min_monthly_capacity else None,
             "IATF 인증" if e.iatf_required else None,
             e.material, e.process]
    return " ".join(p for p in parts if p) + " 협력사 찾아줘"


def with_suggestions(answer: Answer, suggestions: list[Suggestion]) -> Answer:
    """답 끝에 안내를 붙이고 버튼을 단다. 이미 선택지가 있는 답(기업 선택 되묻기 등)은 건드리지 않는다."""
    if not suggestions or answer.options or answer.kind in ("clarify", "refusal", "failed"):
        return answer
    notes = [s.note for s in suggestions if s.note not in answer.text]
    options = list(dict.fromkeys(o for s in suggestions for o in s.options))[:MAX_OPTIONS]
    text = "\n\n".join([answer.text, *notes]) if notes else answer.text
    return answer.model_copy(update={"text": text, "options": options})


def _region_abbr(region: str) -> str:
    name = region.strip()
    for long, short in REGION_NAMES.items():
        if name.startswith(long):
            return short
    for suffix in ("특별자치시", "특별자치도", "특별시", "광역시", "도", "시"):
        if name.endswith(suffix) and len(name) > len(suffix) + 1:
            name = name[: -len(suffix)]
            break
    return name[:2] if name[:2] in ADJACENT else name


def _shown(value: Any) -> str:
    if value is True:
        return "필수"
    if isinstance(value, int) and not isinstance(value, bool):
        return f"{value:,}개 이상"
    return str(value)
