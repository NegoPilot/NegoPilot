from uuid import uuid4
import pytest
import httpx2 as httpx
from pydantic import ValidationError
from negopilot_agent.api.app import create_app
from negopilot_agent.api.settings import ApiSettings
from negopilot_agent.api.review_api import ReviewOffer, ReviewDecomposer


def offer(**updates):
    return dict(supplier='SUP-0034', rfq_id='RFQ-2026-001', unit_price=3900,
        payment_days=60, price_linkage=False, raw_material_ratio=.4, contract_days=365,
        supplier_size={}, buyer_size={}, as_of='2026-10-23', **updates)


async def test_structured_inputs_keep_null_false_and_units():
    body=offer()
    body['buyer_size']={'average_revenue_eok':120.01, 'assets_eok':2000,
        'independence_met':None, 'has_related_enterprises':False}
    result=await ReviewDecomposer(ReviewOffer(**body)).decompose('검토')
    e=result.sub_queries[0].entities
    assert e.buyer_size.average_revenue_eok == 120.01
    assert e.buyer_size.independence_met is None
    assert e.buyer_size.has_related_enterprises is False
    assert e.price_linkage is False and e.raw_material_ratio == .4
    assert len(result.sub_queries)==4
    assert '별표 3' in result.sub_queries[2].question
    assert all(q.entities.as_of=='2026-10-23' for q in result.sub_queries)


@pytest.mark.parametrize('field,value', [('unit_price',0),('payment_days',-1),('price_linkage','false'),
    ('raw_material_ratio',40),('as_of','2026-02-30'),('supplier','unknown'),('extra',1)])
def test_rejects_invalid_form_values(field,value):
    body=offer();body[field]=value
    with pytest.raises(ValidationError): ReviewOffer(**body)


async def test_endpoint_authentication_and_validation_before_execution():
    app=create_app(ApiSettings(internal_api_key='test-review-key'))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as c:
        body={'request_id':str(uuid4()),'query':'검토','offer':offer()}
        response=await c.post('/internal/v2/legal-reviews',json=body)
        assert response.status_code==401
        body['offer']['unit_price']=0
        response=await c.post('/internal/v2/legal-reviews',json=body,headers={'X-Internal-Api-Key':'test-review-key'})
        assert response.status_code==400
        assert response.json()['error']['code']=='VALIDATION_ERROR'
