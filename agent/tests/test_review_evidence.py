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
