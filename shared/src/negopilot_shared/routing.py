"""Routing 결과 계약 (docs/contracts/routing.md).

Routing 노드는 이 모델을 출력하고, Generation은 DecompositionResult와 함께 이 모델을 입력으로 받는다.
필드와 규칙을 바꾸면 계약 문서와 fixtures/routing도 함께 고친다.
"""

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ROUTING_SCHEMA_VERSION = "0.1"

# 사용자에게 고칠 방법을 안내해도 되는 도구 오류 코드 (docs/contracts/check-offer.md 3절).
# MCP 서버는 ToolError 메시지 앞에 "[CODE] "를 붙인다. 이 목록에 없는 코드와 원문은 계속 가린다.
PUBLIC_ERROR_CODES = frozenset({"RFQ_NOT_FOUND", "SUPPLIER_NOT_FOUND", "SUPPLIER_AMBIGUOUS", "VALUE_OUT_OF_RANGE"})
_ERROR_CODE = re.compile(r"^\[([A-Z_]+)\]")


def public_error_code(message: str) -> str | None:
    """도구 오류 메시지 앞의 [CODE]가 공개 목록에 있으면 그 코드를, 아니면 None을 돌려준다."""
    match = _ERROR_CODE.match(message)
    return match.group(1) if match and match.group(1) in PUBLIC_ERROR_CODES else None

# 하위 질문별 결과. Generation이 할 일은 계약 문서 3절
OutcomeStatus = Literal[
    "ok",                 # 도구를 불러 결과를 받음
    "general_knowledge",  # 도구 없이 답할 개념 설명
    "out_of_scope",       # 업무와 무관한 질문
    "unsupported",        # status: planned intent (draft_email)
    "missing_args",       # 도구 필수 인자가 없음 → missing
    "empty",              # inputs로 꺼낼 값이 앞 결과에 없음 (0건)
    "skipped",            # 앞 하위 질문이 ok가 아니라서 실행하지 않음
    "error",              # 도구 호출 실패
]


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    server: str
    tool: str
    args: dict[str, Any]
    result: Any = None          # MCP 도구가 준 값 그대로
    error: str | None = None
    # 공개 오류 코드. Spring 응답 검증기가 아직 이 필드를 받지 않아 HTTP 응답과 저장 JSON에서는 뺀다.
    # Agent 안에서 Generation이 안내 문구를 고르는 데만 쓴다 (#129)
    error_code: str | None = Field(None, exclude=True)


class SubQueryOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str                     # DecompositionResult.sub_queries[].id 와 같다
    intent: str
    status: OutcomeStatus
    calls: list[ToolCall] = Field(default_factory=list)
    # 결과에 붙은 law_refs(rank_suppliers flags, check_offer)를 law-mcp get_article로 미리 조회한 것
    referenced_articles: list[ToolCall] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)   # missing_args일 때 빠진 엔티티 이름
    message: str | None = None  # 개발·로그용 이유. 사용자에게 그대로 보여 주지 않는다

    @model_validator(mode="after")
    def check_status(self) -> "SubQueryOutcome":
        if (self.status == "missing_args") != bool(self.missing):
            raise ValueError("missing은 status 'missing_args'일 때만, 그리고 반드시 채운다")
        if self.status in ("ok", "error") and not self.calls:
            raise ValueError(f"status '{self.status}'에는 도구 호출이 1개 이상 있어야 한다")
        return self


class RouteResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["0.1"] = ROUTING_SCHEMA_VERSION
    # routed: 하위 질문을 실행함 · not_routed: 분해 status가 clarify/failed라 실행할 것이 없음
    status: Literal["routed", "not_routed"]
    outcomes: list[SubQueryOutcome] = Field(default_factory=list)   # sub_queries 순서
    halted: bool = False            # 앞 결과 0건으로 실행을 멈췄는지
    halt_reason: str | None = None

    @model_validator(mode="after")
    def check_consistency(self) -> "RouteResult":
        if self.status == "not_routed" and self.outcomes:
            raise ValueError("status 'not_routed'에는 outcomes를 넣지 않는다")
        if self.status == "routed" and not self.outcomes:
            raise ValueError("status 'routed'에는 outcomes가 1개 이상 있어야 한다")
        ids = [o.id for o in self.outcomes]
        if len(ids) != len(set(ids)):
            raise ValueError("outcomes의 id가 중복된다")
        if self.halted != (self.halt_reason is not None):
            raise ValueError("halted와 halt_reason은 함께 있거나 함께 없어야 한다")
        if self.halted and not any(o.status == "empty" for o in self.outcomes):
            raise ValueError("halted는 status 'empty'인 하위 질문이 있을 때만 true")
        return self

    def outcome(self, qid: str) -> SubQueryOutcome:
        return next(o for o in self.outcomes if o.id == qid)
