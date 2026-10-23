"""Fixture model and tool I/O for the real graph and AnswerGenerator.

These backends return model drafts. Evidence extraction, validation and final
Answer construction remain the responsibility of the unchanged Agent core.
"""

import json
import re
from copy import deepcopy
from typing import Any

from negopilot_shared import DecompositionResult

from .stubs import FIXTURE_DIRECTORY, FixtureDecomposer, UnsupportedStubQuery
from .supplier_chat_service import (
    FINANCIAL_QUERY, FIXTURE_PATH, OVERVIEW_ALIAS, OVERVIEW_QUERY, QUALITY_QUERY, SupplierChatService,
)
from .supplier_chat_models import SupplierDetail

SUPPLIER_QUERIES = {OVERVIEW_QUERY, FINANCIAL_QUERY, QUALITY_QUERY, OVERVIEW_ALIAS}
SEARCH_QUERIES = {
    "협력사 목록 보여줘": {},
    "부산 협력사 찾아줘": {"region": "부산"},
    "경남 협력사 찾아줘": {"region": "경남"},
    "제주 협력사 찾아줘": {"region": "제주"},
    "프레스 협력사 찾아줘": {"process": "프레스"},
}
NAMED_QUERIES = {
    "해솔정밀에 대해 알려줘": "해솔정밀",
    "해솔에 대해 알려줘": "해솔",
    "없는기업에 대해 알려줘": "없는기업",
}
PAYMENT_QUERY = "하도급대금 지급 기한"


def stub_suppliers() -> list[dict]:
    """Extend the v2 fixture without changing the legacy v0.2 catalog."""
    original = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["suppliers"]
    extra = json.loads(FIXTURE_PATH.with_name("direct-chat.json").read_text(encoding="utf-8"))["suppliers"]
    records = [SupplierDetail.model_validate(item).model_dump(mode="json") for item in [*original, *extra]]
    if len({item["supplier_id"] for item in records}) != len(records):
        raise ValueError("Duplicate direct-chat fixture supplier ID")
    return records


def _result(query: str, intent: str, entities: dict, question: str | None = None) -> dict:
    return {
        "status": "ok", "original_query": query,
        "sub_queries": [{
            "id": "q1", "question": question or query, "original_text": query,
            "intent": intent, "grounding": "required", "confidence": 1.0, "entities": entities,
        }],
    }


class FixtureDecompositionBackend:
    """Bind validated request data so history cannot select another fixture.

    The real QueryDecomposer still constructs and sends its prompt to this fake
    backend. Fixture selection uses the request rather than parsing that text.
    """

    def __init__(self, query: str, context: dict[str, list[str]], history: list[dict] | None = None) -> None:
        self.fixtures = FixtureDecomposer().fixtures
        for name in ("06_general_knowledge", "07_number_needs_grounding"):
            fixture = json.loads((FIXTURE_DIRECTORY / f"{name}.json").read_text(encoding="utf-8"))
            self.fixtures[fixture["query"]] = DecompositionResult.model_validate(fixture["expected"])
        self.query = query
        self.context = deepcopy(context)
        self.history = deepcopy(history or [])

    def _followup(self) -> dict | None:
        # Only explicit documented replies can resume a documented pending turn.
        # Never infer a fixture from prompt markers or arbitrary history text.
        if len(self.history) < 2 or self.history[-1].get("role") != "assistant":
            return None
        previous = self.history[-2]
        if previous.get("role") != "user":
            return None
        original = previous.get("content", "").strip()
        if original not in SUPPLIER_QUERIES | {"해솔에 대해 알려줘"}:
            return None
        reply = self.query.strip()
        number = {"첫 번째": 1, "두 번째": 2}.get(reply)
        if match := re.fullmatch(r"([1-9]|10)번?", reply):
            number = int(match[1])
        if number is not None:
            content = self.history[-1].get("content", "")
            try:
                # The Spring conversation adapter supplies this whole JSON block.
                options = json.loads(content.rsplit("\n\n선택지 데이터:\n", 1)[1])
                if (not isinstance(options, list) or not 1 <= number <= len(options)
                        or any(not isinstance(option, dict) or type(option.get("number")) is not int
                               or option["number"] != i or not isinstance(option.get("text"), str)
                               for i, option in enumerate(options, 1))):
                    return None
                reply = options[number - 1]["text"]
            except (ValueError, IndexError, TypeError):
                return None
        for item in stub_suppliers():
            replies = {item["supplier_id"], item["name"], f'{item["name"]} ({item["supplier_id"]})'}
            if reply in replies:
                return _result(self.query, "supplier_detail", {"supplier": item["supplier_id"]},
                               f'{item["name"]} 기업에 대한 요청: {original}')
        return None

    async def generate(self, system: str, user: str) -> str:
        query = self.query
        selected = self.context["selected_supplier_ids"]
        if result := self._followup():
            return json.dumps(result, ensure_ascii=False)
        if query.strip() in SEARCH_QUERIES:
            return json.dumps(_result(query, "supplier_search", SEARCH_QUERIES[query.strip()]), ensure_ascii=False)
        if query.strip() in NAMED_QUERIES:
            return json.dumps(_result(query, "supplier_detail", {"supplier": NAMED_QUERIES[query.strip()]}),
                              ensure_ascii=False)
        if query.strip() == "결제 90일로 해도 돼?":
            return json.dumps(_result(query, "law_check", {"law": "subcontract_act"}, PAYMENT_QUERY),
                              ensure_ascii=False)
        if query.strip() in SUPPLIER_QUERIES:
            if selected:
                result = {
                    "status": "ok", "original_query": query,
                    "sub_queries": [{
                        "id": "q1", "question": query, "original_text": query,
                        "intent": "supplier_detail", "grounding": "required", "confidence": 1.0,
                        "entities": {"supplier": selected[0]},
                    }],
                }
            else:
                result = {
                    "status": "clarify", "original_query": query,
                    "clarification": {
                        "reason": "질문과 선택 문맥에서 대상 기업을 찾을 수 없습니다.",
                        "question": "어느 기업을 확인할까요? 기업 이름이나 ID를 알려 주세요.",
                        "options": ["SUP-0034", "SUP-0041", "SUP-0052"],
                    },
                }
            return json.dumps(result, ensure_ascii=False)
        fixture = self.fixtures.get(query.strip())
        if fixture is None:
            raise UnsupportedStubQuery("Only documented fixture queries are supported")
        return fixture.model_copy(update={"original_query": query}, deep=True).model_dump_json(by_alias=True)


class FixtureGenerationBackend:
    """A deterministic model draft that the real Generator must verify."""

    async def generate(self, system: str, user: str) -> str:
        matches = re.findall(r'<e id="(E\d+)"[^>]* citable="yes" label="([^"]*)">', user)
        if matches:
            evidence_id, label = matches[0]
            body = f"검증용 가상 모델 응답입니다. {label} 자료를 확인했습니다. [{evidence_id}]"
            used = [evidence_id]
        else:
            body = "검증용 가상 모델의 개념 설명입니다."
            used = []
        return json.dumps({"answer": body, "used_evidence": used}, ensure_ascii=False)


class GenerationStubToolCaller:
    """Return shared fixture data without opening a model, MCP or DB connection."""

    async def __call__(self, server: str, tool: str, args: dict[str, Any]) -> Any:
        if server == "law-mcp" and tool == "search_regulation":
            fixture = json.loads((FIXTURE_DIRECTORY.parent / "routing" / "07_number_needs_grounding.json").read_text())
            call = fixture["routing"]["outcomes"][0]["calls"][0]
            # Router appends the user's wording to the decomposed question, so match on that question only.
            if (not args.get("query", "").startswith(PAYMENT_QUERY)
                    or {k: v for k, v in args.items() if k != "query"}
                    != {k: v for k, v in call["args"].items() if k != "query"}):
                raise ValueError("No fixture is configured for this regulation search")
            return [deepcopy(hit) | {"stub": True} for hit in call["result"]]
        if server == "law-mcp" and tool == "get_article":
            for name in ("01_law_article", "07_number_needs_grounding"):
                fixture = json.loads((FIXTURE_DIRECTORY.parent / "routing" / f"{name}.json").read_text())
                for call in fixture["routing"]["outcomes"][0]["calls"]:
                    if call["tool"] == tool and args == call["args"]:
                        return deepcopy(call["result"]) | {"stub": True}
            raise ValueError("No fixture is configured for this article")
        if server == "business-mcp" and tool == "find_suppliers":
            supplier = args["supplier"].strip()
            records = stub_suppliers()
            id_match = re.fullmatch(r"(?:SUP-?)?([0-9]{1,4})", supplier, flags=re.IGNORECASE)
            if id_match:
                sid = f"SUP-{int(id_match[1]):04d}"
                matches = [item for item in records if item["supplier_id"] == sid]
                match_type = "id"
            else:
                exact = [item for item in records if item["name"] == supplier]
                matches = exact or [item for item in records if supplier.casefold() in item["name"].casefold()]
                match_type = "exact" if exact else "partial"
            return {
                "matches": [{key: item[key] for key in ("supplier_id", "name", "region")}
                            for item in matches[:max(1, min(args.get("limit", 10), 100))]],
                "total": len(matches), "match_type": match_type,
            }
        if server == "business-mcp" and tool == "search_suppliers":
            rows = []
            for item in stub_suppliers():
                if args.get("region") and item["region"] != args["region"]:
                    continue
                if args.get("iatf_required") and not item["iatf16949"]:
                    continue
                for capability in item["capabilities"]:
                    if any(args.get(key) and args[key] != capability[key] for key in ("process", "material")):
                        continue
                    if capability["monthly_capacity"] < args.get("min_monthly_capacity", 0):
                        continue
                    rows.append({key: item[key] for key in ("supplier_id", "name", "region", "iatf16949")} | {
                        **capability, "defect_ppm": item["quality"]["defect_ppm"],
                        "on_time_rate": item["quality"]["on_time_delivery_rate"], "source": "STUB", "stub": True,
                    })
            rows.sort(key=lambda row: (row["defect_ppm"] is None, row["defect_ppm"] or 0,
                                       row["supplier_id"], row["process"], row["material"]))
            if args.get("distinct_suppliers"):
                representatives = {}
                for row in rows:
                    representatives.setdefault(row["supplier_id"], row)
                rows = list(representatives.values())
            return rows[:max(1, min(args.get("limit", 20), 100))]
        if server == "business-mcp" and tool == "get_supplier":
            if args["supplier"] == "SUP-0060":
                return next(item for item in stub_suppliers() if item["supplier_id"] == "SUP-0060") | {
                    "source": "STUB", "stub": True,
                }
            supplier = await SupplierChatService().detail(args["supplier"])
            return supplier.model_dump(mode="json") | {"source": "STUB", "stub": True}
        raise ValueError("No fixture is configured for this tool")
