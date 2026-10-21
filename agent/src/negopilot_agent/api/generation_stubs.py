"""Fixture model and tool I/O for the real graph and AnswerGenerator.

These backends return model drafts. Evidence extraction, validation and final
Answer construction remain the responsibility of the unchanged Agent core.
"""

import json
import re
from copy import deepcopy
from typing import Any

from .stubs import FIXTURE_DIRECTORY, FixtureDecomposer, UnsupportedStubQuery
from .supplier_chat_service import (
    FINANCIAL_QUERY, OVERVIEW_ALIAS, OVERVIEW_QUERY, QUALITY_QUERY, SupplierChatService,
)

SUPPLIER_QUERIES = {OVERVIEW_QUERY, FINANCIAL_QUERY, QUALITY_QUERY, OVERVIEW_ALIAS}


class FixtureDecompositionBackend:
    """Bind validated request data so history cannot select another fixture.

    The real QueryDecomposer still constructs and sends its prompt to this fake
    backend. Fixture selection uses the request rather than parsing that text.
    """

    def __init__(self, query: str, context: dict[str, list[str]]) -> None:
        self.fixtures = FixtureDecomposer().fixtures
        self.query = query
        self.context = deepcopy(context)

    async def generate(self, system: str, user: str) -> str:
        query = self.query
        selected = self.context["selected_supplier_ids"]
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
                        "reason": "검증할 가상 기업이 선택되지 않았습니다.",
                        "question": "검증할 가상 기업을 선택해 주세요.",
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
        if server == "law-mcp" and tool == "get_article":
            fixture = json.loads((FIXTURE_DIRECTORY.parent / "routing" / "01_law_article.json").read_text())
            call = fixture["routing"]["outcomes"][0]["calls"][0]
            if args != call["args"]:
                raise ValueError("No fixture is configured for this article")
            return deepcopy(call["result"]) | {"stub": True}
        if server == "business-mcp" and tool == "get_supplier":
            supplier = await SupplierChatService().detail(args["supplier"])
            return supplier.model_dump(mode="json") | {"source": "STUB", "stub": True}
        raise ValueError("No fixture is configured for this tool")
