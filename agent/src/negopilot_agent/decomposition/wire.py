"""OpenAI strict 구조화 출력용 짧은 분해 형식. 모델 출력만 이 형식이고, 받은 뒤 바로 계약 형식으로 바꾼다.

strict 모드는 모든 필드를 required로 만들어서, 계약 형식(Entities 객체 20칸)을 그대로 쓰면 하위 질문마다
쓰지 않는 엔티티까지 "rfq_id": null처럼 전부 출력한다. 출력 토큰이 곧 응답 시간이라 이 부분이 분해 시간의 큰 몫이다.
그래서 모델에는
- entities를 [{"key": 이름, "value": 값}] 목록으로 받아 실제로 쓴 값만 출력하게 하고
- original_query(사용자 원문, 분해기가 채움)와 schema_version(고정값)은 출력하지 않게 한다.
나머지 필드는 계약과 같다. 계약(DecompositionResult)과 검증·재시도는 그대로다.
프롬프트의 규칙 10과 예시도 같은 짧은 형식으로 보여 준다 (prompt.build_system_prompt(compact=True)).
"""

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from negopilot_shared.decomposition import Clarification, Entities, InputRef

EntityKey = Literal[tuple(Entities.model_fields)]  # type: ignore[valid-type]

COMPACT_RULE = ("entities는 [{\"key\": 엔티티 이름, \"value\": 값}] 목록으로 쓴다. 목록이라는 형식만 다르고, 어떤 엔티티를 넣을지는 위 규칙 6 그대로다.\n"
                "    값이 있는 엔티티는 모두 넣고(사용자가 법령 이름을 말했으면 law도 넣는다), 값이 없는 엔티티만 뺀다. 넣을 값이 없으면 빈 목록이다.\n"
                "    앞 하위 질문 결과에서 받을 값(예: 순위에 쓸 rfq_id)은 entities가 비어도 inputs로 받아 그 하위 질문을 만든다.\n"
                "    original_query와 schema_version은 출력하지 않는다 (코드가 사용자 원문과 고정값으로 채운다).")


class EntityValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: EntityKey
    value: str | int | float | bool


class WireSubQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(pattern=r"^q\d+$")
    question: str
    original_text: str
    intent: str
    grounding: Literal["required", "none"]
    confidence: float
    entities: list[EntityValue]
    inputs: list[InputRef]
    depends_on: list[str]


class DecompositionWire(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "clarify", "failed"]
    sub_queries: list[WireSubQuery]
    clarification: Clarification | None
    assumptions: list[str]


def contract_to_wire(result: dict) -> dict:
    """계약 형식 결과(프롬프트 예시) → 짧은 형식. 예시와 실제 출력 형식이 같아야 모델이 헷갈리지 않는다."""
    out = {k: v for k, v in result.items() if k not in ("original_query", "schema_version")}
    out["sub_queries"] = [sq | {"entities": [{"key": k, "value": v} for k, v in sq.get("entities", {}).items()
                                             if v is not None]}
                          for sq in result.get("sub_queries", [])]
    return out


def to_contract_json(text: str) -> str:
    """짧은 형식 JSON → 계약 형식 JSON (original_query 제외). 모양이 다르면 그대로 돌려줘 검증 오류로 처리되게 한다."""
    try:
        data = json.loads(text)
        sub_queries = data["sub_queries"]
    except (ValueError, TypeError, KeyError):
        return text
    if not isinstance(sub_queries, list):
        return text
    for sq in sub_queries:
        if isinstance(sq, dict) and isinstance(sq.get("entities"), list):
            sq["entities"] = {e["key"]: e["value"] for e in sq["entities"]
                              if isinstance(e, dict) and "key" in e and "value" in e}
    return json.dumps(data, ensure_ascii=False)
