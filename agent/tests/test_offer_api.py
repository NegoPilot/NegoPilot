"""POST /internal/offer-proposals: 인증, 입력 검증, JSON·PDF 응답, 입력 오류 코드 (#231)."""
import asyncio
from io import BytesIO
from uuid import uuid4

import httpx2 as httpx
from fastmcp.exceptions import ToolError
from pypdf import PdfReader

from negopilot_agent.api.app import create_app
from negopilot_agent.api.settings import ApiSettings
from negopilot_agent.offer_proposal.pdf import sections

from negopilot_agent.offer_proposal import OfferProposer
from tests.test_offer_proposal import FakeCaller, bench, request

KEY = {'X-Internal-Api-Key': 'test-offer-key'}


def body(**offer):
    return {'request_id': str(uuid4()),
            'offer': {'supplier': 'SUP-0007', 'rfq_id': 'RFQ-2026-001', 'as_of': '2026-10-23', **offer}}


async def post(caller, payload, params=None, headers=KEY):
    app = create_app(ApiSettings(internal_api_key='test-offer-key'), generation_caller=caller)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as c:
        return await c.post('/internal/offer-proposals', json=payload, params=params, headers=headers)


async def test_authentication_and_validation_before_execution():
    caller = FakeCaller(bench())
    assert (await post(caller, body(), headers={})).status_code == 401
    response = await post(caller, body(payment_days=0))
    assert response.status_code == 400 and response.json()['error']['code'] == 'VALIDATION_ERROR'
    assert (await post(caller, body(), params={'format': 'xml'})).status_code == 400
    assert caller.calls == []


async def test_json_response_has_one_offer():
    payload = body(payment_days=90, notes='월 2회 분할 납품')
    response = await post(FakeCaller(bench(market__median=4300.0)), payload)
    assert response.status_code == 200
    data = response.json()
    assert data['request_id'] == payload['request_id']
    offer = data['proposal']['offer']
    assert data['proposal']['status'] == 'offer' and offer['payment_days'] == 60
    assert offer['adjustments'][0] == '요청 조건 지급 90일 → 법정 60일 이내로 조정'
    assert [p['name'] for p in offer['price_range']] == ['opening', 'settlement', 'walk_away']


async def test_pdf_response_is_an_attachment_with_korean_text():
    response = await post(FakeCaller(bench(market__median=4300.0)), body(), params={'format': 'pdf'})
    assert response.status_code == 200
    assert response.headers['content-type'] == 'application/pdf'
    assert response.headers['content-disposition'] == 'attachment; filename="offer-RFQ-2026-001-SUP-0007.pdf"'
    reader = PdfReader(BytesIO(response.content))
    assert len(reader.pages) >= 1
    assert reader.metadata.title == '협상안 RFQ-2026-001 SUP-0007'


async def test_tool_input_errors_are_reported_with_their_code():
    class Missing(FakeCaller):
        async def __call__(self, server, tool, args):
            raise ToolError('[SUPPLIER_NOT_FOUND] 활성 협력사가 없다')
    response = await post(Missing(bench()), body())
    assert response.status_code == 422 and response.json()['error']['code'] == 'SUPPLIER_NOT_FOUND'

    class Broken(FakeCaller):
        async def __call__(self, server, tool, args):
            raise RuntimeError('db password=secret')
    response = await post(Broken(bench()), body())
    assert response.status_code == 500 and 'secret' not in response.text


def test_pdf_sections_use_structured_numbers():
    proposal = asyncio.run(OfferProposer(FakeCaller(bench(market__median=4300.0))).propose(request(notes='울산 공장')))
    doc = dict(sections(proposal))
    conditions = dict(doc['제시 조건'][0][1])
    assert conditions['단가'] == '3,895원' and conditions['예상 계약금액'] == f'{120_000 * 3895:,}원'
    assert conditions['납품·비고'] == '울산 공장'
    rows = doc['협상 범위'][0][1]
    assert [r[1] for r in rows[1:]] == ['3,895원', '3,998원', '4,100원']
    assert doc['고지'] == [('p', '이 문서는 초안이며 최종 결정과 발송은 담당자가 한다. 법률 최종 판단은 법무 확인을 거친다.')]
