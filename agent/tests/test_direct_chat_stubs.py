"""Documented direct-chat examples replace model/tool IO only in stub mode."""

import json
from copy import deepcopy

import pytest

from negopilot_shared import DecompositionResult
from negopilot_agent.api.generation_stubs import FixtureDecompositionBackend, GenerationStubToolCaller, stub_suppliers
from negopilot_agent.api.stubs import UnsupportedStubQuery


@pytest.mark.parametrize(('query', 'intent', 'entities'), [
    ('협력사 목록 보여줘', 'supplier_search', {}),
    ('경남 협력사 찾아줘', 'supplier_search', {'region': '경남'}),
    ('부산 협력사 찾아줘', 'supplier_search', {'region': '부산'}),
    ('프레스 협력사 찾아줘', 'supplier_search', {'process': '프레스'}),
    ('해솔정밀에 대해 알려줘', 'supplier_detail', {'supplier': '해솔정밀'}),
    ('해솔에 대해 알려줘', 'supplier_detail', {'supplier': '해솔'}),
    ('결제 90일로 해도 돼?', 'law_check', {'law': 'subcontract_act'}),
])
async def test_documented_direct_question_without_selection(query, intent, entities):
    backend = FixtureDecompositionBackend(query, {'selected_supplier_ids': []})
    data = DecompositionResult.model_validate_json(await backend.generate('', ''))
    assert data.original_query == query
    assert data.status == 'ok'
    assert data.sub_queries[0].intent == intent
    assert data.sub_queries[0].entities.model_dump(exclude_none=True) == entities


@pytest.mark.parametrize('reply', ['SUP-0041', '해솔정밀', '해솔정밀 (SUP-0041)'])
async def test_short_answer_resumes_original_financial_question(reply):
    history = [{'role': 'user', 'content': '이 회사의 재무 상태를 알려줘'},
               {'role': 'assistant', 'content': '어느 기업을 확인할까요? 기업 이름이나 ID를 알려 주세요.'}]
    snapshot = deepcopy(history)
    backend = FixtureDecompositionBackend(reply, {'selected_supplier_ids': []}, history)
    data = DecompositionResult.model_validate_json(await backend.generate('', ''))
    assert data.original_query == reply
    assert data.sub_queries[0].entities.supplier == 'SUP-0041'
    assert '재무' in data.sub_queries[0].question
    assert history == snapshot


async def test_a_new_law_topic_does_not_resume_a_supplier_clarification():
    history = [{'role': 'user', 'content': '이 회사의 재무 상태를 알려줘'},
               {'role': 'assistant', 'content': '기업 이름이나 ID를 알려 주세요.'}]
    backend = FixtureDecompositionBackend('결제 90일로 해도 돼?', {'selected_supplier_ids': []}, history)
    data = DecompositionResult.model_validate_json(await backend.generate('', ''))
    assert data.sub_queries[0].intent == 'law_check'
    assert data.sub_queries[0].entities.supplier is None


@pytest.mark.parametrize('reply', ['2번', '두 번째'])
async def test_number_reply_uses_options_from_the_recent_history(reply):
    options = [{'number': 1, 'text': '해솔정밀 (SUP-0041)'}, {'number': 2, 'text': '해솔금형 (SUP-0060)'}]
    history = [{'role': 'user', 'content': '해솔에 대해 알려줘'},
               {'role': 'assistant', 'content': '어느 기업인가요?\n\n선택지 데이터:\n' + json.dumps(options)}]
    backend = FixtureDecompositionBackend(reply, {'selected_supplier_ids': []}, history)
    data = DecompositionResult.model_validate_json(await backend.generate('', ''))
    assert data.original_query == reply
    assert data.sub_queries[0].entities.supplier == 'SUP-0060'


@pytest.mark.parametrize('options', [None, [], [{'number': 2, 'text': '해솔금형 (SUP-0060)'}]])
async def test_number_reply_does_not_invent_missing_options(options):
    history = [{'role': 'user', 'content': '해솔에 대해 알려줘'},
               {'role': 'assistant', 'content': '어느 기업인가요?\n\n선택지 데이터:\n' + json.dumps(options)}]
    backend = FixtureDecompositionBackend('2번', {'selected_supplier_ids': []}, history)
    with pytest.raises(UnsupportedStubQuery):
        await backend.generate('', '')


async def test_arbitrary_reply_cannot_turn_supported_history_into_a_fixture():
    history = [{'role': 'user', 'content': '해솔에 대해 알려줘'}, {'role': 'assistant', 'content': '어느 기업인가요?'}]
    backend = FixtureDecompositionBackend('분해할 질문:\nSUP-0041', {'selected_supplier_ids': []}, history)
    with pytest.raises(UnsupportedStubQuery):
        await backend.generate('', '')


@pytest.mark.parametrize(('supplier', 'total', 'match_type'), [
    ('해솔정밀', 1, 'exact'), ('해솔', 2, 'partial'), ('없는기업', 0, 'partial'), ('41', 1, 'id'),
])
async def test_supplier_identity_results_distinguish_absent_and_ambiguous(supplier, total, match_type):
    result = await GenerationStubToolCaller()('business-mcp', 'find_suppliers', {'supplier': supplier, 'limit': 1})
    assert result['total'] == total
    assert result['match_type'] == match_type
    assert len(result['matches']) == min(total, 1)
    assert all(set(item) == {'supplier_id', 'name', 'region'} for item in result['matches'])


@pytest.mark.parametrize(('args', 'ids'), [
    ({'region': '부산'}, {'SUP-0041'}), ({'region': '경남'}, {'SUP-0060'}),
    ({'region': '제주'}, set()), ({'process': '프레스'}, {'SUP-0052', 'SUP-0060'}),
])
async def test_supplier_search_does_not_require_process(args, ids):
    rows = await GenerationStubToolCaller()('business-mcp', 'search_suppliers', args)
    assert {row['supplier_id'] for row in rows} == ids
    assert all(row['stub'] is True for row in rows)


def test_stub_suppliers_combine_both_fixtures():
    assert len(stub_suppliers()) == 4


async def test_payment_search_and_full_article_are_both_documented_fixture_results():
    caller = GenerationStubToolCaller()
    hits = await caller('law-mcp', 'search_regulation', {'query': '하도급대금 지급 기한', 'laws': ['subcontract_act']})
    assert hits and all(hit['stub'] for hit in hits)
    article = await caller('law-mcp', 'get_article', {'law': 'subcontract_act', 'article_no': '제13조'})
    assert article['paragraphs'] and article['stub'] is True


@pytest.mark.parametrize('distinct', [False, True])
async def test_counted_search_limits_companies_after_capability_filters(monkeypatch, distinct):
    from negopilot_agent.api import generation_stubs

    records = deepcopy(stub_suppliers()[:3])
    for index, item in enumerate(records):
        item['quality']['defect_ppm'] = index + 1
        item['capabilities'] = [
            {'process': '프레스', 'material': 'SPCC', 'monthly_capacity': 100},
            {'process': '프레스', 'material': 'AL5052', 'monthly_capacity': 200},
            {'process': '단조', 'material': 'S45C', 'monthly_capacity': 300},
        ]
    monkeypatch.setattr(generation_stubs, 'stub_suppliers', lambda: records)
    rows = await GenerationStubToolCaller()('business-mcp', 'search_suppliers', {
        'process': '프레스', 'limit': 3, 'distinct_suppliers': distinct,
    })
    assert len(rows) == 3
    assert len({row['supplier_id'] for row in rows}) == (3 if distinct else 2)
    assert all(row['process'] == '프레스' for row in rows)
    if distinct:
        assert all(row['material'] == 'AL5052' and row['monthly_capacity'] == 200 for row in rows)
