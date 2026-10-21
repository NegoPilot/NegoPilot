"""Routing 출력 형식 (초안). Generation이 이 모델을 입력으로 받는다.

Generation 담당과 합의하면 shared/로 옮겨 계약으로 만든다.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

# ok          도구를 불러 결과를 받음
# no_tool     도구가 없는 intent (general_knowledge, out_of_scope) → Generation이 바로 답함
# unsupported status: planned intent (draft_email) → "아직 지원하지 않는 기능"
# missing_args 도구 필수 인자가 분해 결과에 없음 → Generation이 되묻기
# empty       inputs로 꺼낼 값이 앞 결과에 없음 (0건) → 실행을 멈추고 "조건에 맞는 결과 없음"
# skipped     앞 하위 질문이 ok가 아니라서 실행하지 않음
# error       도구 호출 실패 (ToolError, 연결 실패)
OutcomeStatus = Literal["ok", "no_tool", "unsupported", "missing_args", "empty", "skipped", "error"]


class ToolCall(BaseModel):
    server: str
    tool: str
    args: dict[str, Any]
    result: Any = None
    error: str | None = None


class SubQueryOutcome(BaseModel):
    id: str
    intent: str
    status: OutcomeStatus
    calls: list[ToolCall] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)   # missing_args일 때 빠진 엔티티 이름
    message: str | None = None                        # 사람이 읽는 설명 (왜 이 status인지)


class RouteResult(BaseModel):
    # routed: 하위 질문을 실행함 · not_routed: 분해 status가 clarify/failed라 실행할 것이 없음
    status: Literal["routed", "not_routed"]
    outcomes: list[SubQueryOutcome] = Field(default_factory=list)   # sub_queries 순서
    halted: bool = False            # 앞 결과 0건으로 실행을 멈췄는지
    halt_reason: str | None = None

    def outcome(self, qid: str) -> SubQueryOutcome:
        return next(o for o in self.outcomes if o.id == qid)
