"""쿼리 분해 결과 계약 (docs/contracts/query-decomposition.md).

분해 노드는 이 모델을 출력하고, Routing·Generation은 이 모델로 입력을 검증한다.
필드와 규칙을 바꾸면 계약 문서와 fixtures도 함께 고친다.
"""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from negopilot_shared.intents import load_intents

SCHEMA_VERSION = "0.1"
MAX_SUB_QUERIES = 5
CLARIFY_CONFIDENCE = 0.6  # 이보다 낮으면 되묻기 후보

PROCESSES = ("다이캐스팅", "프레스", "사출", "단조", "CNC가공")
LAW_KEYS = ("subcontract_act", "subcontract_decree", "winwin_act", "winwin_decree",
            "sme_act", "sme_decree")
SELECT_RE = re.compile(r'^(top1|all|top\(([1-9]\d*)\)|by_name\("[^"]+"\))$')


class Entities(BaseModel):
    """엔티티 사전. 이름은 MCP 도구 인자명과 같다. 사전에 없는 키는 받지 않는다."""

    model_config = ConfigDict(extra="forbid")

    # business-mcp
    rfq_id: str | None = Field(None, pattern=r"^RFQ-\d{4}-\d{3}$")
    supplier: str | None = None              # supplier_id(SUP-0001) 또는 회사명
    oem_id: str | None = None
    process: Literal[PROCESSES] | None = None  # type: ignore[valid-type]
    material: str | None = None
    region: str | None = None
    iatf_required: bool | None = None
    min_monthly_capacity: int | None = Field(None, gt=0)
    top_n: int | None = Field(None, ge=1, le=100)
    unit_price: int | None = Field(None, gt=0)          # 원
    payment_days: int | None = Field(None, gt=0)
    price_linkage: bool | None = None
    raw_material_ratio: float | None = Field(None, ge=0, le=1)
    contract_days: int | None = Field(None, gt=0)
    linkage_waived_by_agreement: bool | None = None
    buyer_is_small_business: bool | None = None
    # law-mcp
    law: Literal[LAW_KEYS] | None = None  # type: ignore[valid-type]
    article_no: str | None = Field(None, pattern=r"^제\d+조(의\d+)?$")
    as_of: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    # 판단 근거 (도구 인자는 아님)
    price_change_pct: float | None = None   # 인하는 음수

    def present(self) -> set[str]:
        return {k for k, v in self.model_dump().items() if v is not None}


ENTITY_NAMES = set(Entities.model_fields)


class InputRef(BaseModel):
    """앞 하위 질문 결과에서 값을 받아 올 자리. 실제로 꺼내는 일은 Routing의 결과 대응표가 한다."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    slot: str
    from_: str = Field(alias="from")
    select: str

    @field_validator("slot")
    @classmethod
    def slot_is_entity(cls, v: str) -> str:
        if v not in ENTITY_NAMES:
            raise ValueError(f"slot '{v}'는 엔티티 사전에 없다")
        return v

    @field_validator("select")
    @classmethod
    def select_is_known(cls, v: str) -> str:
        if not SELECT_RE.match(v):
            raise ValueError(f"select '{v}'는 top1, top(n), all, by_name(\"이름\") 중 하나여야 한다")
        return v


class SubQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^q\d+$")
    question: str = Field(min_length=1)      # 법률·코드 용어로 정규화한 질문
    original_text: str = Field(min_length=1) # 원문에서 해당 부분
    intent: str
    grounding: Literal["required", "none"]
    confidence: float = Field(ge=0, le=1)
    entities: Entities = Field(default_factory=Entities)
    inputs: list[InputRef] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_intent(self) -> "SubQuery":
        intents = load_intents()
        if self.intent not in intents:
            raise ValueError(f"intent '{self.intent}'가 등록표에 없다. 가능: {', '.join(intents)}")
        spec = intents[self.intent]
        if self.grounding != spec.grounding:
            raise ValueError(f"intent '{self.intent}'의 grounding은 '{spec.grounding}'이어야 한다")
        allowed = set(spec.entities) | {"price_change_pct"}
        used = self.entities.present() | {i.slot for i in self.inputs}
        if extra := used - allowed:
            raise ValueError(f"intent '{self.intent}'에 쓸 수 없는 엔티티: {sorted(extra)}")
        return self


class Clarification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1)
    question: str = Field(min_length=1)
    options: list[str] = Field(min_length=2, max_length=4)


class DecompositionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["0.1"] = SCHEMA_VERSION
    status: Literal["ok", "clarify", "failed"]
    original_query: str = Field(min_length=1)
    sub_queries: list[SubQuery] = Field(default_factory=list, max_length=MAX_SUB_QUERIES)
    clarification: Clarification | None = None
    assumptions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_status_and_graph(self) -> "DecompositionResult":
        if self.status == "ok":
            if not self.sub_queries:
                raise ValueError("status 'ok'에는 하위 질문이 1개 이상 있어야 한다")
            if self.clarification is not None:
                raise ValueError("status 'ok'에는 clarification을 넣지 않는다")
        elif self.status == "clarify":
            if self.clarification is None or self.sub_queries:
                raise ValueError("status 'clarify'는 clarification만 있고 하위 질문은 비어 있어야 한다")
        elif self.sub_queries or self.clarification is not None:
            raise ValueError("status 'failed'에는 하위 질문과 clarification을 넣지 않는다")

        seen: set[str] = set()
        for sq in self.sub_queries:
            if sq.id in seen:
                raise ValueError(f"하위 질문 id '{sq.id}'가 중복된다")
            # 앞에 나온 하위 질문에만 의존할 수 있다 → 순환이 생기지 않는다
            if missing := [d for d in sq.depends_on if d not in seen]:
                raise ValueError(f"{sq.id}의 depends_on {missing}는 앞에 나온 하위 질문이 아니다")
            if bad := [i.from_ for i in sq.inputs if i.from_ not in sq.depends_on]:
                raise ValueError(f"{sq.id}의 inputs.from {bad}는 depends_on에도 있어야 한다")
            seen.add(sq.id)
        return self

    def parallel_groups(self) -> list[list[str]]:
        """의존 관계에 따라 같은 단계에서 병렬로 실행할 수 있는 하위 질문 묶음."""
        level: dict[str, int] = {}
        for sq in self.sub_queries:
            level[sq.id] = 1 + max((level[d] for d in sq.depends_on), default=-1)
        groups: dict[int, list[str]] = {}
        for qid, lv in level.items():
            groups.setdefault(lv, []).append(qid)
        return [groups[k] for k in sorted(groups)]
