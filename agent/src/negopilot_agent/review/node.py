"""LangGraph 노드: review, research.

    graph.add_edge("generate", "review")
    graph.add_conditional_edges("review", after_review, ["research", END])
    graph.add_conditional_edges("research", after_research, ["generate", END])

review는 답을 보고 보강할 거리를 정하고(pending), research는 그것을 실행해 Routing 결과를 고친다.
보강으로 바뀐 것이 있으면 generate가 답을 다시 쓰고, review가 한 번 더 본다. 라운드 수를 넘으면 끝낸다.
"""

import logging
from typing import NotRequired, TypedDict

from langgraph.graph import END

from negopilot_shared import DecompositionResult, RouteResult

from negopilot_agent.generation.answer import Answer
from negopilot_agent.review.reviewer import ReviewLog, ReviewRound, configured_rounds, plan, reinforce
from negopilot_agent.routing.mcp_client import McpToolCaller
from negopilot_agent.routing.router import Router, ToolCaller

log = logging.getLogger(__name__)


class ReviewState(TypedDict):
    query: str
    decomposition: DecompositionResult
    routing: NotRequired[RouteResult]
    answer: NotRequired[Answer]
    review: NotRequired[ReviewLog]


def make_review_node(max_rounds: int | None = None):
    """max_rounds를 생략하면 NEGOPILOT_REVIEW_ROUNDS(기본 1)를 따른다. 0이면 검토만 하고 보강하지 않는다."""
    rounds = configured_rounds() if max_rounds is None else max_rounds

    async def review(state: ReviewState) -> dict:
        history = state.get("review") or ReviewLog()
        if len(history.rounds) >= rounds:
            return {"review": history.model_copy(update={"pending": []})}
        tried = history.tried()
        # 이미 해 본 보강은 다시 하지 않는다 (같은 실패를 되풀이하지 않게)
        pending = [a for a in plan(state["decomposition"], state.get("routing"), state.get("answer"))
                   if a.key() not in tried]
        if pending:
            log.info("검토: 보강 %s", [(a.kind, a.sub_query, a.reason) for a in pending])
        return {"review": history.model_copy(update={"pending": pending})}

    return review


def after_review(state: ReviewState) -> str:
    return "research" if state["review"].pending else END


def make_research_node(caller: ToolCaller | None = None):
    """테스트에서는 가짜 caller를 넣고, 생략하면 실제 MCP 서버(HTTP)를 부른다."""
    router = Router(caller or McpToolCaller())

    async def research(state: ReviewState) -> dict:
        history = state["review"]
        decomposition, routing, actions, changed = await reinforce(
            router, state["decomposition"], state["routing"], history.pending)
        done = ReviewRound(round=len(history.rounds) + 1, actions=actions, regenerated=changed)
        log.info("검토 %d회차: %s", done.round, [(a.kind, a.sub_query, a.resolved) for a in actions])
        out: dict = {"review": ReviewLog(rounds=[*history.rounds, done])}
        if changed:
            out |= {"decomposition": decomposition, "routing": routing}
        return out

    return research


def after_research(state: ReviewState) -> str:
    return "generate" if state["review"].rounds[-1].regenerated else END
