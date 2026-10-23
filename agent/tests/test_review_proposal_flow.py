from uuid import uuid4
import time
import httpx2 as httpx
import pytest
from negopilot_agent.api.app import create_app
from negopilot_agent.api.settings import ApiSettings
from negopilot_agent.api.review_snapshot import sign_review, verify_review
from negopilot_agent.api.review_api import ReviewOffer
from negopilot_agent.api.review_caller import ReviewedToolCaller
from negopilot_agent.legal_assessment import assess_transaction
from tests.test_offer_proposal import FakeCaller, bench

KEY='review-test-key'
def offer():
    return {'supplier':'SUP-0007','rfq_id':'RFQ-2026-001','as_of':'2026-10-23',
        'unit_price':3900,'payment_days':30,'price_linkage':True,'contract_days':365,
        'raw_material_ratio':.4,'energy_ratio':.05,'supplier_size':{},'buyer_size':{},
        'transaction':{'transaction_type':'manufacturing','party_requirements_confirmed':True,
            'price_scope':'new_order','contracted_on':'2026-10-01','payment_basis':'receipt',
            'upstream_payment_applicable':False,'evidence_references':['contract-A'],
            'actual_contract_amount':468000000,'committed_quantity':120000,
            'linkage_details':[{'category':'material','name':'steel','cost_ratio':.4,
                'indicator':'official index','formula':'base + cost difference','trigger_ratio':.03,
                'frequency':'monthly','reference_period':'2026-10','reflection_date':'next delivery'}]}}
def signed(data=None):
    data=data or offer()
    return sign_review(data,assess_transaction(data['transaction'],data,data['as_of']),[],KEY)
def test_signature_expiry_and_other_key(monkeypatch):
    review=signed()
    assert verify_review(review['review_token'],KEY)['offer']==offer()
    with pytest.raises(ValueError): verify_review(review['review_token']+'x',KEY)
    with pytest.raises(ValueError): verify_review(review['review_token'],'other')
    monkeypatch.setattr(time,'time',lambda:review['expires_at']+1)
    with pytest.raises(ValueError): verify_review(review['review_token'],KEY)
async def post(review,**extra):
    caller=FakeCaller(bench());app=create_app(ApiSettings(internal_api_key=KEY),generation_caller=caller)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as c:
        response=await c.post('/internal/offer-proposals/from-review?writing=false',json={
            'request_id':str(uuid4()),'review_token':review['review_token'],**extra},headers={'X-Internal-Api-Key':KEY})
    return response,caller
async def test_condition_trace_and_alternatives():
    review=signed();response,_=await post(review)
    assert response.status_code==200,response.text
    result=response.json()['internal_briefing']
    assert result['review_basis']['input_hash']==review['input_hash']
    assert result['status']=='offer'
    assert len(result['offer']['condition_basis'])==4
    for item in result['offer']['condition_basis']:
        assert item['review_id']==review['review_id'] and item['finding_ids'] and item['law_refs']
    assert all(a['assessment']['findings'] for a in result['offer']['assessed_alternatives'])
async def test_missing_linkage_and_forbidden_override():
    data=offer();data['transaction']['linkage_details']=[]
    response,_=await post(signed(data))
    assert response.status_code==200,response.text
    assert response.json()['internal_briefing']['draft_status']=='needs_information'
    response,_=await post(signed(),offer={'unit_price':1})
    assert response.status_code==400
async def test_bad_snapshot_does_not_call_tools():
    review=signed();review['review_token']+='x'
    response,caller=await post(review)
    assert response.status_code==409 and not caller.calls
async def test_upstream_deadline_blocks_candidates():
    data=offer();data['transaction'].update(receipt_date='2026-10-01',upstream_payment_applicable=True,upstream_received_on='2026-10-01')
    response,_=await post(signed(data))
    assert response.status_code==200,response.text
    assert response.json()['internal_briefing']['status']=='no_offer'
async def test_findings_reach_answer_pipeline():
    data=offer();data['transaction']['linkage_details']=[]
    caller=ReviewedToolCaller(ReviewOffer(**data),FakeCaller(bench()))
    checked=await caller('business-mcp','check_offer',data)
    assert checked['verdict_code']=='CHECK_NEEDED'
    assert any(f['code']=='TRANSACTION_LINKAGE_AGREEMENT' for f in checked['warnings'])

async def test_retrospective_reduction_is_not_recommended_without_justification():
    data=offer();data['transaction'].update(price_scope='existing_order',previous_unit_price=4100)
    response,_=await post(signed(data))
    assert response.status_code==200,response.text
    assert response.json()['internal_briefing']['status']=='no_offer'

async def test_partial_retrieval_cannot_claim_ready():
    data=offer();assessment=assess_transaction(data['transaction'],data,data['as_of'])
    assessment['sources_complete']=False
    review=sign_review(data,assessment,[],KEY)
    response,_=await post(review)
    assert response.json()['internal_briefing']['draft_status']=='needs_information'

async def test_invalid_target_range_is_400():
    response,_=await post(signed(),target_price=4000,max_price=100)
    assert response.status_code==400


async def test_review_price_does_not_replace_rfq_target_and_explicit_target_wins():
    response, _ = await post(signed())
    result = response.json()
    assert set(result) == {'request_id', 'offer_document', 'check', 'internal_briefing'}
    assert result['internal_briefing']['target_price'] == bench()['rfq']['target_price']
    assert result['internal_briefing']['target_price'] != offer()['unit_price']
    assert set(result['internal_briefing']['review_basis']) == {'review_id', 'version', 'input_hash'}
    response, _ = await post(signed(), target_price=3751)
    assert response.json()['internal_briefing']['target_price'] == 3751


async def test_sender_and_notes_reach_supplier_document():
    sender = {'name': '홍길동', 'department': '구매팀', 'job_title': '매니저', 'email': 'buyer@example.com'}
    response, _ = await post(signed(), sender=sender, notes='분할 납품 협의')
    document = response.json()['offer_document']
    assert document['sender']['contact_name'] == sender['name']
    assert all(document['sender'][key] == sender[key] for key in ('department', 'job_title', 'email'))
    assert document['delivery_notes'] == '분할 납품 협의'


async def test_internal_comparisons_never_reach_supplier_json_pdf_or_docx():
    import io
    import json
    from pypdf import PdfReader
    from negopilot_agent.offer_proposal.document import OfferDocument
    from negopilot_agent.offer_proposal import supplier

    data = offer()
    data['transaction'].update(committed_quantity=1237, previous_payment_days=60,
        previous_unit_price=4123, buyer_financing_rate=.073, supplier_financing_rate=.137,
        evidence_references=['INTERNAL-EVIDENCE-SECRET'])
    response, _ = await post(signed(data))
    assert response.status_code == 200, response.text
    result = response.json()
    internal = result['internal_briefing']
    document = OfferDocument.model_validate(result['offer_document'])
    texts = [json.dumps(result['offer_document'], ensure_ascii=False),
             ''.join(page.extract_text() for page in PdfReader(io.BytesIO(supplier.render_pdf(document))).pages),
             supplier.docx_text(supplier.render_docx(document))]
    forbidden = ['commercial_comparison', 'condition_basis', 'assessed_alternatives', 'review_basis',
                 'INTERNAL-EVIDENCE-SECRET', internal['review_basis']['input_hash'],
                 internal['review_basis']['review_id']]
    comparison = internal['offer']['commercial_comparison']['selected']
    for name in ('purchase_amount', 'baseline_amount', 'buyer_price_saving', 'buyer_financing_cost',
                 'supplier_financing_saving', 'buyer_net_saving', 'supplier_revenue_finance_change'):
        value = abs(comparison[name])
        assert value > 10000
        forbidden.extend((str(value), f'{value:,}'))
    for text in texts:
        assert not [value for value in forbidden if value in text]


async def test_from_review_requires_authentication():
    caller = FakeCaller(bench())
    app = create_app(ApiSettings(internal_api_key=KEY), generation_caller=caller)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/internal/offer-proposals/from-review', json={
            'request_id': str(uuid4()), 'review_token': signed()['review_token']})
    assert response.status_code == 401
    assert caller.calls == []

async def test_transaction_searches_fit_decomposition_contract():
    from negopilot_agent.api.review_api import ReviewDecomposer
    result=await ReviewDecomposer(ReviewOffer(**offer())).decompose('검토')
    assert len(result.sub_queries)==5
    assert '제11조' in result.sub_queries[-1].question
