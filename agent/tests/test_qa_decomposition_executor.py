"""QA connector tests only. No classification model or live MCP is evaluated."""

from contextlib import asynccontextmanager
from copy import deepcopy
from decimal import Decimal
import json

import pytest

from negopilot_shared import DecompositionResult
from negopilot_agent.qa_decomposition.contracts import QAPlan
from negopilot_agent.qa_decomposition.executor import execute


QUERY = "A기업의 최신 견적에서 5% 할인한 금액을 계산해줘"


def core(query=QUERY, *, supplier="A기업", intent="supplier_detail"):
    return {"schema_version": "0.1", "status": "ok", "original_query": query,
            "sub_queries": [{"id": "q1", "question": query, "original_text": query,
                             "intent": intent, "grounding": "required", "confidence": 1,
                             "entities": {} if supplier is None else {"supplier": supplier}}]}


def discount(*, amount="12000", rate="5", step_id="discount"):
    return {"id": step_id, "kind": "internal", "operation": "apply_discount",
            "arguments": {"amount": amount, "discount_pct": rate}}


def plan(*steps, query=QUERY):
    return {"schema_version": "qa-decomposition.v1", "status": "ok", "original_query": query,
            "steps": list(steps)}


def lookup(*, supplier="A기업"):
    return {"id": "lookup", "kind": "routing", "decomposition": core(supplier=supplier)}


def reference(**changes):
    return {"from_step": "lookup", "from_sub_query": "q1", "selector": "latest_quote_unit_price"} | changes


class Caller:
    def __init__(self, *, quotes=None, tool_error=None):
        self.calls = []
        self.sessions = 0
        self.tool_error = tool_error
        self.quotes = quotes if quotes is not None else [
            {"quoted_at": "2026-01-02", "unit_price": 10000, "rfq_id": "RFQ-2026-001"},
            {"quoted_at": "2026-02-01", "unit_price": 12000, "rfq_id": "RFQ-2026-002"},
        ]

    @asynccontextmanager
    async def session(self):
        self.sessions += 1
        yield

    async def __call__(self, server, tool, args):
        self.calls.append((server, tool, deepcopy(args)))
        if self.tool_error == tool:
            raise RuntimeError("Synthetic MCP failure")
        assert server == "business-mcp"
        if tool == "find_suppliers":
            return {"matches": [{"supplier_id": "SUP-0001", "name": "A기업"}], "total": 1, "match_type": "exact"}
        assert tool == "get_supplier"
        return {"supplier_id": args["supplier"], "name": "A기업", "recent_quotes": deepcopy(self.quotes)}


async def test_internal_discount_uses_decimal_and_no_mcp():
    caller = Caller()
    result = await execute(plan(discount()), query=QUERY, caller=caller)
    assert result["status"] == "completed" and result["classification"] == "internal"
    assert result["final_result"] == {"amount": "12000", "discount_pct": "5", "discount_amount": "600",
                                      "discounted_amount": "11400"}
    assert caller.calls == [] and caller.sessions == 0
    assert result["events"] == [{"sequence": 1, "kind": "internal", "step_id": "discount",
                                 "target": "apply_discount", "arguments": {"amount": "12000", "discount_pct": "5"},
                                 "result": result["final_result"], "status": "ok", "error": None}]


@pytest.mark.parametrize(("amount", "rate", "expected"), [(0, 5, "0"), (12000, 100, "0"),
                                                         ("0.1", "5", "0.095"), (0.1, 5, "0.095")])
async def test_decimal_and_percentage_boundaries(amount, rate, expected):
    result = await execute(plan(discount(amount=amount, rate=rate)), query=QUERY, caller=Caller())
    assert result["status"] == "completed"
    assert result["final_result"]["discounted_amount"] == expected


async def test_existing_core_contract_runs_the_real_router_without_internal_calculation():
    caller = Caller()
    result = await execute(DecompositionResult.model_validate(core()), query=QUERY, caller=caller)
    assert result["status"] == "completed" and result["classification"] == "mcp"
    assert caller.sessions == 1
    assert caller.calls == [("business-mcp", "find_suppliers", {"supplier": "A기업", "limit": 10}),
                            ("business-mcp", "get_supplier", {"supplier": "SUP-0001"})]
    assert result["final_result"]["outcomes"][0]["calls"][-1]["result"]["recent_quotes"] == caller.quotes
    assert [event["sequence"] for event in result["events"]] == [1, 2]


async def test_hybrid_binds_the_newest_quote_and_records_actual_order():
    caller = Caller()
    raw = plan(lookup(), discount(amount=reference()))
    result = await execute(raw, query=QUERY, caller=caller)
    assert result["status"] == "completed" and result["classification"] == "mcp_then_internal"
    assert result["final_result"]["discounted_amount"] == "11400"
    assert [event["target"] for event in result["events"]] == ["business-mcp.find_suppliers", "business-mcp.get_supplier",
                                                             "latest_quote_unit_price", "apply_discount"]
    assert [event["kind"] for event in result["events"]] == ["mcp", "mcp", "binding", "internal"]
    assert [event["sequence"] for event in result["events"]] == [1, 2, 3, 4]
    assert result["events"][2]["arguments"] == reference()
    assert result["events"][3]["arguments"] == {"amount": "12000", "discount_pct": "5"}
    assert raw["steps"][1]["arguments"]["amount"] == reference()


@pytest.mark.parametrize(("field", "value"), [("amount", True), ("amount", -1), ("amount", "NaN"),
                                                ("amount", "Infinity"), ("amount", "0e999999999"),
                                                ("amount", "1e-999999999"), ("amount", "1e30"),
                                                ("discount_pct", True), ("discount_pct", -1),
                                                ("discount_pct", 101), ("discount_pct", float("nan"))])
async def test_invalid_numbers_fail_before_any_mcp_call(field, value):
    raw = plan(lookup(), discount())
    raw["steps"][1]["arguments"][field] = value
    caller = Caller()
    result = await execute(raw, query=QUERY, caller=caller)
    assert result["status"] == "validation_failed"
    assert result["errors"][0]["code"] == "INVALID_DECOMPOSITION"
    assert result["events"] == [] and caller.calls == []


@pytest.mark.parametrize("raw", [
    plan(lookup(), discount(amount=reference(from_step="future"))),
    plan(discount(amount=reference(from_step="discount"))),
    plan(discount(amount=reference()), lookup()),
    plan(lookup(), discount(amount=reference(from_sub_query="q9"))),
    plan(lookup(), discount(amount=reference(selector="__dict__"))),
    plan(discount(), discount()),
    plan(discount() | {"operation": "check_offer"}),
    plan(lookup(), discount() | {"operation": "eval"}),
    plan(discount() | {"unexpected": "value"}),
    plan(discount()) | {"unexpected": "value"},
    plan(lookup() | {"decomposition": core(query="unrelated question")}),
    plan(discount(), query="wrong input"),
    "not an output object",
])
async def test_invalid_graph_and_unsupported_functions_fail_closed(raw):
    caller = Caller()
    result = await execute(raw, query=QUERY, caller=caller)
    assert result["status"] == "validation_failed"
    assert result["errors"][0]["code"] == "INVALID_DECOMPOSITION"
    assert result["events"] == [] and caller.calls == []


async def test_mutated_core_model_is_revalidated():
    raw = DecompositionResult.model_validate(core())
    raw.sub_queries[0].intent = "not_a_registered_intent"
    result = await execute(raw, query=QUERY, caller=Caller())
    assert result["status"] == "validation_failed" and not result["events"]


@pytest.mark.parametrize("raw", [plan(lookup(), discount(amount=None)),
                                 plan(lookup(), discount(rate=None)),
                                 plan(lookup(supplier=None), discount())])
async def test_missing_required_input_holds_the_whole_plan_and_asks_before_calling(raw):
    caller = Caller()
    result = await execute(raw, query=QUERY, caller=caller)
    assert result["status"] == "clarify"
    assert result["errors"][0]["code"] == "MISSING_INPUT"
    assert result["clarification"]["question"]
    assert result["events"] == [] and caller.calls == []


@pytest.mark.parametrize(("quotes", "code"), [
    ([], "QUOTE_NOT_FOUND"),
    ([{"quoted_at": "2026-02-30", "unit_price": 12000}], "INVALID_QUOTE"),
    ([{"quoted_at": "20260201", "unit_price": 12000}], "INVALID_QUOTE"),
    ([{"quoted_at": "2026-02-01", "unit_price_krw": 12000}], "INVALID_QUOTE"),
    ([{"quoted_at": "2026-02-01", "unit_price": True}], "INVALID_QUOTE"),
    ([{"quoted_at": "2026-02-01", "unit_price": 0}], "INVALID_QUOTE"),
    ([{"quoted_at": "2026-02-01", "unit_price": -1}], "INVALID_QUOTE"),
    ([{"quoted_at": "2026-02-01", "unit_price": "12000"}], "INVALID_QUOTE"),
    ([{"quoted_at": "2026-02-01", "unit_price": 12000},
      {"quoted_at": "2026-02-01", "unit_price": 13000}], "AMBIGUOUS_QUOTE"),
])
async def test_missing_malformed_and_ambiguous_quote_blocks_computation(quotes, code):
    result = await execute(plan(lookup(), discount(amount=reference())), query=QUERY, caller=Caller(quotes=quotes))
    assert result["status"] == "execution_failed" and result["classification"] == "mcp"
    assert result["errors"][0]["code"] == code
    assert result["final_result"] is None
    assert [event["kind"] for event in result["events"]] == ["mcp", "mcp", "binding"]
    assert result["events"][-1]["status"] == "error"


@pytest.mark.parametrize("tool", ["find_suppliers", "get_supplier"])
async def test_mcp_failure_blocks_downstream_internal_execution(tool):
    result = await execute(plan(lookup(), discount(amount=reference())), query=QUERY, caller=Caller(tool_error=tool))
    assert result["status"] == "execution_failed"
    assert result["errors"][0]["code"] == "TOOL_ERROR"
    assert all(event["kind"] == "mcp" for event in result["events"])
    assert result["events"][-1]["error"]["exception_type"] == "RuntimeError"


async def test_general_knowledge_is_not_falsely_reported_as_an_internal_function():
    raw = core(supplier=None, intent="general_knowledge")
    raw["sub_queries"][0]["grounding"] = "none"
    result = await execute(raw, query=QUERY, caller=Caller())
    assert result["status"] == "completed" and result["classification"] == "none"
    assert result["events"] == []


async def test_unsupported_planned_intent_does_not_trigger_calls():
    raw = core(supplier=None, intent="draft_email")
    result = await execute(raw, query=QUERY, caller=Caller())
    assert result["status"] == "validation_failed"
    assert result["errors"][0]["code"] == "UNSUPPORTED_TARGET"
    assert result["events"] == []


async def test_existing_clarification_and_failed_outputs_do_not_execute():
    clarify = {"schema_version": "0.1", "status": "clarify", "original_query": QUERY,
               "clarification": {"reason": "정보 부족", "question": "어느 기업인가요?", "options": ["기업 이름", "기업 ID"]}}
    result = await execute(clarify, query=QUERY, caller=Caller())
    assert result["status"] == "clarify" and result["clarification"] == clarify["clarification"]
    assert not result["events"]
    result = await execute({"status": "failed", "original_query": QUERY}, query=QUERY, caller=Caller())
    assert result["status"] == "failed" and result["errors"][0]["code"] == "DECOMPOSITION_FAILED"
    assert not result["events"]


async def test_native_qa_model_revalidates_and_executes():
    output = QAPlan.model_validate(plan(discount(amount=Decimal("12000"))))
    result = await execute(output, query=QUERY, caller=Caller())
    assert result["status"] == "completed"


async def test_internal_failure_is_recorded_and_stops_later_mcp(monkeypatch):
    def failure(*args):
        raise RuntimeError("Synthetic internal failure")
    monkeypatch.setattr("negopilot_agent.qa_decomposition.executor.apply_discount", failure)
    caller = Caller()
    result = await execute(plan(discount(), lookup()), query=QUERY, caller=caller)
    assert result["status"] == "execution_failed" and not caller.calls
    assert result["events"][0]["status"] == "error"
    assert result["errors"][0]["code"] == "EXECUTION_ERROR"


async def test_out_of_scope_later_in_plan_blocks_all_calls():
    unsupported = core(supplier=None, intent="out_of_scope")
    unsupported["sub_queries"][0]["grounding"] = "none"
    raw = plan(lookup(), {"id": "unsupported", "kind": "routing", "decomposition": unsupported})
    caller = Caller()
    result = await execute(raw, query=QUERY, caller=caller)
    assert result["status"] == "validation_failed"
    assert result["errors"][0]["code"] == "UNSUPPORTED_TARGET"
    assert not caller.calls and not result["events"]


async def test_invalid_source_slot_is_rejected_before_any_call():
    routing = core()
    ranking = deepcopy(routing["sub_queries"][0]) | {"id": "q2", "intent": "supplier_ranking",
        "entities": {}, "inputs": [{"slot": "rfq_id", "from": "q1", "select": "top1"}], "depends_on": ["q1"]}
    routing["sub_queries"].append(ranking)
    caller = Caller()
    result = await execute(routing, query=QUERY, caller=caller)
    assert result["status"] == "validation_failed" and result["errors"][0]["code"] == "INVALID_REFERENCE"
    assert not caller.calls


async def test_ambiguous_supplier_name_clarifies_without_get_supplier_or_calculation():
    class AmbiguousCaller(Caller):
        async def __call__(self, server, tool, args):
            self.calls.append((server, tool, args))
            assert tool == "find_suppliers"
            return {"matches": [{"supplier_id": "SUP-0001", "name": "A기업"},
                                {"supplier_id": "SUP-0002", "name": "A기업"}], "total": 2, "match_type": "exact"}
    caller = AmbiguousCaller()
    result = await execute(plan(discount(step_id="earlier"), lookup(), discount(amount=reference())),
                           query=QUERY, caller=caller)
    assert result["status"] == "clarify" and result["final_result"] is None
    assert len(caller.calls) == 1
    assert [event["target"] for event in result["events"]] == ["apply_discount", "business-mcp.find_suppliers"]
    assert all(event["status"] == "ok" for event in result["events"])


async def test_multiple_supplier_results_cannot_be_used_as_one_quote():
    class FanoutCaller(Caller):
        async def __call__(self, server, tool, args):
            if tool == "search_suppliers":
                self.calls.append((server, tool, args))
                return [{"supplier_id": "SUP-0001", "name": "A기업"}, {"supplier_id": "SUP-0002", "name": "B기업"}]
            return await super().__call__(server, tool, args)
    routing = core(supplier=None, intent="supplier_search")
    detail = deepcopy(routing["sub_queries"][0]) | {"id": "q2", "intent": "supplier_detail",
        "inputs": [{"slot": "supplier", "from": "q1", "select": "all"}], "depends_on": ["q1"]}
    routing["sub_queries"].append(detail)
    raw = plan({"id": "lookup", "kind": "routing", "decomposition": routing},
               discount(amount=reference(from_sub_query="q2")))
    result = await execute(raw, query=QUERY, caller=FanoutCaller())
    assert result["status"] == "execution_failed" and result["errors"][0]["code"] == "AMBIGUOUS_QUOTE"
    assert result["classification"] == "mcp"
    assert [event["target"] for event in result["events"]] == ["business-mcp.search_suppliers",
        "business-mcp.get_supplier", "business-mcp.get_supplier", "latest_quote_unit_price"]
    assert [event["sequence"] for event in result["events"]] == [1, 2, 3, 4]
    assert all(event["status"] != "running" for event in result["events"])


@pytest.mark.parametrize("bad_result", [{"value": float("nan")}, {"value": float("inf")},
                                        {"value": object()}, {"value": {1, 2}}])
async def test_non_json_tool_result_is_recorded_and_never_reaches_router(bad_result):
    class InvalidCaller:
        async def __call__(self, server, tool, args):
            return bad_result
    result = await execute(plan(lookup(), discount(amount=reference())), query=QUERY, caller=InvalidCaller())
    assert result["status"] == "execution_failed" and result["errors"][0]["code"] == "TOOL_ERROR"
    assert len(result["events"]) == 1
    assert result["events"][0]["status"] == "error" and result["events"][0]["result"] is None
    assert result["events"][0]["error"]["code"] == "INVALID_TOOL_RESULT"
    json.dumps(result, allow_nan=False)


async def test_qa_executor_disables_inherited_langsmith_tracing(monkeypatch):
    from langsmith import get_tracing_context, run_trees

    uploads = []
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "synthetic-unused-key")
    monkeypatch.setattr(run_trees.RunTree, "post", lambda *args, **kwargs: uploads.append("post"))
    monkeypatch.setattr(run_trees.RunTree, "patch", lambda *args, **kwargs: uploads.append("patch"))

    class ContextCaller(Caller):
        async def __call__(self, server, tool, args):
            assert get_tracing_context()["enabled"] is False
            return await super().__call__(server, tool, args)

    previous = get_tracing_context()["enabled"]
    result = await execute(core(), query=QUERY, caller=ContextCaller())
    assert result["status"] == "completed"
    assert get_tracing_context()["enabled"] == previous
    assert uploads == []


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
async def test_non_finite_core_entity_is_rejected_as_non_json_before_routing(value):
    raw = core()
    raw["sub_queries"][0]["entities"]["price_change_pct"] = value
    caller = Caller()
    result = await execute(raw, query=QUERY, caller=caller)
    assert result["status"] == "validation_failed" and not caller.calls
    assert result["normalized_plan"] is None
    json.dumps(result, allow_nan=False)
