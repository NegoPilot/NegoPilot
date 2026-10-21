"""Generation의 입출력 형식.

- Evidence: Routing 결과(도구 원본)를 근거 단위로 펼친 것. 모델은 E1, E2 같은 id로만 인용한다
- GenerationDraft: 모델이 구조화 출력으로 돌려주는 초안
- Answer: 사용자에게 보여 줄 최종 답변 (Backend가 SSE로 화면에 넘긴다)
"""

from typing import Any, Literal

from pydantic import BaseModel, Field


class Evidence(BaseModel):
    id: str                                  # "E1", "E2" ... 모델이 인용할 때 쓰는 짧은 id
    sub_query: str                           # 어느 하위 질문의 결과인지 (q1, q2 ...)
    source: Literal["law", "business"]
    kind: str                                # article, regulation_hit, offer_finding, ranked_supplier ...
    label: str                               # 사람이 읽는 출처 이름. 법령은 law-mcp citation 그대로
    text: str                                # 모델에게 보여 줄 근거 본문 (숫자는 원본 그대로)
    # False면 설명할 때 참고만 하고 인용하지 않는다 (law_check의 get_article 조문 전문. Routing 계약 1절 5)
    citable: bool = True
    source_url: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class GenerationDraft(BaseModel):
    """모델 출력. 본문 문장 끝에 [E1]처럼 근거 id를 붙인다."""

    answer: str = Field(description="사용자에게 보여 줄 답변. 근거를 쓴 문장 끝에 [E1], [E2]처럼 근거 id를 붙인다")
    used_evidence: list[str] = Field(default_factory=list, description="본문에서 인용한 근거 id 목록")


class Citation(BaseModel):
    n: int                                   # 화면에 보이는 번호 [1], [2]
    evidence_id: str
    label: str
    source_url: str | None = None


class VerificationReport(BaseModel):
    """근거 검사 결과. 평가 스크립트가 그대로 읽는다."""

    attempts: int = 1
    unknown_citations: list[str] = Field(default_factory=list)    # 근거 목록에 없는 id
    ungrounded_numbers: list[str] = Field(default_factory=list)   # 근거·질문에 없는 숫자
    ungrounded_articles: list[str] = Field(default_factory=list)  # 근거에 없는 조문 번호
    masked: bool = False                                          # 재시도 후에도 남아 "[확인 필요]"로 가렸는지

    @property
    def ok(self) -> bool:
        return not (self.unknown_citations or self.ungrounded_numbers or self.ungrounded_articles)


AnswerKind = Literal[
    "answer",        # 도구 결과(또는 일반 지식)로 답함
    "clarify",       # 되묻기 (분해 clarify, 또는 Routing missing_args)
    "refusal",       # out_of_scope
    "no_result",     # 실행했지만 쓸 수 있는 결과가 없음 (empty, error, unsupported만 있음)
    "failed",        # 분해 실패 또는 생성 실패
]


class Answer(BaseModel):
    kind: AnswerKind
    text: str                                         # 화면에 그대로 보여 줄 본문 ([1] 형식 인용 포함)
    citations: list[Citation] = Field(default_factory=list)
    options: list[str] = Field(default_factory=list)  # clarify일 때 선택지
    grounded: bool = True                             # False면 화면에 "근거 자료 없음" 배지
    notices: list[str] = Field(default_factory=list)  # 가정, 미지원 기능, 일부 실패 같은 안내
    evidence: list[Evidence] = Field(default_factory=list)          # 평가·디버깅용 (화면에는 안 씀)
    verification: VerificationReport | None = None
