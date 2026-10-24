"""검토 에이전트: 생성한 답과 도구 결과를 보고, 근거를 더 찾으면 나아지는 경우에만 한 번 더 찾게 한다.

분해 → 라우트 → 생성 뒤에 붙는다. 먼저 규칙으로 정해진 실패를 보고, 규칙에 걸리지 않은 답은 모델 검토자가 본다.

보강 종류
1. retry_error (규칙): 도구 호출이 일시 오류로 실패한 하위 질문을 다시 실행한다.
   RFQ 없음, 협력사 없음처럼 공개 오류 코드가 붙은 실패는 입력 문제라 다시 불러도 같으므로 제외한다.
2. relax (규칙): 협력사 검색이 0건이면 먼저 인접 지역에서 찾고, 그래도 없으면 지역 → 최소 월 생산능력 → IATF 인증
   순으로 조건을 하나씩 뺀다. 공정·소재는 질문의 핵심이라 빼지 않는다. 바꾼 조건은 분해 가정에 적어 답 끝에 밝힌다.
3. follow_up (모델 검토자, critic.py): 질문에 답하지 못했거나 판단에 필요한 법적 조건이 근거에 없어 빠진 부분을
   보충 질문으로 받아, 기존 분해기로 하위 질문을 만들고 Routing으로 실행한다.

보강해서 결과가 바뀐 경우에만 generate로 돌아간다. 라운드 수는 NEGOPILOT_REVIEW_ROUNDS(기본 1, 0이면 끔)를 넘지 않는다.
"""

import logging
import os
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from negopilot_shared import DecompositionResult, RouteResult
from negopilot_shared.decomposition import MAX_SUB_QUERIES, SubQuery

from negopilot_agent.generation.answer import Answer
from negopilot_agent.review.critic import Critique
from negopilot_agent.routing.router import Router

log = logging.getLogger(__name__)

DEFAULT_ROUNDS = 1
# 빼도 되는 검색 조건 (뺄 순서). 값은 가정 문구에 쓸 이름
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
# 지역 약칭으로 바꿀 때 떼는 꼬리 (business-mcp resolve_region과 같은 규칙의 간단판)
REGION_NAMES = {"경상남": "경남", "경상북": "경북", "전라남": "전남", "전라북": "전북", "충청남": "충남", "충청북": "충북"}
FOLLOW_UP_INTENTS = {"rfq_lookup", "supplier_search", "supplier_detail", "supplier_ranking", "offer_check",
                     "law_check", "law_article"}


def configured_rounds() -> int:
    try:
        return max(0, int(os.getenv("NEGOPILOT_REVIEW_ROUNDS", DEFAULT_ROUNDS)))
    except ValueError:
        return DEFAULT_ROUNDS


ActionKind = Literal["retry_error", "relax", "follow_up"]


class ReviewAction(BaseModel):
    kind: ActionKind
    sub_query: str
    reason: str                                         # 왜 보강하는지 (로그·트레이스용)
    detail: dict[str, Any] = Field(default_factory=dict)  # relax: 바꾼 조건, follow_up: 빠진 부분·보충 질문·추가한 하위 질문
    resolved: bool | None = None                        # 실행 뒤 결과가 나아졌는지. 실행 전에는 None

    def key(self) -> tuple:
        return self.kind, self.sub_query, self.detail.get("question")


class ReviewRound(BaseModel):
    round: int
    actions: list[ReviewAction]
    regenerated: bool         # 보강 결과로 답을 다시 만들었는지


class ReviewLog(BaseModel):
    rounds: list[ReviewRound] = Field(default_factory=list)
    pending: list[ReviewAction] = Field(default_factory=list)   # 검토가 정하고 아직 실행하지 않은 보강

    def tried(self) -> set[tuple]:
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
                actions.append(ReviewAction(kind="relax", sub_query=o.id, reason="검색 결과 0건",
                                            detail={"relaxable": keys}))
    return actions


def follow_ups(critique: Critique, decomposition: DecompositionResult) -> list[ReviewAction]:
    """검토자의 보충 질문을 보강으로 바꾼다. 하위 질문 자리가 남아 있을 때만."""
    if critique.complete or len(decomposition.sub_queries) >= MAX_SUB_QUERIES:
        return []
    return [ReviewAction(kind="follow_up", sub_query="new", reason="검토자: 답에서 빠진 부분",
                         detail={"aspect": m.aspect, "question": m.question}) for m in critique.missing]


# ---------- 실행 ----------

class Decomposer(Protocol):
    async def decompose(self, query: str, history: list[dict] | None = None) -> DecompositionResult: ...


async def reinforce(router: Router, decomposer: Decomposer | None, decomposition: DecompositionResult,
                    routing: RouteResult, actions: list[ReviewAction]
                    ) -> tuple[DecompositionResult, RouteResult, list[ReviewAction], bool]:
    """보강을 실행한다. (분해, 라우팅, 결과를 적은 actions, 바뀐 것이 있는지)를 돌려준다."""
    done: list[ReviewAction] = []
    changed = False

    retry = [a for a in actions if a.kind == "retry_error"]
    if retry:
        routing = await router.rerun(decomposition, routing, {a.sub_query for a in retry})
        status = {o.id: o.status for o in routing.outcomes}
        for a in retry:
            done.append(a.model_copy(update={"resolved": status[a.sub_query] != "error"}))
        # 다시 해도 실패면 답이 그대로이므로 다시 쓰지 않는다 (모델 호출 비용을 아낀다)
        changed = any(a.resolved for a in done)

    for a in (a for a in actions if a.kind == "relax"):
        decomposition, routing, how = await _relax(router, decomposition, routing, a)
        done.append(a.model_copy(update={"resolved": how is not None, "detail": a.detail | (how or {})}))
        changed |= how is not None

    for a in (a for a in actions if a.kind == "follow_up"):
        if decomposer is None:
            done.append(a.model_copy(update={"resolved": False}))
            continue
        decomposition, routing, added = await _follow_up(router, decomposer, decomposition, routing, a)
        resolved = any(o.status == "ok" for o in routing.outcomes if o.id in added)
        done.append(a.model_copy(update={"resolved": resolved, "detail": a.detail | {"added": added}}))
        changed |= resolved

    return decomposition, routing, done, changed


async def _relax(router: Router, decomposition: DecompositionResult, routing: RouteResult,
                 action: ReviewAction) -> tuple[DecompositionResult, RouteResult, dict[str, Any] | None]:
    """0건 검색을 넓힌다. 지역이 있으면 인접 지역부터, 그래도 없으면 조건을 앞에서부터 하나씩 더 뺀다.

    결과가 나온 방법을 detail로 돌려준다 ({"nearby": [...]} 또는 {"dropped": [...]}). 끝까지 0건이면 None.
    """
    sq = next(s for s in decomposition.sub_queries if s.id == action.sub_query)
    keys: list[str] = action.detail["relaxable"]

    region = sq.entities.region
    neighbors = ADJACENT.get(_region_abbr(region), []) if isinstance(region, str) else []
    if neighbors:
        found = []
        for name in neighbors:
            candidate = _with_entities(decomposition, sq, {"region": name})
            result = await router.rerun(candidate, routing, {sq.id})
            outcome = next(o for o in result.outcomes if o.id == sq.id)
            if outcome.status == "ok":
                found.append((name, outcome))
        if found:
            names = [n for n, _ in found]
            # 지역마다 부른 검색을 한 하위 질문의 결과로 합친다. 분해의 지역 값은 원래대로 두고 가정에 밝힌다
            merged = found[0][1].model_copy(update={"calls": [c for _, o in found for c in o.calls]})
            routing = routing.model_copy(update={
                "outcomes": [merged if o.id == sq.id else o for o in routing.outcomes]})
            note = (f"'{sq.original_text}' 조건에 맞는 협력사가 {region}에 없어 "
                    f"인접 지역({', '.join(names)})에서 찾았습니다.")
            return (decomposition.model_copy(update={"assumptions": [*decomposition.assumptions, note]}), routing,
                    {"nearby": names})

    for n in range(1, len(keys) + 1):
        dropped = keys[:n]
        candidate = _with_entities(decomposition, sq, dict.fromkeys(dropped))
        result = await router.rerun(candidate, routing, {sq.id})
        if next(o for o in result.outcomes if o.id == sq.id).status == "ok":
            removed = ", ".join(f"{RELAXABLE[k]}({_shown(getattr(sq.entities, k))})" for k in dropped)
            note = f"'{sq.original_text}' 조건에 맞는 협력사가 없어 {removed} 조건을 빼고 다시 찾았습니다."
            return (candidate.model_copy(update={"assumptions": [*decomposition.assumptions, note]}), result,
                    {"dropped": dropped})
    return decomposition, routing, None


def _with_entities(decomposition: DecompositionResult, sq: SubQuery, update: dict[str, Any]) -> DecompositionResult:
    changed = sq.model_copy(update={"entities": sq.entities.model_copy(update=update)})
    return decomposition.model_copy(update={
        "sub_queries": [changed if s.id == sq.id else s for s in decomposition.sub_queries]})


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


async def _follow_up(router: Router, decomposer: Decomposer, decomposition: DecompositionResult,
                     routing: RouteResult, action: ReviewAction) -> tuple[DecompositionResult, RouteResult, list[str]]:
    """검토자의 보충 질문을 분해기로 하위 질문으로 바꿔 기존 분해 뒤에 붙이고, 새 하위 질문만 실행한다.

    원래 질문(original_query)과 기존 하위 질문·결과는 그대로 둔다. 이미 있는 하위 질문과 같은 것은 붙이지 않는다.
    """
    try:
        extra = await decomposer.decompose(action.detail["question"])
    except Exception as error:  # noqa: BLE001 - 보충은 덤이다. 실패해도 원래 답은 그대로 둔다
        log.warning("보충 질문 분해 실패: %s", type(error).__name__)
        return decomposition, routing, []
    if extra.status != "ok":
        return decomposition, routing, []

    existing = {_signature(s) for s in decomposition.sub_queries}
    room = MAX_SUB_QUERIES - len(decomposition.sub_queries)
    next_no = 1 + max(int(s.id[1:]) for s in decomposition.sub_queries)
    ids: dict[str, str] = {}
    added: list[SubQuery] = []
    for s in extra.sub_queries:
        if len(added) >= room:
            break
        # 앞 결과가 필요한 하위 질문은 그 앞 질문도 함께 붙었을 때만 쓴다
        if (s.intent not in FOLLOW_UP_INTENTS or _signature(s) in existing
                or any(d not in ids for d in s.depends_on)):
            continue
        ids[s.id] = f"q{next_no + len(added)}"
        added.append(s.model_copy(update={
            "id": ids[s.id],
            "depends_on": [ids[d] for d in s.depends_on],
            "inputs": [i.model_copy(update={"from_": ids[i.from_]}) for i in s.inputs]}))
        existing.add(_signature(s))
    if not added:
        return decomposition, routing, []

    try:
        extended = DecompositionResult.model_validate(
            decomposition.model_copy(update={"sub_queries": [*decomposition.sub_queries, *added]})
            .model_dump(by_alias=True))
    except ValueError as error:
        log.warning("보충 하위 질문을 붙이지 못함: %s", error)
        return decomposition, routing, []
    new_ids = [s.id for s in added]
    return extended, await router.rerun(extended, routing, set(new_ids)), new_ids


def _signature(sq: SubQuery) -> tuple:
    return sq.intent, sq.entities.model_dump_json(exclude_none=True)


def _shown(value: Any) -> str:
    if value is True:
        return "필수"
    if isinstance(value, int) and not isinstance(value, bool):
        return f"{value:,}개 이상"
    return str(value)
