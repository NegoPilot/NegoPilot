"""새 경로의 도구 실행·불확실성 보존·실패 처리를 서버/유료 호출 없이 확인한다."""
import json
import pytest
from pydantic import ValidationError
from negopilot_agent.generation import Answer
from negopilot_agent.legal_review.situation import ReviewPlan, SituationPlanner, SituationReviewer, to_decomposition, complete_plan

QUERY = '원자재가 올라서 단가 조정을 요청하고 싶어요.'


def plan_for(topics=('cost_adjustment', 'price_linkage'), questions=None):
    return ReviewPlan(issues=[dict(topic=t, rationale='요청에 관련된 경로 확인',
                                  basis_quotes=['단가 조정']) for t in topics],
                      questions=questions or [], unsupported=[])


class FixedBackend:
    def __init__(self, texts):
        self.texts = iter(texts)
        self.calls = 0

    async def generate(self, system, user):
        self.calls += 1
        return next(self.texts)


class FixedPlanner:
    def __init__(self, plan):
        self.value = plan

    async def plan(self, query, history=None):
        return self.value


class Caller:
    def __init__(self, broken=None):
        self.calls = []
        self.broken = broken

    async def __call__(self, server, tool, args):
        self.calls.append((server, tool, args))
        if tool == self.broken:
            raise RuntimeError('offline failure')
        if tool == 'search_regulation':
            return [dict(type='조문', law_key='subcontract_act', article_no='제3조',
                         text='검색 근거', citation='테스트 근거')]
        return {'law_key': args['law'], 'article_no': args['article_no'],
                'citation': '테스트 조문', 'source_url': 'https://www.law.go.kr/',
                'paragraphs': [{'no': '①', 'text': '테스트 조문 본문'}]}


class Generator:
    def __init__(self):
        self.calls = 0

    async def generate(self, decomposition, routing):
        self.calls += 1
        return Answer(kind='answer', text='확인한 근거 범위에서 설명합니다.', grounded=True)


async def test_cost_and_linkage_routes_are_both_executed_with_date():
    caller, generator = Caller(), Generator()
    p = plan_for(questions=['주요 원재료 비중과 기존 연동 약정 내용을 알려 주세요.'])
    result = await SituationReviewer(FixedPlanner(p), caller, generator).review(QUERY, '2026-10-23')
    articles = {args['article_no'] for _, tool, args in caller.calls if tool == 'get_article'}
    assert articles == {'제16조의2', '제3조', '제2조'}
    assert all(tool == 'get_article' for _, tool, _ in caller.calls)
    assert all(a['as_of'] == '2026-10-23' for _, _, a in caller.calls)
    assert all(server == 'law-mcp' for server, _, _ in caller.calls)
    assert p.questions[0] in result.answer.text
    assert result.answer.suggested_questions == []
    assert result.routing is not None and generator.calls == 1


async def test_empty_plan_does_not_execute_tools_or_generator():
    caller, generator = Caller(), Generator()
    result = await SituationReviewer(FixedPlanner(plan_for(topics=())), caller, generator).review('안녕', '2026-10-23')
    assert result.answer.kind == 'clarify'
    assert caller.calls == [] and generator.calls == 0


async def test_fabricated_fact_quote_is_rejected_and_never_routed():
    data = plan_for().model_dump()
    data['issues'][0]['basis_quotes'] = ['매출 100억원']
    bad = json.dumps(data)
    backend = FixedBackend([bad, bad])
    caller, generator = Caller(), Generator()
    result = await SituationReviewer(SituationPlanner(backend), caller, generator).review(QUERY, '2026-10-23')
    assert result.answer.kind == 'failed' and result.plan is None
    assert backend.calls == 2 and caller.calls == [] and generator.calls == 0


async def test_model_refusal_never_calls_tools():
    caller, generator = Caller(), Generator()
    result = await SituationReviewer(SituationPlanner(FixedBackend([None])), caller, generator).review(QUERY, '2026-10-23')
    assert result.answer.kind == 'failed' and not result.answer.grounded
    assert caller.calls == []


async def test_partial_request_preserves_unsupported_and_search_failure():
    p = plan_for(topics=('price_linkage',))
    p.unsupported = ['계약서를 대신 서명하는 요청']
    result = await SituationReviewer(FixedPlanner(p), Caller('get_article'), Generator()).review(QUERY, '2026-10-23')
    assert any('미지원 요청' in n for n in result.answer.notices)
    assert any('근거 미확인 쟁점' in n for n in result.answer.notices)


def test_ambiguous_timing_can_keep_both_candidates_and_question():
    p = plan_for(('price_setting', 'price_reduction'), ['이미 발주한 물량인가요, 새 발주 물량인가요?'])
    d = to_decomposition(p, '단가를 깎으려고 해요', '2026-10-23')
    assert len(d.sub_queries) == 2
    assert all('이미 발주한 물량인가요' in q.question for q in d.sub_queries)
    assert {q.entities.article_no for q in d.sub_queries} == {'제4조', '제11조'}


def test_duplicate_topics_and_invalid_date_are_rejected():
    with pytest.raises(ValidationError):
        plan_for(('price_setting', 'price_setting'))
    with pytest.raises(ValueError):
        to_decomposition(plan_for(), QUERY, '2026-02-30')


async def test_default_output_only_exposes_actual_sources_and_no_generated_legal_conclusion():
    result = await SituationReviewer(FixedPlanner(plan_for()), Caller()).review(QUERY, '2026-10-23')
    assert result.answer.kind == 'answer'
    assert result.answer.citations and result.answer.evidence
    assert '아직 판단하지 않았습니다' in result.answer.text
    assert all(c.source_url == 'https://www.law.go.kr/' for c in result.answer.citations)
    assert '테스트 조문 본문' in result.answer.evidence[0].text


def test_plan_closure_prevents_cost_and_ambiguous_route_omissions():
    p = complete_plan(plan_for(('cost_adjustment',)))
    assert {i.topic for i in p.issues} == {'cost_adjustment', 'price_linkage'}
    p = plan_for(('price_reduction',))
    p.price_stage = 'unknown'
    p = complete_plan(p)
    assert {i.topic for i in p.issues} == {'price_setting', 'price_reduction'}
    assert p.questions
    assert complete_plan(p) == p


class ArticleCaller(Caller):
    """조문마다 항 번호가 다른 본문을 돌려준다."""

    async def __call__(self, server, tool, args):
        self.calls.append((server, tool, args))
        nos = '①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱'
        return {'law_key': args['law'], 'article_no': args['article_no'],
                'citation': f"테스트 {args['article_no']}", 'source_url': 'https://www.law.go.kr/',
                'paragraphs': [{'no': no, 'text': f"{args['article_no']} {no}항 본문"} for no in nos]}


async def test_default_output_quotes_key_paragraphs_verbatim():
    result = await SituationReviewer(FixedPlanner(plan_for()), ArticleCaller()).review(QUERY, '2026-10-23')
    text = result.answer.text
    assert '> ① 제16조의2 ①항 본문' in text and '> ⑩ 제16조의2 ⑩항 본문' in text
    assert '> ④ 제3조 ④항 본문' in text
    assert '> ⑯ 제2조 ⑯항 본문' in text and '> ⑱ 제2조 ⑱항 본문' in text
    assert '제16조의2 ②항 본문' not in text   # 정해 둔 항만 발췌
    assert '전체 조문 근거에서 확인' in text


async def test_failed_lookup_has_no_excerpt():
    result = await SituationReviewer(FixedPlanner(plan_for(('price_setting',))),
                                     Caller('get_article')).review(QUERY, '2026-10-23')
    assert '근거 미확인' in result.answer.text and '  > ' not in result.answer.text


def test_long_paragraph_is_cut_and_marked():
    from negopilot_shared import RouteResult
    from negopilot_agent.legal_review.situation import key_excerpts, EXCERPT_CHARS
    routing = RouteResult(status='routed', outcomes=[dict(id='q1', intent='law_article', status='ok', calls=[dict(
        server='law-mcp', tool='get_article', args={}, result={'paragraphs': [{'no': '①', 'text': '가' * 500}]})])])
    [line] = key_excerpts(routing, 'q1', ['①'])
    assert line.endswith('…(생략)') and len(line) == len('① ') + EXCERPT_CHARS + len(' …(생략)')


def test_timing_and_scope_questions_are_not_repeated():
    p = plan_for(('price_reduction',), ['현재 단가 인하 대상이 신규 발주분인가요, 아니면 이미 위탁한 기존 물량인가요?',
                                        '해당 거래가 하도급 거래에 해당하는지 여부를 알려주십시오.',
                                        '상대방과 사전 협의가 있었나요?'])
    p.price_stage = 'unknown'
    p = complete_plan(p)
    assert p.questions == ['인하 대상은 새 발주 물량인가요, 이미 발주한 물량인가요?',
                           '해당 거래가 하도급 거래에 해당하는지 여부를 알려주십시오.',
                           '상대방과 사전 협의가 있었나요?']
    assert complete_plan(p) == p


def test_key_paragraph_numbers_exist_in_current_law_data():
    from pathlib import Path
    from negopilot_agent.legal_review.situation import ARTICLE_ANCHORS, KEY_PARAGRAPHS, DEFINITION_PARAGRAPHS
    path = Path(__file__).resolve().parents[2] / 'mcp-servers/law-mcp/data/index/subcontract_act.json'
    versions = json.loads(path.read_text())['versions']
    current = max((v for v in versions if v['effective_date'] <= '2026-10-23'), key=lambda v: v['effective_date'])
    articles = {a['no']: {p['no'] for p in a['paragraphs']} for a in current['articles']}
    for topic, anchor in ARTICLE_ANCHORS.items():
        assert set(KEY_PARAGRAPHS[topic]) <= articles[anchor], anchor
    assert set(DEFINITION_PARAGRAPHS) <= articles['제2조']
