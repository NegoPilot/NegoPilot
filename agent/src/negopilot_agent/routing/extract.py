"""결과 대응표: intent별로 도구 결과에서 목록을 꺼내고, inputs.select로 값을 고른다.

분해는 도구 응답 구조를 모르고 선택어(top1, top(n), all, by_name("이름"))만 쓴다.
도구 응답 구조가 바뀌면 이 표만 고친다.
"""

import re
from collections.abc import Callable
from typing import Any

from negopilot_agent.routing.results import ToolCall


def _list(result: Any) -> list[dict]:
    return result if isinstance(result, list) else []


def _ranked(result: Any) -> list[dict]:
    return result.get("ranked", []) if isinstance(result, dict) else []


def _one(result: Any) -> list[dict]:
    return [result] if isinstance(result, dict) else []


class ResultMap:
    def __init__(self, items: Callable[[Any], list[dict]], slots: dict[str, str], name: str | None):
        self.items = items      # 도구 결과 → 순서 있는 항목 목록
        self.slots = slots      # 엔티티 이름 → 항목의 필드
        self.name = name        # by_name("이름")으로 비교할 필드


# intent → 대응표. 순서는 각 도구의 반환 순서를 따른다 (list_rfqs 마감일 순, rank_suppliers 점수 순,
# search_suppliers 불량률 순). law_check는 search_regulation 다음 get_article 결과만 쓴다.
RESULT_MAPS: dict[str, ResultMap] = {
    "rfq_lookup": ResultMap(_list, {"rfq_id": "rfq_id", "oem_id": "oem_id"}, "part_name"),
    "supplier_search": ResultMap(_list, {"supplier": "supplier_id"}, "name"),
    "supplier_ranking": ResultMap(_ranked, {"supplier": "supplier_id"}, "name"),
    "supplier_detail": ResultMap(_one, {"supplier": "supplier_id"}, "name"),
    "law_article": ResultMap(_one, {"law": "law_key", "article_no": "article_no"}, "title"),
    "law_check": ResultMap(_one, {"law": "law_key", "article_no": "article_no"}, "title"),
}

_TOP_N = re.compile(r"^top\((\d+)\)$")
_BY_NAME = re.compile(r'^by_name\("(.+)"\)$')


def _squash(text: str) -> str:
    return re.sub(r"[\s()（）㈜]|주식회사", "", str(text)).lower()


def items_of(intent: str, calls: list[ToolCall]) -> list[dict]:
    """하위 질문 하나의 호출 결과를 대응표로 펼친다. 여러 번 불렀으면(fan-out) 이어 붙인다."""
    rmap = RESULT_MAPS.get(intent)
    if rmap is None:
        raise KeyError(f"intent '{intent}'는 결과 대응표에 없어 inputs로 쓸 수 없다")
    picked = calls
    if intent == "law_check":
        picked = [c for c in calls if c.tool == "get_article"]
    return [item for c in picked if c.error is None for item in rmap.items(c.result)]


def select_values(intent: str, calls: list[ToolCall], slot: str, select: str) -> list[Any]:
    """앞 하위 질문(intent, calls)의 결과에서 slot 값을 선택어대로 꺼낸다. 0건이면 빈 목록."""
    items = items_of(intent, calls)
    rmap = RESULT_MAPS[intent]
    if slot not in rmap.slots:
        raise KeyError(f"intent '{intent}' 결과에서 '{slot}' 값을 꺼낼 수 없다")
    field = rmap.slots[slot]

    if select == "top1":
        chosen = items[:1]
    elif select == "all":
        chosen = items
    elif m := _TOP_N.match(select):
        chosen = items[: int(m.group(1))]
    elif m := _BY_NAME.match(select):
        target = _squash(m.group(1))
        chosen = [it for it in items
                  if target in _squash(it.get(rmap.name or "", "")) or target == _squash(it.get(field, ""))]
    else:
        raise ValueError(f"알 수 없는 선택어: {select}")

    values: list[Any] = []
    for it in chosen:
        v = it.get(field)
        if v is not None and v not in values:
            values.append(v)
    return values
