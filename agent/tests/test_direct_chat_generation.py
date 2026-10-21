"""Direct chat Generation checks with synthetic routing and fake model backends."""

import json
import re

import pytest

from negopilot_shared import DecompositionResult, RouteResult

from negopilot_agent.generation import AnswerGenerator
from negopilot_agent.generation.generator import (
    ARTICLE_LOOKUP_NOTICE, SUPPLIER_LOOKUP_NOTICE, SUPPLIER_SELECTION_TEXT,
)


class NeverBackend:
    async def generate(self, system: str, user: str) -> str:
        raise AssertionError("This result must not call a model backend")


class EvidenceBackend:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def generate(self, system: str, user: str) -> str:
        self.prompts.append(user)
        match = re.search(r'<e id="(E[0-9]+)"[^>]* citable="yes"', user)
        assert match is not None
        evidence_id = match.group(1)
        return json.dumps({"answer": f"확인된 근거를 안내합니다. [{evidence_id}]",
                           "used_evidence": [evidence_id]}, ensure_ascii=False)


def candidate(number: int, name: str | None = None) -> dict:
    return {"supplier_id": f"SUP-{number:04d}", "name": name or f"가상협력사{number}", "region": "부산"}


def lookup_call(matches: list[dict] | None = None, **overrides) -> dict:
    rows = matches if matches is not None else [candidate(41, "해솔정밀"), candidate(42, "해솔부품")]
    return {"server": "business-mcp", "tool": "find_suppliers", "args": {"supplier": "해솔", "limit": 10},
            "result": {"matches": rows, "total": len(rows), "match_type": "partial"}} | overrides


def ambiguous_outcome(**overrides) -> dict:
    return {"id": "q1", "intent": "supplier_detail", "status": "missing_args", "missing": ["supplier"],
            "calls": [lookup_call()], "message": "INTERNAL_LOOKUP_MARKER"} | overrides


async def generate(outcomes: list[dict], backend=None):
    decomposition = DecompositionResult.model_validate({
        "status": "ok", "original_query": "협력사와 관련 법령 정보를 알려줘",
        "sub_queries": [{"id": outcome["id"], "intent": outcome["intent"], "question": "정보를 알려줘",
                         "original_text": "정보 조회", "grounding": "required", "confidence": 1.0}
                        for outcome in outcomes],
    })
    routing = RouteResult.model_validate({"status": "routed", "outcomes": outcomes})
    return await AnswerGenerator(backend=backend or NeverBackend()).generate(decomposition, routing)


async def test_ambiguous_company_uses_structured_options_without_model():
    answer = await generate([ambiguous_outcome()])
    assert answer.kind == "clarify"
    assert answer.text == SUPPLIER_SELECTION_TEXT
    assert answer.options == ["해솔정밀 (SUP-0041)", "해솔부품 (SUP-0042)"]
    assert "부산" not in " ".join(answer.options)
    assert "INTERNAL_LOOKUP_MARKER" not in answer.model_dump_json()


async def test_admin_created_five_digit_company_ids_remain_selectable():
    rows = [candidate(10000, "수동기업"), candidate(10001, "수동부품")]
    answer = await generate([ambiguous_outcome(calls=[lookup_call(rows)])])
    assert answer.kind == "clarify"
    assert answer.options == ["수동기업 (SUP-10000)", "수동부품 (SUP-10001)"]


async def test_company_options_are_unique_and_bounded():
    rows = [candidate(1), candidate(1), *[candidate(i) for i in range(2, 14)]]
    answer = await generate([ambiguous_outcome(calls=[lookup_call(rows)])])
    assert len(answer.options) == 10
    assert answer.options == [f"가상협력사{i} (SUP-{i:04d})" for i in range(1, 11)]


@pytest.mark.parametrize("call", [
    lookup_call(server="untrusted-server"),
    lookup_call(tool="get_supplier"),
    lookup_call(error="INTERNAL_LOOKUP_MARKER"),
    lookup_call(result="해솔정밀 (SUP-0041) 또는 해솔부품 (SUP-0042)"),
    lookup_call(result={"matches": [candidate(41), candidate(42)], "total": "2", "match_type": "partial"}),
    lookup_call(result={"matches": [candidate(41), candidate(42)], "total": True, "match_type": "partial"}),
    lookup_call(result={"matches": [candidate(41), candidate(42)], "total": 2, "match_type": "unknown"}),
    lookup_call(result={"matches": [candidate(41), candidate(42)], "total": 1, "match_type": "partial"}),
    lookup_call([candidate(41), {"supplier_id": "SUP-0042\nINTERNAL_LOOKUP_MARKER", "name": "해솔부품"}]),
    lookup_call([candidate(41), candidate(42, "해솔부품\nINTERNAL_LOOKUP_MARKER")]),
    lookup_call([candidate(41)]),
])
async def test_untrusted_or_malformed_candidate_data_is_not_an_option(call):
    answer = await generate([ambiguous_outcome(calls=[call])])
    assert answer.kind == "clarify"
    assert answer.options == []
    assert "INTERNAL_LOOKUP_MARKER" not in answer.model_dump_json()


@pytest.mark.parametrize("status", ["empty", "error"])
async def test_no_result_or_tool_failure_never_becomes_company_clarification(status):
    call = lookup_call(error="INTERNAL_LOOKUP_MARKER") if status == "error" else lookup_call()
    answer = await generate([ambiguous_outcome(status=status, missing=[], calls=[call])])
    assert answer.kind == "no_result"
    assert answer.options == []
    assert "INTERNAL_LOOKUP_MARKER" not in answer.model_dump_json()


async def test_partial_answer_preserves_company_choices_and_missing_notice():
    backend = EvidenceBackend()
    answer = await generate([
        ambiguous_outcome(),
        {"id": "q2", "intent": "supplier_detail", "status": "ok", "calls": [
            {"server": "business-mcp", "tool": "get_supplier", "args": {"supplier": "SUP-0052"},
             "result": {"supplier_id": "SUP-0052", "name": "다온프레스", "source": "MOCK"}},
        ]},
    ], backend)
    assert answer.kind == "answer" and answer.grounded
    assert answer.options == ["해솔정밀 (SUP-0041)", "해솔부품 (SUP-0042)"]
    assert SUPPLIER_SELECTION_TEXT in answer.notices
    assert answer.verification.ok and len(backend.prompts) == 1
    assert "INTERNAL_LOOKUP_MARKER" not in backend.prompts[0] + answer.model_dump_json()


async def test_company_selection_does_not_hide_other_missing_fields():
    answer = await generate([
        ambiguous_outcome(),
        {"id": "q2", "intent": "offer_check", "status": "missing_args", "missing": ["payment_days"]},
    ])
    assert answer.kind == "clarify"
    assert SUPPLIER_SELECTION_TEXT in answer.text and "결제 기간(일)" in answer.text
    assert len(answer.options) == 2


def supplier_detail_call(number: int, error=None) -> dict:
    return {"server": "business-mcp", "tool": "get_supplier", "args": {"supplier": f"SUP-{number:04d}"},
            "error": error, "result": {"supplier_id": f"SUP-{number:04d}", "source": "MOCK",
                                      "name": "FAILED_DETAIL_MARKER" if error else "확인된 협력사"}}


async def test_partial_supplier_details_keep_only_successful_evidence_and_fixed_notice():
    backend = EvidenceBackend()
    answer = await generate([{
        "id": "q1", "intent": "supplier_detail", "status": "ok",
        "calls": [supplier_detail_call(41), supplier_detail_call(42, "PRIVATE_SUPPLIER_ERROR"),
                  supplier_detail_call(43, "SECOND_SUPPLIER_ERROR")],
    }], backend)
    assert answer.kind == "answer" and answer.grounded and answer.verification.ok
    assert len(answer.evidence) == 1 and answer.evidence[0].meta["supplier_id"] == "SUP-0041"
    assert answer.notices.count(SUPPLIER_LOOKUP_NOTICE) == 1
    assert answer.text.count(SUPPLIER_LOOKUP_NOTICE) == 1
    assert len(backend.prompts) == 1
    rendered = backend.prompts[0] + answer.model_dump_json()
    assert all(marker not in rendered for marker in [
        "PRIVATE_SUPPLIER_ERROR", "SECOND_SUPPLIER_ERROR", "FAILED_DETAIL_MARKER", "SUP-0042", "SUP-0043",
    ])


async def test_successful_supplier_details_have_no_partial_failure_notice():
    answer = await generate([{
        "id": "q1", "intent": "supplier_detail", "status": "ok",
        "calls": [supplier_detail_call(41), supplier_detail_call(42)],
    }], EvidenceBackend())
    assert answer.kind == "answer" and len(answer.evidence) == 2
    assert SUPPLIER_LOOKUP_NOTICE not in answer.notices


async def test_all_supplier_details_failed_never_uses_partial_success_notice_or_model():
    answer = await generate([{
        "id": "q1", "intent": "supplier_detail", "status": "error",
        "calls": [supplier_detail_call(41, "PRIVATE_SUPPLIER_ERROR"), supplier_detail_call(42, "OTHER_ERROR")],
    }])
    assert answer.kind == "no_result" and answer.evidence == []
    assert SUPPLIER_LOOKUP_NOTICE not in answer.notices
    assert "PRIVATE_SUPPLIER_ERROR" not in answer.model_dump_json()


def law_search_call(result=None) -> dict:
    return {"server": "law-mcp", "tool": "search_regulation", "args": {"query": "대금 지급 기한"},
            "result": result if result is not None else [{
                "law_key": "subcontract_act", "article_no": "제13조", "type": "조문",
                "citation": "가상 법령 제13조 제1항", "text": "가상 지급 기한 근거입니다.",
            }]}


def article_call(error=None) -> dict:
    return {"server": "law-mcp", "tool": "get_article",
            "args": {"law": "subcontract_act", "article_no": "제13조"}, "error": error,
            "result": {"law_key": "subcontract_act", "article_no": "제13조", "citation": "가상 법령 제13조",
                       "source_url": "https://example.test/law/13",
                       "paragraphs": [{"no": "①", "text": "실패한 전문 원문" if error else "성공한 전문 원문"}]}}


@pytest.mark.parametrize("include_successful_article", [False, True])
async def test_partial_law_lookup_keeps_search_citation_and_fixed_notice(include_successful_article):
    calls = [law_search_call(), article_call("INTERNAL_LAW_MARKER"), article_call("SECOND_ERROR_MARKER")]
    if include_successful_article:
        calls.append(article_call())
    backend = EvidenceBackend()
    answer = await generate([{"id": "q1", "intent": "law_check", "status": "ok", "calls": calls}], backend)
    assert answer.kind == "answer" and answer.grounded
    assert answer.citations[0].label == "가상 법령 제13조 제1항"
    assert answer.notices.count(ARTICLE_LOOKUP_NOTICE) == 1
    assert answer.text.count(ARTICLE_LOOKUP_NOTICE) == 1
    assert answer.verification.ok and len(backend.prompts) == 1
    references = [item for item in answer.evidence if item.kind == "article_reference"]
    assert len(references) == int(include_successful_article)
    assert all(item.citable for item in references)
    rendered = backend.prompts[0] + answer.model_dump_json()
    assert all(marker not in rendered for marker in ["INTERNAL_LAW_MARKER", "SECOND_ERROR_MARKER", "실패한 전문 원문"])


async def test_successful_law_lookup_has_no_failure_notice():
    answer = await generate([{"id": "q1", "intent": "law_check", "status": "ok",
                              "calls": [law_search_call(), article_call()]}], EvidenceBackend())
    assert answer.kind == "answer" and ARTICLE_LOOKUP_NOTICE not in answer.notices


@pytest.mark.parametrize("intent,status,call", [
    ("law_check", "empty", law_search_call([])),
    ("law_check", "error", law_search_call() | {"error": "INTERNAL_LAW_MARKER"}),
    ("law_article", "error", article_call("INTERNAL_LAW_MARKER")),
])
async def test_empty_or_failed_law_search_uses_no_model(intent, status, call):
    answer = await generate([{"id": "q1", "intent": intent, "status": status, "calls": [call]}])
    assert answer.kind == "no_result" and answer.options == []
    assert ARTICLE_LOOKUP_NOTICE not in answer.notices
    assert "INTERNAL_LAW_MARKER" not in answer.model_dump_json()
