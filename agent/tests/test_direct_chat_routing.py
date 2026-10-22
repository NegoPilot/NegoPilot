"""질문만으로 조회하는 Routing 회귀. 실제 모델과 MCP 및 DB는 호출하지 않는다."""

import json
import socket
from copy import deepcopy

import pytest

from negopilot_shared import DecompositionResult
from negopilot_agent.api.execution import SafeToolCaller
from negopilot_agent.generation import AnswerGenerator
from negopilot_agent.generation.evidence import build_evidence
from negopilot_agent.routing import Router
from negopilot_agent.routing.extract import select_values


MATCHES = [
    {"supplier_id": "SUP-0041", "name": "대한정밀", "region": "경남"},
    {"supplier_id": "SUP-0042", "name": "대한금속", "region": "경기"},
]
LAW_HITS = [{"law_key": "subcontract_act", "type": "조문", "article_no": "제13조",
             "citation": "하도급법 제13조①", "text": "하도급대금 지급에 관한 규정"}]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("External connections are forbidden")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)


def decomposition(intent="supplier_detail", entities=None, *, sub_queries=None):
    base = {"question": "기업과 법령 조회", "original_text": "조회해줘", "grounding": "required",
            "confidence": 0.9}
    rows = sub_queries or [{"id": "q1", "intent": intent, "entities": entities or {}}]
    return DecompositionResult.model_validate({
        "status": "ok", "original_query": "조회해줘", "sub_queries": [base | row for row in rows]})


class Caller:
    def __init__(self, **overrides):
        self.calls = []
        self.overrides = overrides

    async def __call__(self, server, tool, args):
        self.calls.append((server, tool, deepcopy(args)))
        defaults = {
            "find_suppliers": {"matches": MATCHES[:1], "total": 1, "match_type": "exact"},
            "search_suppliers": MATCHES,
            "get_supplier": {"supplier_id": args.get("supplier"), "name": "대한정밀", "source": "MOCK"},
            "search_regulation": LAW_HITS,
            "get_article": {"law_key": "subcontract_act", "article_no": "제13조",
                            "citation": "하도급법 제13조", "paragraphs": [{"no": "①", "text": "지급 규정"}]},
            "list_rfqs": [{"rfq_id": "RFQ-2026-001"}],
            "rank_suppliers": {"ranked": []},
            "check_offer": {"verdict": "문제 없음", "violations": [], "warnings": [], "info": [], "law_refs": []},
        }
        value = self.overrides.get(tool, defaults[tool])
        if callable(value):
            value = value(args)
        if isinstance(value, Exception):
            raise value
        return deepcopy(value)


class GenerationBackend:
    def __init__(self):
        self.calls = []

    async def generate(self, system, user):
        self.calls.append((system, user))
        return json.dumps({"answer": "확인한 자료입니다. [E1]", "used_evidence": ["E1"]}, ensure_ascii=False)


async def run_answer(d, caller):
    routing = await Router(SafeToolCaller(caller)).route(d)
    backend = GenerationBackend()
    answer = await AnswerGenerator(backend=backend).generate(d, routing)
    return routing, answer, backend


async def test_admin_created_five_digit_id_is_resolved_before_detail_lookup():
    caller = Caller(find_suppliers={"matches": [{"supplier_id": "SUP-10000", "name": "수동기업"}],
                                   "total": 1, "match_type": "exact"})
    routing = await Router(caller).route(decomposition("supplier_detail", {"supplier": "수동기업"}))
    assert routing.outcome("q1").status == "ok"
    assert caller.calls[-1] == ("business-mcp", "get_supplier", {"supplier": "SUP-10000"})


@pytest.mark.parametrize("entities", [{}, {"region": "경남"}, {"material": "알루미늄"},
                                      {"iatf_required": True}, {"top_n": 3}])
async def test_supplier_search_does_not_require_process(entities):
    caller = Caller()
    routing, answer, _ = await run_answer(decomposition("supplier_search", entities), caller)
    expected = {"limit" if key == "top_n" else key: value for key, value in entities.items()}
    if "top_n" in entities:
        expected["distinct_suppliers"] = True
    assert caller.calls == [("business-mcp", "search_suppliers", expected)]
    assert routing.outcome("q1").status == "ok"
    assert answer.kind == "answer" and answer.citations


async def test_explicit_supplier_count_keeps_all_filters_and_counts_companies_before_fanout():
    companies = [
        {"supplier_id": f"SUP-{number:04d}", "name": f"테스트기업{number}", "region": "부산",
         "process": "프레스", "material": "강판", "monthly_capacity": 1000, "source": "MOCK"}
        for number in range(41, 44)
    ]
    def search(args):
        assert args == {"process": "프레스", "material": "강판", "region": "부산", "iatf_required": True,
                        "min_monthly_capacity": 500, "limit": 3, "distinct_suppliers": True}
        return companies

    d = decomposition(sub_queries=[
        {"id": "q1", "intent": "supplier_search",
         "entities": {"process": "프레스", "material": "강판", "region": "부산", "iatf_required": True,
                      "min_monthly_capacity": 500, "top_n": 3}},
        {"id": "q2", "intent": "supplier_detail", "depends_on": ["q1"],
         "inputs": [{"slot": "supplier", "from": "q1", "select": "all"}]},
    ])
    original = d.model_dump()
    caller = Caller(search_suppliers=search)
    routing, answer, _ = await run_answer(d, caller)

    assert d.model_dump() == original
    assert routing.outcome("q1").calls[0].result == companies
    assert [args["supplier"] for _, tool, args in caller.calls if tool == "get_supplier"] == [
        "SUP-0041", "SUP-0042", "SUP-0043"]
    assert len([item for item in answer.evidence if item.kind == "supplier_hit"]) == 3


async def test_search_without_count_preserves_capability_rows_and_original_arguments():
    rows = [MATCHES[0] | {"process": "프레스"}, MATCHES[0] | {"process": "CNC가공"}, MATCHES[1]]
    caller = Caller(search_suppliers=rows)
    routing = await Router(caller).route(decomposition("supplier_search", {"region": "부산"}))
    assert caller.calls == [("business-mcp", "search_suppliers", {"region": "부산"})]
    assert routing.outcome("q1").calls[0].result == rows


async def test_ranking_top_n_does_not_receive_distinct_search_argument():
    caller = Caller()
    await Router(caller).route(decomposition("supplier_ranking", {"rfq_id": "RFQ-2026-001", "top_n": 3}))
    assert caller.calls == [("business-mcp", "rank_suppliers", {"rfq_id": "RFQ-2026-001", "top_n": 3})]


@pytest.mark.parametrize("match_type", ["exact", "partial"])
async def test_one_name_match_uses_confirmed_id_and_only_detail_evidence(match_type):
    caller = Caller(find_suppliers={"matches": MATCHES[:1], "total": 1, "match_type": match_type})
    routing, answer, _ = await run_answer(decomposition(entities={"supplier": "대한"}), caller)
    assert caller.calls == [
        ("business-mcp", "find_suppliers", {"supplier": "대한", "limit": 10}),
        ("business-mcp", "get_supplier", {"supplier": "SUP-0041"}),
    ]
    outcome = routing.outcome("q1")
    assert select_values("supplier_detail", outcome.calls, "supplier", "top1") == ["SUP-0041"]
    assert [e.kind for e in answer.evidence] == ["supplier"]
    assert answer.kind == "answer" and answer.verification.ok


@pytest.mark.parametrize("supplier", ["SUP-0041", "SUP-TEST"])
async def test_explicit_id_keeps_existing_direct_lookup(supplier):
    caller = Caller()
    routing = await Router(caller).route(decomposition(entities={"supplier": supplier}))
    assert routing.outcome("q1").status == "ok"
    assert caller.calls == [("business-mcp", "get_supplier", {"supplier": supplier})]


async def test_ambiguous_name_asks_with_structured_candidates_without_guessing():
    caller = Caller(find_suppliers={"matches": MATCHES, "total": 2, "match_type": "partial"})
    routing, answer, backend = await run_answer(decomposition(entities={"supplier": "대한"}), caller)
    assert routing.outcome("q1").status == "missing_args"
    assert routing.outcome("q1").missing == ["supplier"]
    assert answer.kind == "clarify"
    assert answer.options == ["대한정밀 (SUP-0041)", "대한금속 (SUP-0042)"]
    assert len(caller.calls) == 1 and not backend.calls


@pytest.mark.parametrize(("intent", "entities", "overrides"), [
    ("supplier_detail", {"supplier": "없는기업"},
     {"find_suppliers": {"matches": [], "total": 0, "match_type": "partial"}}),
    ("supplier_search", {}, {"search_suppliers": []}),
    ("law_check", {}, {"search_regulation": []}),
    ("rfq_lookup", {}, {"list_rfqs": []}),
])
async def test_empty_lookup_returns_no_result_without_generation(intent, entities, overrides):
    caller = Caller(**overrides)
    routing, answer, backend = await run_answer(decomposition(intent, entities), caller)
    assert routing.outcome("q1").status == "empty"
    assert not routing.halted
    assert answer.kind == "no_result" and not answer.options
    assert answer.evidence == [] and not backend.calls
    assert len(caller.calls) == 1


@pytest.mark.parametrize("tool", ["find_suppliers", "get_supplier", "search_regulation"])
async def test_tool_failure_is_no_result_and_never_candidate_clarification(tool):
    d = decomposition("law_check") if tool == "search_regulation" else decomposition(
        entities={"supplier": "SUP-0041" if tool == "get_supplier" else "대한"})
    caller = Caller(**{tool: RuntimeError("SECRET_CANDIDATE_OR_CONNECTION_STRING")})
    routing, answer, backend = await run_answer(d, caller)
    assert routing.outcome("q1").status == "error"
    assert answer.kind == "no_result" and not answer.options and not backend.calls
    assert "SECRET" not in answer.model_dump_json()


@pytest.mark.parametrize("result", [
    None, [], {}, {"matches": [], "total": True, "match_type": "partial"},
    {"matches": [], "total": 1, "match_type": "partial"},
    {"matches": MATCHES, "total": 1, "match_type": "partial"},
    {"matches": MATCHES, "total": 2, "match_type": []},
    {"matches": [{"supplier_id": "invented", "name": "기업"}], "total": 1, "match_type": "exact"},
    {"matches": [MATCHES[0], MATCHES[0]], "total": 2, "match_type": "exact"},
])
async def test_malformed_name_lookup_cannot_become_company_facts(result):
    caller = Caller(find_suppliers=result)
    routing, answer, backend = await run_answer(decomposition(entities={"supplier": "대한"}), caller)
    assert routing.outcome("q1").status == "error"
    assert answer.kind == "no_result" and answer.evidence == [] and not backend.calls
    assert len(caller.calls) == 1


async def test_empty_search_skips_dependents_but_allows_independent_later_group():
    d = decomposition(sub_queries=[
        {"id": "q1", "intent": "supplier_search"},
        {"id": "q2", "intent": "rfq_lookup"},
        {"id": "q3", "intent": "supplier_detail", "depends_on": ["q1"],
         "inputs": [{"slot": "supplier", "from": "q1", "select": "top1"}]},
        {"id": "q4", "intent": "supplier_ranking", "depends_on": ["q2"],
         "inputs": [{"slot": "rfq_id", "from": "q2", "select": "top1"}]},
    ])
    caller = Caller(search_suppliers=[])
    routing = await Router(caller).route(d)
    assert [o.status for o in routing.outcomes] == ["empty", "ok", "skipped", "ok"]
    assert not routing.halted
    assert ("business-mcp", "rank_suppliers", {"rfq_id": "RFQ-2026-001"}) in caller.calls


async def test_law_search_remains_evidence_when_article_enrichment_fails():
    caller = Caller(get_article=RuntimeError("PRIVATE_UPSTREAM_DETAIL"))
    routing, answer, backend = await run_answer(decomposition("law_check", {"as_of": "2026-10-21"}), caller)
    assert routing.outcome("q1").status == "ok"
    assert [e.kind for e in build_evidence(routing)] == ["regulation_hit"]
    assert answer.kind == "answer" and answer.citations and answer.notices
    assert answer.verification.ok and len(backend.calls) == 1
    assert "PRIVATE" not in answer.model_dump_json()
    assert all(args.get("as_of") == "2026-10-21" for _, _, args in caller.calls)


async def test_partial_answer_preserves_evidence_and_only_missing_information():
    caller = Caller()
    d = decomposition(sub_queries=[
        {"id": "q1", "intent": "law_check"},
        {"id": "q2", "intent": "offer_check", "entities": {"supplier": "SUP-0041", "rfq_id": "RFQ-2026-001",
                                                                 "unit_price": 12000, "payment_days": 30}},
    ])
    routing, answer, backend = await run_answer(d, caller)
    assert routing.outcome("q2").missing == ["price_linkage"]
    assert answer.kind == "answer" and answer.citations and len(backend.calls) == 1
    assert any("연동" in notice for notice in answer.notices)


def search_then_details():
    return decomposition(sub_queries=[
        {"id": "q1", "intent": "supplier_search"},
        {"id": "q2", "intent": "supplier_detail", "depends_on": ["q1"],
         "inputs": [{"slot": "supplier", "from": "q1", "select": "all"}]},
    ])


@pytest.mark.parametrize("failure", [RuntimeError("PRIVATE_SUPPLIER_FAILURE"), {}, None])
async def test_supplier_detail_fanout_keeps_valid_success_and_only_failed_lookup_notice(failure):
    caller = Caller(get_supplier=lambda args: (
        {"supplier_id": args["supplier"], "name": "대한정밀", "source": "MOCK"}
        if args["supplier"] == "SUP-0041" else failure))
    routing, answer, backend = await run_answer(search_then_details(), caller)
    details = routing.outcome("q2")
    assert details.status == "ok"
    assert len([call for call in details.calls if call.error]) == 1
    successful = [item for item in answer.evidence if item.sub_query == "q2"]
    assert len(successful) == 1 and successful[0].meta["supplier_id"] == "SUP-0041"
    assert answer.kind == "answer" and answer.notices and len(backend.calls) == 1
    assert "PRIVATE" not in answer.model_dump_json() + backend.calls[0][1]


@pytest.mark.parametrize("failure", [RuntimeError("PRIVATE_SUPPLIER_FAILURE"), {}, None])
async def test_supplier_detail_fanout_without_valid_success_is_still_error(failure):
    caller = Caller(get_supplier=failure)
    routing = await Router(SafeToolCaller(caller)).route(search_then_details())
    assert routing.outcome("q2").status == "error"
    assert not [item for item in build_evidence(routing) if item.sub_query == "q2"]


OFFER = {"rfq_id": "RFQ-2026-001", "unit_price": 12000, "payment_days": 45, "price_linkage": True}


async def test_offer_check_by_name_checks_the_confirmed_supplier_id():
    """offer_check도 이름이면 find_suppliers로 대상을 확정한 뒤 그 ID로 검사한다 (#129)."""
    caller = Caller()
    routing = await Router(caller).route(decomposition("offer_check", {"supplier": "대한"} | OFFER))
    assert routing.outcome("q1").status == "ok"
    assert caller.calls == [
        ("business-mcp", "find_suppliers", {"supplier": "대한", "limit": 10}),
        ("business-mcp", "check_offer", {"supplier": "SUP-0041"} | OFFER),
    ]


async def test_offer_check_with_ambiguous_name_asks_instead_of_checking():
    """이름이 여러 곳에 맞으면 check_offer를 부르지 않고 후보를 되묻는다."""
    caller = Caller(find_suppliers={"matches": MATCHES, "total": 2, "match_type": "partial"})
    routing, answer, backend = await run_answer(decomposition("offer_check", {"supplier": "대한"} | OFFER), caller)
    outcome = routing.outcome("q1")
    assert (outcome.status, outcome.missing) == ("missing_args", ["supplier"])
    assert [tool for _, tool, _ in caller.calls] == ["find_suppliers"]
    assert answer.kind == "clarify"
    assert answer.options == ["대한정밀 (SUP-0041)", "대한금속 (SUP-0042)"]
    assert not backend.calls


async def test_offer_check_with_unknown_name_is_empty_without_checking():
    caller = Caller(find_suppliers={"matches": [], "total": 0, "match_type": "partial"})
    routing = await Router(caller).route(decomposition("offer_check", {"supplier": "없는기업"} | OFFER))
    assert routing.outcome("q1").status == "empty"
    assert [tool for _, tool, _ in caller.calls] == ["find_suppliers"]


async def test_offer_check_with_explicit_id_skips_name_lookup():
    caller = Caller()
    routing = await Router(caller).route(decomposition("offer_check", {"supplier": "SUP-0041"} | OFFER))
    assert routing.outcome("q1").status == "ok"
    assert caller.calls == [("business-mcp", "check_offer", {"supplier": "SUP-0041"} | OFFER)]
