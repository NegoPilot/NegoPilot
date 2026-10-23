"""Generation 단위 테스트.

입력은 shared/fixtures/routing 11개(분해 fixtures를 실제 business-mcp·law-mcp로 Routing한 결과)다.
모델은 가짜 백엔드로 대신해서 비용이 들지 않고, MCP 서버도 필요 없다.

    uv run python -m pytest -q tests/test_generation.py
"""

import json
from pathlib import Path

import pytest
from langgraph.graph import END, START, StateGraph

from negopilot_shared import DecompositionResult, RouteResult

from negopilot_agent.generation import AnswerGenerator, build_evidence, make_generate_node
from negopilot_agent.generation import verify
from negopilot_agent.generation.answer import Evidence
from negopilot_agent.generation.node import GenerationState
from negopilot_agent.generation.prompt import build_user_message

FIXTURES = Path(__file__).resolve().parents[2] / "shared" / "fixtures"
ROUTING_DIR = FIXTURES / "routing"
NAMES = sorted(p.stem for p in ROUTING_DIR.glob("*.json"))


def load(name: str) -> tuple[DecompositionResult, RouteResult]:
    data = json.loads((ROUTING_DIR / f"{name}.json").read_text(encoding="utf-8"))
    dec = json.loads((FIXTURES / data["decomposition"]).read_text(encoding="utf-8"))
    return DecompositionResult.model_validate(dec["expected"]), RouteResult.model_validate(data["routing"])


def make_decomposition(sub_queries: list[dict], assumptions: list[str] | None = None) -> DecompositionResult:
    base = {"question": "질문", "original_text": "원문", "grounding": "required", "confidence": 0.9,
            "entities": {}, "inputs": [], "depends_on": []}
    return DecompositionResult.model_validate({
        "status": "ok", "original_query": "테스트", "sub_queries": [base | sq for sq in sub_queries],
        "assumptions": assumptions or []})


class FakeBackend:
    """정해 둔 응답을 순서대로 돌려준다 (마지막 응답은 반복). 함수면 (system, user)로 응답을 만든다."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.prompts: list[str] = []

    async def generate(self, system: str, user: str):
        self.prompts.append(user)
        r = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        return r(system, user) if callable(r) else r


def draft(answer: str, used: list[str] | None = None) -> str:
    return json.dumps({"answer": answer, "used_evidence": used or []}, ensure_ascii=False)


def cite_first(system: str, user: str) -> str:
    """인용할 수 있는 첫 근거를 인용하고, 근거가 없으면 개념 설명만 하는 가짜 모델."""
    for line in user.splitlines():
        if line.startswith('<e id="') and 'citable="yes"' in line:
            eid = line.split('"')[1]
            return draft(f"요청하신 내용을 정리했습니다. [{eid}]", [eid])
    return draft("RFQ는 완성차가 부품 협력사에 보내는 견적 요청서를 말합니다.")


async def run(name: str, backend=None):
    d, r = load(name)
    gen = AnswerGenerator(backend=backend or FakeBackend(cite_first))
    return await gen.generate(d, r), r


def outcome_with(status: str, **kw) -> dict:
    return {"id": kw.pop("id", "q1"), "intent": kw.pop("intent", "supplier_ranking"), "status": status} | kw


GOOD_CALL = {"server": "business-mcp", "tool": "get_supplier", "args": {"supplier": "SUP-0001"},
             "result": {"supplier_id": "SUP-0001", "name": "(주)대한프레스", "source": "MOCK", "defect_ppm": 120}}
BAD_CALL = {"server": "business-mcp", "tool": "get_supplier", "args": {"supplier": "SUP-0002"},
            "error": "ConnectError: connect to 10.0.0.5:9102 failed"}


# ---------- fixtures 11개 ----------

EXPECTED_KIND = {
    "01_law_article": "answer", "02_parallel_suppliers": "answer", "03_rank_then_law": "answer",
    "04_business_wording": "answer", "05_offer_check": "answer", "06_general_knowledge": "answer",
    "07_number_needs_grounding": "answer", "08_assumption_instead_of_clarify": "answer",
    "09_clarify": "clarify", "10_out_of_scope": "refusal", "11_failed": "failed",
}


def test_all_routing_fixtures_are_covered():
    assert set(NAMES) == set(EXPECTED_KIND)


@pytest.mark.parametrize("name", NAMES)
async def test_every_fixture_gets_expected_kind(name):
    answer, _ = await run(name)
    assert answer.kind == EXPECTED_KIND[name]
    assert answer.text


@pytest.mark.parametrize("name", [n for n, k in EXPECTED_KIND.items() if k == "answer" and n != "06_general_knowledge"])
async def test_tool_answers_cite_and_pass_verification(name):
    answer, _ = await run(name)
    assert answer.grounded and answer.citations
    assert "[1]" in answer.text and "[E" not in answer.text
    assert answer.verification.ok


async def test_clarify_shows_options_without_calling_model():
    d, r = load("09_clarify")
    gen = AnswerGenerator()   # 백엔드를 안 넣어도 모델 없이 끝나야 한다 (API 키 불필요)
    answer = await gen.generate(d, r)
    assert answer.kind == "clarify" and answer.text == d.clarification.question
    assert answer.options == d.clarification.options
    assert gen._backend is None


async def test_decomposition_failure_requests_retry_without_company_or_rfq_details():
    backend = FakeBackend(cite_first)
    answer, _ = await run("11_failed", backend=backend)
    assert answer.kind == "failed"
    assert "다시 시도해 주세요" in answer.text
    assert all(label not in answer.text for label in ("기업", "협력사", "RFQ"))
    assert answer.options == []
    assert backend.prompts == []


async def test_out_of_scope_refuses_and_lists_capabilities():
    answer, _ = await run("10_out_of_scope")
    assert "범위 밖" in answer.text and "협력사 검색" in answer.text


async def test_general_knowledge_is_marked_ungrounded():
    answer, _ = await run("06_general_knowledge")
    assert not answer.grounded and not answer.citations


async def test_assumptions_are_shown():
    d, _ = load("08_assumption_instead_of_clarify")
    answer, _ = await run("08_assumption_instead_of_clarify")
    assert f"※ 가정: {d.assumptions[0]}" in answer.text


# ---------- 근거 인용 규칙 (Routing 계약 1절 5·6) ----------

async def test_law_check_can_cite_search_hits_and_valid_full_articles():
    _, r = load("07_number_needs_grounding")
    ev = build_evidence(r)
    hits = [e for e in ev if e.kind == "regulation_hit"]
    refs = [e for e in ev if e.kind == "article_reference"]
    assert hits and all(e.citable for e in hits)
    assert refs and all(e.citable and e.source_url for e in refs)
    search = next(c for c in r.outcome("q1").calls if c.tool == "search_regulation")
    assert hits[0].label == search.result[0]["citation"]    # 항 단위 citation 그대로


async def test_citing_valid_full_article_is_allowed():
    _, r = load("07_number_needs_grounding")
    ref = next(e for e in build_evidence(r) if e.kind == "article_reference")
    backend = FakeBackend(draft(f"조문 전문입니다. [{ref.id}]"))
    answer, _ = await run("07_number_needs_grounding", backend=backend)
    assert answer.verification.attempts == 1 and answer.verification.ok
    assert answer.citations[0].label == ref.label
    assert answer.citations[0].source_url == ref.source_url


async def test_law_article_cites_get_article_citation():
    _, r = load("01_law_article")
    ev = build_evidence(r)
    assert len(ev) == 1 and ev[0].citable
    assert ev[0].label == r.outcome("q1").calls[0].result["citation"]


async def test_offer_check_cites_referenced_articles():
    _, r = load("05_offer_check")
    ev = build_evidence(r)
    kinds = [e.kind for e in ev]
    assert kinds[0] == "offer_verdict" and "offer_finding" in kinds
    refs = [e for e in ev if e.kind == "referenced_article"]
    expected = [a.result["citation"] for a in r.outcome("q1").referenced_articles]
    assert [e.label for e in refs] == expected and all(e.citable for e in refs)


async def test_failed_referenced_article_keeps_only_the_number():
    d, r = load("05_offer_check")
    o = r.outcome("q1")
    broken = o.referenced_articles[0].model_copy(update={"result": None, "error": "ToolError: 조회 실패"})
    r = r.model_copy(update={"outcomes": [o.model_copy(update={"referenced_articles": [broken, *o.referenced_articles[1:]]})]})
    ev = build_evidence(r)
    assert broken.args["article_no"] not in [e.meta.get("article_no") for e in ev if e.kind == "referenced_article"]
    # 검사 항목 본문에 번호가 남아 있어 번호만 언급하는 건 근거 검사를 통과한다
    no = broken.args["article_no"]
    report = verify.check(f"하도급법 {no} 위반 소지가 있습니다.", ev)
    assert not report.ungrounded_articles


async def test_parallel_results_reach_the_model_together():
    """fixture 03: q1(순위)과 q2(법 검토)가 따로 오지만 한 프롬프트에 함께 들어가야 합쳐서 답할 수 있다."""
    backend = FakeBackend(cite_first)
    await run("03_rank_then_law", backend=backend)
    prompt = backend.prompts[0]
    assert 'sub_query="q1"' in prompt and 'sub_query="q2"' in prompt
    assert "1위" in prompt


# ---------- 근거 검사 ----------

async def test_unknown_citation_triggers_retry():
    _, r = load("07_number_needs_grounding")
    hit = next(e for e in build_evidence(r) if e.citable)
    backend = FakeBackend(draft("지급 기한 조항입니다. [E99]"), draft(f"지급 기한 조항입니다. [{hit.id}]"))
    answer, _ = await run("07_number_needs_grounding", backend=backend)
    assert answer.verification.attempts == 2 and answer.verification.ok
    assert "E99" in backend.prompts[1]


async def test_ungrounded_number_is_masked_after_retry():
    backend = FakeBackend(draft("지급 기한은 93일입니다."))
    answer, _ = await run("07_number_needs_grounding", backend=backend)
    assert answer.verification.masked
    assert "93일" not in answer.text and verify.MASK in answer.text


async def test_general_knowledge_cannot_state_articles_or_numbers():
    backend = FakeBackend(draft("RFQ 회신은 보통 14일 안에 하며 하도급법 제3조가 적용됩니다."))
    answer, _ = await run("06_general_knowledge", backend=backend)
    assert "제3조" not in answer.text and "14일" not in answer.text


def test_numbers_from_question_ratio_and_commas_are_allowed():
    ev = [Evidence(id="E1", sub_query="q1", source="business", kind="offer_verdict", label="검사",
                   text='{"unit_price":12000,"raw_material_ratio":0.45}')]
    report = verify.check("단가 12,000원, 원재료 비중 45%, 결제 75일 [E1]", ev, ["결제 75일로 제안해도 돼?"])
    assert report.ok


def test_render_citations_numbers_by_first_appearance():
    ev = [Evidence(id=f"E{i}", sub_query="q1", source="law", kind="regulation_hit", label=f"조문{i}", text="")
          for i in (1, 2, 3)]
    text, cites = verify.render_citations("가 [E3]. 나 [E1, E3]. 다 [E7].", ev)
    assert text == "가 [1]. 나 [2, 1]. 다 ."
    assert [(c.n, c.evidence_id) for c in cites] == [(1, "E3"), (2, "E1")]


# ---------- 하위 질문 status별 처리 (Routing 계약 3절) ----------

async def generate_with(sub_queries: list[dict], outcomes: list[dict], halted: bool = False, backend=None):
    d = make_decomposition(sub_queries)
    r = RouteResult.model_validate({"status": "routed", "outcomes": outcomes, "halted": halted,
                                    "halt_reason": "q1: 0건" if halted else None})
    return await AnswerGenerator(backend=backend or FakeBackend(cite_first)).generate(d, r)


async def test_missing_args_asks_in_plain_words():
    answer = await generate_with([{"id": "q1", "intent": "offer_check"}],
                                 [outcome_with("missing_args", intent="offer_check",
                                               missing=["payment_days", "price_linkage"], message="check_offer 필수 인자 없음")])
    assert answer.kind == "clarify"
    assert "결제 기간(일)" in answer.text and "연동 약정" in answer.text
    assert "payment_days" not in answer.text and "필수 인자" not in answer.text


async def test_error_shows_generic_text_for_that_part_only():
    sqs = [{"id": "q1", "intent": "supplier_detail", "original_text": "SUP-0001"},
           {"id": "q2", "intent": "supplier_detail", "original_text": "SUP-0002"}]
    answer = await generate_with(sqs, [outcome_with("ok", intent="supplier_detail", calls=[GOOD_CALL]),
                                       outcome_with("error", id="q2", intent="supplier_detail", calls=[BAD_CALL],
                                                    message=BAD_CALL["error"])])
    assert answer.kind == "answer"                                   # 된 부분은 답한다
    assert "'SUP-0002' 부분은 자료를 가져오지 못했습니다." in answer.text
    assert "10.0.0.5" not in answer.text and "ConnectError" not in answer.text
    assert answer.text.rstrip().endswith("자료를 가져오지 못했습니다.")   # 안내는 답변 끝에


async def test_error_only_is_no_result():
    answer = await generate_with([{"id": "q1", "intent": "supplier_detail", "original_text": "SUP-0002"}],
                                 [outcome_with("error", intent="supplier_detail", calls=[BAD_CALL])])
    assert answer.kind == "no_result" and "10.0.0.5" not in answer.text


async def test_empty_and_skipped_after_halt():
    sqs = [{"id": "q1", "intent": "rfq_lookup", "original_text": "이번 주 마감 RFQ"},
           {"id": "q2", "intent": "supplier_ranking", "original_text": "협력사 추천",
            "inputs": [{"slot": "rfq_id", "from": "q1", "select": "top1"}], "depends_on": ["q1"]}]
    answer = await generate_with(sqs, [outcome_with("empty", intent="rfq_lookup", message="q1 0건"),
                                       outcome_with("skipped", id="q2", message="앞 결과 0건")], halted=True)
    assert answer.kind == "no_result"
    assert "조건에 맞는 결과가 없습니다" in answer.text
    assert "협력사 추천" not in answer.text                      # skipped는 원인을 이미 말했으니 따로 안 말함
    assert "0건" not in answer.text                              # message·halt_reason은 보여 주지 않음


async def test_unsupported_mail_is_announced_with_answer():
    sqs = [{"id": "q1", "intent": "supplier_detail"}, {"id": "q2", "intent": "draft_email", "original_text": "메일 써줘"}]
    answer = await generate_with(sqs, [outcome_with("ok", intent="supplier_detail", calls=[GOOD_CALL]),
                                       outcome_with("unsupported", id="q2", intent="draft_email")])
    assert answer.kind == "answer" and "메일 작성은 아직 지원하지 않습니다" in answer.text


async def test_model_refusal_returns_failed():
    answer, _ = await run("01_law_article", backend=FakeBackend(None))
    assert answer.kind == "failed"


async def test_prompt_marks_citable_evidence():
    d, r = load("04_business_wording")
    ev = build_evidence(r)
    ev.append(Evidence(id="E999", sub_query="q1", source="law", kind="reference", label="참고 자료",
                       text="인용할 수 없는 참고 자료", citable=False))
    user = build_user_message(d, ev, [])
    assert 'citable="yes"' in user and 'citable="no"' in user and "<question>" in user


# ---------- LangGraph ----------

async def test_generate_node_in_graph():
    graph = StateGraph(GenerationState)
    graph.add_node("generate", make_generate_node(AnswerGenerator(backend=FakeBackend(cite_first))))
    graph.add_edge(START, "generate")
    graph.add_edge("generate", END)
    app = graph.compile()

    d, r = load("05_offer_check")
    state = await app.ainvoke({"query": d.original_query, "decomposition": d, "routing": r})
    assert state["answer"].kind == "answer"
    assert state["answer"].citations[0].label.startswith("협상안 검사 결과")

async def test_company_size_never_calls_generation_model():
    from negopilot_agent.generation.generator import plan
    d, r = load('05_offer_check')
    evidence = [Evidence(id='E1', sub_query='q1', source='business', kind='company_size',
                         label='기업 규모 검토', text='{"is_sme":true,"is_small_business":null,"missing":["independence_met"]}', meta={'role':'buyer'})]
    backend = FakeBackend(draft('소기업이 아닙니다.'))
    answer = await AnswerGenerator(backend=backend)._write(d, evidence, plan(d, r))
    assert not backend.prompts
    assert answer.kind == 'answer'
    assert '소기업 판정 보류' in answer.text
    assert answer.citations
