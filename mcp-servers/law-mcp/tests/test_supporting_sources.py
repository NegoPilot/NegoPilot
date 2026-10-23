import asyncio
import hashlib
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
import server

async def call(name, args):
    async with Client(server.mcp) as client:
        result = await client.call_tool(name, args)
        return result.data

def test_official_catalog_has_all_p0_and_original_checksums():
    docs=asyncio.run(call('list_supporting_documents', {'as_of':'2026-10-23'}))
    assert {d['document_id'] for d in docs} == {
        'sme_annex_1','sme_annex_2','sme_annex_3','manufacturing_scope','linkage_guideline',
        'late_interest','bill_discount','price_reduction','restricted_information',
        'linkage_templates','energy_linkage_guide'}
    for d in docs:
        assert d['available_as_of']
        assert d['source_url'].startswith(('https://www.law.go.kr/','https://www.mss.go.kr/','https://www.smes.go.kr/'))
        for a in d['assets']:
            assert hashlib.sha256((server.supporting_sources.ROOT/a['path']).read_bytes()).hexdigest()==a['sha256']

@pytest.mark.parametrize('query,expected',[
    ('자동차 제조업 중소기업 규모 기준','sme_annex_1'),
    ('원가계산서 원가내역서 경영상 정보 요구','restricted_information'),
    ('선급금 지연이율 15.5','late_interest'),
    ('어음 할인율 7.5','bill_discount'),
    ('표준 미연동계약서','linkage_templates')])
def test_search_retrieves_official_evidence(query,expected):
    hits=asyncio.run(call('search_regulation',{'query':query,'as_of':'2026-10-23','top_k':10}))
    assert any(h.get('document_id')==expected for h in hits)

def test_future_annex_not_used_for_old_contract():
    with pytest.raises(ToolError,match='판본'):
        asyncio.run(call('get_supporting_document',{'document_id':'sme_annex_1','as_of':'2025-01-01'}))
    hits=asyncio.run(call('search_regulation',{'query':'자동차 중소기업 규모','as_of':'2025-01-01','top_k':20}))
    assert not any(h.get('document_id','').startswith('sme_annex') for h in hits)

def test_small_business_industry_table_natural_query_top_five():
    hits=asyncio.run(call('search_regulation', {
        'query':'소기업 해당 여부를 확인하는 업종별 기준표',
        'as_of':'2026-10-23', 'top_k':5}))
    assert any(h.get('document_id')=='sme_annex_3' for h in hits)

def test_full_template_pages_reconstruct_without_loss():
    first=asyncio.run(call('get_supporting_document',{'document_id':'linkage_templates','limit':100}))
    assert first['effective_date'] is None
    assert first['published_date']=='2026-08-18'
    assert first['next_offset']==100
    parts=[]; offset=0
    while True:
        page=asyncio.run(call('get_supporting_document',{'document_id':'linkage_templates','offset':offset,'limit':2000}))
        parts.append(page['text'])
        if page['next_offset'] is None: break
        offset=page['next_offset']
    assert ''.join(parts)==server.SUPPORTING_SOURCES['linkage_templates']['text']

