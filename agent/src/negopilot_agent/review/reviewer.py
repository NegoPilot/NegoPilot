"""검토 에이전트: 생성한 답과 도구 결과를 보고, 근거를 더 찾으면 나아지는 경우에만 한 번 더 찾게 한다.

분해 → 라우트 → 생성 뒤에 붙는다. 판단은 모델 없이 규칙으로 한다 (비용이 없고 같은 상황에 늘 같은 판단).

보강 종류
1. retry_error: 도구 호출이 일시 오류로 실패한 하위 질문을 다시 실행한다.
   RFQ 없음, 협력사 없음처럼 공개 오류 코드가 붙은 실패는 입력 문제라 다시 불러도 같으므로 제외한다.
2. relax: 협력사 검색이 0건이면 지역 → 최소 월 생산능력 → IATF 인증 순으로 조건을 하나씩 빼며 다시 검색한다.
   공정·소재는 질문의 핵심이라 빼지 않는다. 뺀 조건은 분해 가정에 적어 답 끝에 "가정:"으로 밝힌다.
3. fetch_article: 답이 근거에 없는 조문 번호를 써서 [확인 필요]로 가려졌으면, 그 조문을 law-mcp에서 가져와
   근거에 넣고 다시 생성한다. 법령은 가린 자리 바로 앞의 법령 이름 → 근거의 법령 순으로 정하고,
   여러 법령 중 어느 것인지 모르면 가져오지 않는다 (다른 법령의 같은 번호 조문으로 검사를 통과하지 않게).

보강해서 결과가 바뀐 경우에만 generate로 돌아간다. 라운드 수는 NEGOPILOT_REVIEW_ROUNDS(기본 1, 0이면 끔)를 넘지 않는다.
"""

import logging
import os
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from negopilot_shared import DecompositionResult, RouteResult, SubQueryOutcome

from negopilot_agent.generation.answer import Answer
from negopilot_agent.generation.verify import ARTICLE_RE, MASK, normalize_article
from negopilot_agent.routing.router import Router

log = logging.getLogger(__name__)

DEFAULT_ROUNDS = 1
MAX_ARTICLES = 3          # 한 라운드에 가져올 최대 조문 수
# 빼도 되는 검색 조건 (뺄 순서). 값은 가정 문구에 쓸 이름
RELAXABLE = {"region": "지역", "min_monthly_capacity": "최소 월 생산능력", "iatf_required": "IATF 인증"}
DEFAULT_LAW = "subcontract_act"   # 법령 단서가 하나도 없을 때. NegoPilot 답의 대부분이 하도급법이다
# law-mcp ALIASES와 같은 이름 (공백·가운뎃점을 뺀 형태). 긴 이름부터 맞춰야 '하도급법 시행령'이 '하도급법'으로 읽히지 않는다
LAW_NAMES = {
    "하도급거래공정화에관한법률시행령": "subcontract_decree",
    "하도급거래공정화에관한법률": "subcontract_act",
    "하도급법시행령": "subcontract_decree",
    "하도급법": "subcontract_act",
    "대중소기업상생협력촉진에관한법률시행령": "winwin_decree",
    "대중소기업상생협력촉진에관한법률": "winwin_act",
    "상생협력법시행령": "winwin_decree",
    "상생협력법": "winwin_act",
    "중소기업기본법시행령": "sme_decree",
    "중소기업기본법": "sme_act",
}
_LAW_BEFORE_MASK = re.compile(
    r"((?:하도급거래\s*공정화에\s*관한\s*법률|하도급법|대[·ㆍ]?\s*중소기업\s*상생협력\s*촉진에\s*관한\s*법률|상생협력법"
    r"|중소기업기본법)(?:\s*시행령)?)\s*" + re.escape(MASK))


def configured_rounds() -> int:
    try:
        return max(0, int(os.getenv("NEGOPILOT_REVIEW_ROUNDS", DEFAULT_ROUNDS)))
    except ValueError:
        return DEFAULT_ROUNDS


ActionKind = Literal["retry_error", "relax", "fetch_article"]


class ReviewAction(BaseModel):
    kind: ActionKind
    sub_query: str
    reason: str                                         # 왜 보강하는지 (로그·트레이스용)
    detail: dict[str, Any] = Field(default_factory=dict)  # relax: dropped, fetch_article: law·article_no
    resolved: bool | None = None                        # 실행 뒤 결과가 나아졌는지. 실행 전에는 None

    def key(self) -> tuple:
        return self.kind, self.sub_query, self.detail.get("law"), self.detail.get("article_no")


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
    actions += _article_actions(routing, answer)
    return actions


def _article_actions(routing: RouteResult, answer: Answer | None) -> list[ReviewAction]:
    report = answer.verification if answer else None
    if answer is None or answer.kind != "answer" or report is None or not report.masked:
        return []
    known = {normalize_article(str(e.meta.get("article_no") or "")) for e in answer.evidence} - {""}
    articles: list[str] = []
    for item in report.ungrounded_articles:
        m = ARTICLE_RE.search(item)
        article = normalize_article(m.group(0)) if m else ""
        # 조문은 근거에 있고 항만 틀린 경우는 조문을 다시 가져와도 나아지지 않는다
        if article and article not in known and article not in articles:
            articles.append(article)
    if not articles:
        return []
    law = guess_law(answer.text, routing)
    if law is None:
        log.info("검토: 근거에 없는 조문 %s의 법령을 정할 수 없어 보강하지 않음", articles)
        return []
    target = _attach_target(routing)
    if target is None:
        return []
    return [ReviewAction(kind="fetch_article", sub_query=target.id, reason="근거에 없는 조문 번호",
                         detail={"law": law, "article_no": a}) for a in articles[:MAX_ARTICLES]]


def guess_law(text: str, routing: RouteResult) -> str | None:
    """가린 조문의 법령. 가린 자리 앞 이름 → 근거에 나온 법령 → 기본(하도급법). 후보가 둘 이상이면 None."""
    named = {LAW_NAMES.get(re.sub(r"[\s·ㆍ]", "", m.group(1))) for m in _LAW_BEFORE_MASK.finditer(text)} - {None}
    if named:
        return named.pop() if len(named) == 1 else None
    seen: set[str] = set()
    for o in routing.outcomes:
        for c in [*o.calls, *o.referenced_articles]:
            if c.server != "law-mcp" or c.error:
                continue
            if isinstance(c.args.get("law"), str):
                seen.add(c.args["law"])
            if isinstance(c.result, list):
                seen |= {h["law_key"] for h in c.result if isinstance(h, dict) and isinstance(h.get("law_key"), str)}
    seen = {LAW_NAMES.get(re.sub(r"[\s·ㆍ]", "", s), s) for s in seen}
    if len(seen) > 1:
        return None
    return seen.pop() if seen else DEFAULT_LAW


def _attach_target(routing: RouteResult) -> SubQueryOutcome | None:
    """가져온 조문을 붙일 하위 질문. 법령을 다룬 질문이 있으면 그것, 없으면 결과가 있는 첫 질문."""
    ok = [o for o in routing.outcomes if o.status == "ok"]
    law = [o for o in ok if any(c.server == "law-mcp" for c in o.calls) or o.referenced_articles]
    return (law or ok or [None])[0]


# ---------- 실행 ----------

async def reinforce(router: Router, decomposition: DecompositionResult, routing: RouteResult,
                    actions: list[ReviewAction]) -> tuple[DecompositionResult, RouteResult, list[ReviewAction], bool]:
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
        decomposition, routing, dropped = await _relax(router, decomposition, routing, a)
        done.append(a.model_copy(update={"resolved": dropped is not None, "detail": a.detail | {"dropped": dropped or []}}))
        changed |= dropped is not None

    fetch = [a for a in actions if a.kind == "fetch_article"]
    if fetch:
        outcomes = {o.id: o.model_copy(deep=True) for o in routing.outcomes}
        for a in fetch:
            call = await router.fetch("law-mcp", "get_article",
                                      {"law": a.detail["law"], "article_no": a.detail["article_no"]})
            ok = call.error is None and isinstance(call.result, dict)
            if ok:
                outcomes[a.sub_query].referenced_articles.append(call)
            done.append(a.model_copy(update={"resolved": ok}))
            changed |= ok
        routing = routing.model_copy(update={"outcomes": [outcomes[o.id] for o in routing.outcomes]})

    return decomposition, routing, done, changed


async def _relax(router: Router, decomposition: DecompositionResult, routing: RouteResult,
                 action: ReviewAction) -> tuple[DecompositionResult, RouteResult, list[str] | None]:
    """조건을 앞에서부터 하나씩 더 빼며 다시 검색한다. 결과가 나온 조건 조합을 쓰고, 끝까지 0건이면 그대로 둔다."""
    sq = next(s for s in decomposition.sub_queries if s.id == action.sub_query)
    keys: list[str] = action.detail["relaxable"]
    for n in range(1, len(keys) + 1):
        dropped = keys[:n]
        relaxed_sq = sq.model_copy(update={"entities": sq.entities.model_copy(update=dict.fromkeys(dropped))})
        candidate = decomposition.model_copy(update={
            "sub_queries": [relaxed_sq if s.id == sq.id else s for s in decomposition.sub_queries]})
        result = await router.rerun(candidate, routing, {sq.id})
        if next(o for o in result.outcomes if o.id == sq.id).status == "ok":
            removed = ", ".join(f"{RELAXABLE[k]}({_shown(getattr(sq.entities, k))})" for k in dropped)
            note = f"'{sq.original_text}' 조건에 맞는 협력사가 없어 {removed} 조건을 빼고 다시 찾았습니다."
            return candidate.model_copy(update={"assumptions": [*decomposition.assumptions, note]}), result, dropped
    return decomposition, routing, None


def _shown(value: Any) -> str:
    if value is True:
        return "필수"
    if isinstance(value, int) and not isinstance(value, bool):
        return f"{value:,}개 이상"
    return str(value)
