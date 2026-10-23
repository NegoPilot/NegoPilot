import asyncio
import hashlib
import json
from pathlib import Path

import pytest
from fastmcp import Client
from pydantic import ValidationError
from company_size import assess, CompanySizeInput, RULES
import server

DAY='2026-10-23'
def company(**overrides):
    values=dict(ksic_code='C30399',average_revenue_eok=1100,assets_eok=2000,
                independence_met=True,has_related_enterprises=False,transition_reviewed=True,
                deemed_sme=False,evidence_reference='test: reviewed financials and legal criteria')
    return values | overrides

@pytest.mark.parametrize('revenue,sme',[(999,True),(1001,True),(1200,True),(1200.01,False)])
def test_auto_parts_uses_1200_instead_of_1000(revenue,sme):
    assert assess(company(average_revenue_eok=revenue),DAY)['is_sme'] is sme

@pytest.mark.parametrize('code,revenue,sme', [('C30393',1500,True),('C30393',1500.01,False),
    ('C24100',1800,True),('C26100',1001,False),('C22100',1200,True)])
def test_industry_and_seat_exceptions(code,revenue,sme):
    assert assess(company(ksic_code=code,average_revenue_eok=revenue),DAY)['is_sme'] is sme

@pytest.mark.parametrize('revenue,small',[(120,True),(120.01,False)])
def test_small_business_threshold(revenue,small):
    assert assess(company(average_revenue_eok=revenue),DAY)['is_small_business'] is small

@pytest.mark.parametrize('overrides',[
    {'average_revenue_eok':None},{'assets_eok':None},{'independence_met':None},
    {'has_related_enterprises':None},{'has_related_enterprises':True},
    {'transition_reviewed':False},{'evidence_reference':None},{'ksic_code':'G'},
    {'ksic_code':'C30'},{'ksic_code':'C31202'}])
def test_incomplete_information_never_confirms_size(overrides):
    result=assess(company(**overrides),DAY)
    assert result['is_sme'] is None and result['missing']

def test_related_aggregate_and_assets_are_not_ignored():
    assert assess(company(has_related_enterprises=True,related_aggregate_revenue_eok=1201,
                          related_calculation_verified=True),DAY)['is_sme'] is False
    assert assess(company(assets_eok=5000),DAY)['is_sme'] is False
    assert assess(company(independence_met=False),DAY)['is_sme'] is False

def test_grace_needs_explicit_review_and_does_not_prove_small_status():
    assert assess(company(average_revenue_eok=1201,deemed_sme=None),DAY)['is_sme'] is None
    result=assess(company(average_revenue_eok=1201,deemed_sme=True),DAY)
    assert result['is_sme'] is True and result['is_small_business'] is None

@pytest.mark.parametrize('day',['2025-12-31','2026-10-24'])
def test_unreviewed_dates_do_not_fallback(day):
    assert assess(company(),day)['is_sme'] is None

@pytest.mark.parametrize('value',[-1,float('nan'),float('inf')])
def test_invalid_money(value):
    with pytest.raises(ValidationError): CompanySizeInput(**company(average_revenue_eok=value))

def test_rule_hashes_match_law_mcp_originals():
    root=Path(__file__).resolve().parents[2]/'law-mcp/data/official'
    docs={d['document_id']:d for d in json.loads((root/'catalog.json').read_text())['documents']}
    for ref in RULES['sources']:
        pdf=next(a for a in docs[ref['document_id']]['assets'] if a['path'].endswith('.pdf'))
        assert pdf['sha256']==ref['sha256']
        assert hashlib.sha256((root/pdf['path']).read_bytes()).hexdigest()==ref['sha256']

async def call(name,args):
    async with Client(server.mcp) as client: return (await client.call_tool(name,args)).data

def test_assessment_through_mcp():
    result=asyncio.run(call('assess_company_size',{'company':company(),'as_of':DAY}))
    assert result['is_sme'] is True and result['thresholds']['sme_eok']==1200

@pytest.fixture
def offer_db(monkeypatch):
    monkeypatch.setattr(server,'resolve_supplier_id',lambda _: 'SUP-TEST')
    monkeypatch.setattr(server,'get_rfq',lambda _: dict(rfq_id='RFQ-TEST',part_name='부품',process='프레스',material='SPCC',annual_volume=120000,target_price=4000,required_cert='IATF16949'))
    def query(sql,params=()):
        if 'supplier_overview' in sql:
            return [dict(name='Test',source='MOCK',revenue_eok=900,raw_material_ratio=.4,iatf16949=True,
                         certification_source='MOCK',capability_source='MOCK',quality_source='MOCK')]
        if 'monthly_capacity' in sql:return [{'monthly_capacity':20000}]
        if 'SELECT unit_price' in sql:return [{'unit_price':4000}]
        if 'percentile_cont' in sql:return [{'median':4000,'n':3}]
        raise AssertionError(sql)
    monkeypatch.setattr(server,'query',query)
    monkeypatch.setattr(server.psycopg,'connect',lambda *a,**k: pytest.fail('No DB mutation in offline tests'))
    return dict(supplier='SUP-TEST',rfq_id='RFQ-TEST',unit_price=4000,payment_days=60,price_linkage=False,
                as_of=DAY,raw_material_ratio=.4)

def test_check_offer_does_not_use_last_year_revenue(offer_db):
    result=asyncio.run(call('check_offer',offer_db))
    assert result['company_size']['supplier']['is_sme'] is None
    assert 'SME_STATUS_UNKNOWN' in [f['code'] for f in result['warnings']]
    assert 'SUBCONTRACT_ACT_APPLIES' not in [f['code'] for f in result['info']]

def test_check_offer_applies_verified_small_buyer_exemption(offer_db):
    result=asyncio.run(call('check_offer',offer_db|{'supplier_size':company(),
        'buyer_size':company(average_revenue_eok=120)}))
    assert result['company_size']['supplier']['is_sme'] is True
    assert 'LINKAGE_EXEMPT' in [f['code'] for f in result['info']]
    assert not result['violations']

def test_unverified_buyer_boolean_does_not_create_exemption(offer_db):
    result=asyncio.run(call('check_offer',offer_db|{'buyer_is_small_business':True}))
    assert 'BUYER_SIZE_UNVERIFIED' in [f['code'] for f in result['warnings']]
    assert 'LINKAGE_EXEMPT' not in [f['code'] for f in result['info']]

def test_unknown_buyer_preserves_unknown_linkage_exception(offer_db):
    result=asyncio.run(call('check_offer',offer_db|{'supplier_size':company(),
        'buyer_size':company(average_revenue_eok=120,independence_met=None)}))
    assert result['company_size']['buyer']['is_small_business'] is None
    assert 'BUYER_SIZE_UNKNOWN' in [f['code'] for f in result['warnings']]
    assert 'LINKAGE_TERMS_MISSING' not in [f['code'] for f in result['violations']]
    assert 'LINKAGE_TERMS_MISSING' in [f['code'] for f in result['warnings']]   # 지우지 않고 경고로 (#234)
    assert 'LINKAGE_EXEMPT' not in [f['code'] for f in result['info']]

def test_known_non_small_buyer_still_flags_missing_terms(offer_db):
    result=asyncio.run(call('check_offer',offer_db|{'supplier_size':company(),
        'buyer_size':company(average_revenue_eok=120.01)}))
    assert result['company_size']['buyer']['is_small_business'] is False
    assert 'LINKAGE_TERMS_MISSING' in [f['code'] for f in result['violations']]
