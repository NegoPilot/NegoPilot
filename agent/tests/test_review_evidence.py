"""Review handoff tests independent of negotiation generation."""
import time
from types import SimpleNamespace
from uuid import uuid4
import httpx2 as httpx
import pytest
from negopilot_agent.api.app import create_app
from negopilot_agent.api.settings import ApiSettings
from negopilot_agent.api.review_api import ReviewOffer, ReviewDecomposer
from negopilot_agent.api.review_caller import ReviewedToolCaller
from negopilot_agent.api.review_snapshot import sign_review, verify_review
from tests.test_review_api import offer
from tests.test_legal_assessment import complete_facts

KEY = 'review-test-key'

def test_review_signature_hash_and_expiry(monkeypatch):
    data=offer(transaction=complete_facts())
    review=sign_review(data, {'status':'ready'}, [], KEY)
    assert verify_review(review['review_token'],KEY)['offer']==data
    for token,key in [(review['review_token']+'x',KEY),(review['review_token'],'wrong')]:
        with pytest.raises(ValueError): verify_review(token,key)
    monkeypatch.setattr(time,'time',lambda:review['expires_at'])
    with pytest.raises(ValueError): verify_review(review['review_token'],KEY)

async def test_transaction_findings_feed_answer_evidence_without_proposal():
    async def caller(server,tool,args):
        return {'violations':[], 'warnings':[], 'info':[]}
    data=offer(transaction=complete_facts(linkage_details=[]))
    result=await ReviewedToolCaller(ReviewOffer(**data),caller)('business-mcp','check_offer',data)
    assert result['verdict_code']=='CHECK_NEEDED'
    assert any(f['code']=='TRANSACTION_LINKAGE_AGREEMENT' for f in result['warnings'])

async def test_search_contract_keeps_five_queries():
    result=await ReviewDecomposer(ReviewOffer(**offer(transaction=complete_facts()))).decompose('검토')
    assert len(result.sub_queries)==5
    assert '제11조' in result.sub_queries[-1].question

@pytest.mark.parametrize('status,outcome,expected', [('COMPLETED','ok','ready'),('COMPLETED','failed','needs_information'),('FAILED','failed',None)])
async def test_api_snapshot_preserves_facts_and_partial_retrieval(monkeypatch,status,outcome,expected):
    import negopilot_agent.api.review_api as module
    async def execute(self,request):
        content={'execution_status':status,'routing':{'outcomes':[{'status':outcome}]},'answer':{'evidence':[],'notices':[]}}
        return SimpleNamespace(execution_status=status,model_dump=lambda **kw:content)
    monkeypatch.setattr(module.GenerationExecutor,'execute',execute)
    app=create_app(ApiSettings(internal_api_key=KEY))
    body=offer(transaction=complete_facts());body['price_linkage']=True
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        response=await client.post('/internal/legal-reviews',json={'request_id':str(uuid4()),'query':'검토','offer':body},headers={'X-Internal-Api-Key':KEY})
    assert response.status_code==200
    if expected is None:
        assert 'legal_review' not in response.json()
    else:
        review=response.json()['legal_review']
        assert review['assessment']['status']==expected
        snapshot=verify_review(review['review_token'],KEY)
        assert snapshot['offer']['transaction']['actual_contract_amount']==200000000
        assert len(snapshot['assessment']['findings'])==5


MCP_RESULT = {
    'verdict': '위반 소지 있음', 'verdict_code': 'VIOLATION',
    'violations': [
        {'code': 'PAYMENT_DAYS_OVER_60', 'severity': 'violation', 'message': '지급 기한 120일: 60일 이내로 정해야 한다',
         'law_refs': [{'law': 'subcontract_act', 'article_no': '제13조', 'paragraph': '①'}]},
        {'code': 'LINKAGE_TERMS_MISSING', 'severity': 'violation', 'message': '원재료 비중 40%로 주요 원재료인데 연동 약정이 없다',
         'law_refs': [{'law': 'subcontract_act', 'article_no': '제3조', 'paragraph': '②'}]}],
    'warnings': [{'code': 'LINKAGE_WAIVED_NEEDS_WRITTEN_REASON', 'severity': 'warning', 'message': '미연동 합의 서면', 'law_refs': []}],
    'info': [{'code': 'ENERGY_LINKAGE_EXEMPT', 'severity': 'info', 'message': '에너지 연동 예외', 'law_refs': []}],
}


async def reviewed(transaction, **changes):
    async def caller(server, tool, args):
        return {k: (list(v) if isinstance(v, list) else v) for k, v in MCP_RESULT.items()}
    data = offer(transaction=transaction) | {'payment_days': 120, **changes}
    return await ReviewedToolCaller(ReviewOffer(**data), caller)('business-mcp', 'check_offer', data)


def codes(result, bucket):
    return [f['code'] for f in result[bucket]]


@pytest.mark.parametrize('transaction', [{}, complete_facts(linkage_details=[])])
async def test_mcp_findings_survive_transaction_review(transaction):
    """거래 사실을 비워도, 다 넣어도 MCP의 위반 판정과 예외 안내는 남는다 (#257)."""
    result = await reviewed(transaction)
    assert {'PAYMENT_DAYS_OVER_60', 'LINKAGE_TERMS_MISSING'} <= set(codes(result, 'violations'))
    assert 'LINKAGE_WAIVED_NEEDS_WRITTEN_REASON' in codes(result, 'warnings')
    assert 'ENERGY_LINKAGE_EXEMPT' in codes(result, 'info')
    assert result['verdict'] == '위반 소지 있음'
    added = any(c.startswith('TRANSACTION_') for b in ('violations', 'warnings', 'info') for c in codes(result, b))
    assert added == bool(transaction)       # 아무것도 입력하지 않으면 check_offer 결과를 그대로 둔다


async def test_empty_transaction_keeps_ok_verdict():
    """빈 거래 사실이 '문제 없음'을 '확인 필요'로 바꾸지 않는다. #253 이전 결과와 같다."""
    async def caller(server, tool, args):
        return {'verdict': '문제 없음', 'verdict_code': 'OK', 'violations': [], 'warnings': [], 'info': []}
    data = offer(transaction={})
    result = await ReviewedToolCaller(ReviewOffer(**data), caller)('business-mcp', 'check_offer', data)
    assert result == {'verdict': '문제 없음', 'verdict_code': 'OK', 'violations': [], 'warnings': [], 'info': []}


async def test_same_issue_violation_is_not_counted_twice():
    """MCP가 이미 120일을 위반으로 냈으면 거래 사실의 지급 판단은 설명(info)으로만 붙는다."""
    result = await reviewed(complete_facts())
    assert codes(result, 'violations').count('PAYMENT_DAYS_OVER_60') == 1
    assert 'TRANSACTION_PAYMENT_DEADLINE' not in codes(result, 'violations')
    assert 'TRANSACTION_PAYMENT_DEADLINE' in codes(result, 'info')


async def test_stricter_transaction_deadline_adds_a_violation():
    """MCP는 45일을 통과시켜도, 기성금을 받은 날부터 15일이 먼저 지나면 거래 사실 판단이 위반을 더한다."""
    async def caller(server, tool, args):
        return {'verdict': '문제 없음', 'verdict_code': 'OK', 'violations': [], 'warnings': [], 'info': []}
    facts = complete_facts(receipt_date='2026-10-01', upstream_payment_applicable=True, upstream_received_on='2026-10-02')
    data = offer(transaction=facts) | {'payment_days': 45, 'price_linkage': True}
    result = await ReviewedToolCaller(ReviewOffer(**data), caller)('business-mcp', 'check_offer', data)
    assert 'TRANSACTION_PAYMENT_DEADLINE' in codes(result, 'violations') and result['verdict'] == '위반 소지 있음'


# 화면(TransactionFactsForm.vue)이 아무것도 입력하지 않았을 때 보내는 값
EMPTY_FORM = {
    "transaction_type": "unknown", "party_requirements_confirmed": None, "price_scope": "unknown", "contracted_on": None,
    "renewed_on": None, "actual_contract_amount": None, "committed_quantity": None, "previous_unit_price": None,
    "payment_basis": "unknown", "receipt_date": None, "completion_date": None, "monthly_agreed_date": None,
    "agreed_due_date": None, "upstream_received_on": None, "upstream_payment_applicable": None,
    "payment_exception_claimed": False, "payment_exception_reason": None, "payment_exception_evidence": [],
    "reduction_reason": None, "reduction_evidence": [], "waiver_reason": None, "waiver_evidence": [],
    "evidence_references": [], "linkage_details": [],
}


async def test_empty_web_form_counts_as_not_entered():
    """폼은 '지급기한 예외 주장'을 기본으로 '주장하지 않음'(false)으로 보낸다. 그것만으로 입력한 것으로 보지 않는다."""
    async def caller(server, tool, args):
        return {'verdict': '문제 없음', 'verdict_code': 'OK', 'violations': [], 'warnings': [], 'info': []}
    data = offer(transaction=EMPTY_FORM)
    result = await ReviewedToolCaller(ReviewOffer(**data), caller)('business-mcp', 'check_offer', data)
    assert result['verdict'] == '문제 없음' and result['warnings'] == []
    entered_one = offer(transaction=EMPTY_FORM | {"payment_basis": "receipt"})
    result = await ReviewedToolCaller(ReviewOffer(**entered_one), caller)('business-mcp', 'check_offer', entered_one)
    assert any(f['code'].startswith('TRANSACTION_') for f in result['warnings'] + result['info'])
