"""Deterministic QA doubles. These mappings do not measure model classification."""

from copy import deepcopy


INTERNAL_QUERY = "12000원에서 5% 할인하면 얼마야?"
LOOKUP_QUERY = "A기업의 최신 견적을 조회해줘"
HYBRID_QUERY = "A기업의 최신 견적에서 5% 할인한 금액을 계산해줘"
MISSING_QUERY = "12000원에서 할인하면 얼마야?"
INVALID_QUERY = "12000원에서 5% 할인해줘 (잘못된 실행 대상 테스트)"
LATE_INVALID_QUERY = "A기업의 최신 견적에서 5% 할인해줘 (뒤 단계 검증 실패 테스트)"
FOLLOWUP_QUERY = "그 견적에서 5% 할인한 금액은?"
AMBIGUOUS_QUERY = "B기업의 최신 견적에서 5% 할인한 금액을 계산해줘"


def routing_step(query: str, supplier: str = "A기업") -> dict:
    return {"id": "lookup", "kind": "routing", "decomposition": {
        "schema_version": "0.1", "status": "ok", "original_query": query,
        "sub_queries": [{"id": "q1", "question": "협력사 정보와 최근 견적 조회", "original_text": query,
                         "intent": "supplier_detail", "grounding": "required", "confidence": 1.0,
                         "entities": {"supplier": supplier}, "inputs": [], "depends_on": []}],
        "clarification": None, "assumptions": [],
    }}


def discount_step(amount=12000, rate=5) -> dict:
    return {"id": "discount", "kind": "internal", "operation": "apply_discount",
            "arguments": {"amount": amount, "discount_pct": rate}}


def plan(query: str, steps: list[dict]) -> dict:
    return {"schema_version": "qa-decomposition.v1", "status": "ok", "original_query": query,
            "steps": steps, "clarification": None, "error": None}


def quote_ref() -> dict:
    return {"from_step": "lookup", "from_sub_query": "q1", "selector": "latest_quote_unit_price"}


class FixtureDecomposer:
    """A query-keyed temporary module replacing QueryDecomposer for wiring only."""

    def __init__(self):
        self.inputs: list[dict] = []

    async def decompose(self, query: str, history: list[dict] | None = None, *, context=None):
        self.inputs.append(deepcopy({"query": query, "history": history, "context": context}))
        if query == INTERNAL_QUERY:
            return plan(query, [discount_step()])
        if query == LOOKUP_QUERY:
            return plan(query, [routing_step(query)])
        if query == HYBRID_QUERY:
            return plan(query, [routing_step(query), discount_step(quote_ref())])
        if query == MISSING_QUERY:
            return plan(query, [discount_step(rate=None)])
        if query == INVALID_QUERY:
            step = discount_step()
            step["operation"] = "unsupported_operation"
            return plan(query, [step])
        if query == LATE_INVALID_QUERY:
            step = discount_step(quote_ref())
            step["operation"] = "unsupported_operation"
            return plan(query, [routing_step(query), step])
        if query == FOLLOWUP_QUERY:
            # This is an explicit fixture expectation. It is not learned history reasoning.
            if not history or context != {"selected_supplier_ids": ["SUP-0001"]}:
                raise ValueError("Fixture follow-up requires its recorded history and context")
            return plan(query, [routing_step(query, "SUP-0001"), discount_step(quote_ref())])
        if query == AMBIGUOUS_QUERY:
            return plan(query, [routing_step(query, "B기업"), discount_step(quote_ref())])
        raise ValueError("Query is not registered in the wiring fixture module")


def create_decomposer() -> FixtureDecomposer:
    return FixtureDecomposer()


class FixtureToolCaller:
    """In-process MCP boundary double. No sockets or database connections are used."""

    def __init__(self):
        self.calls: list[dict] = []

    async def __call__(self, server: str, tool: str, args: dict):
        self.calls.append(deepcopy({"server": server, "tool": tool, "arguments": args}))
        if server != "business-mcp":
            raise ValueError("Fixture server is not registered")
        if tool == "find_suppliers" and args.get("supplier") in {"A기업", "B기업"}:
            supplier = args["supplier"]
            supplier_id = "SUP-0001" if supplier == "A기업" else "SUP-0002"
            return {"matches": [{"supplier_id": supplier_id, "name": supplier}],
                    "total": 1, "match_type": "exact"}
        if tool == "get_supplier" and args.get("supplier") in {"SUP-0001", "SUP-0002"}:
            supplier_id = args["supplier"]
            quotes = [{"unit_price": 12000, "quoted_at": "2026-10-20"},
                      {"unit_price": 13000, "quoted_at": "2026-10-01"}]
            if supplier_id == "SUP-0002":
                quotes[1]["quoted_at"] = "2026-10-20"
            return {"supplier_id": supplier_id, "name": "A기업" if supplier_id == "SUP-0001" else "B기업",
                    "recent_quotes": quotes}
        raise ValueError("Fixture tool or arguments are not registered")
