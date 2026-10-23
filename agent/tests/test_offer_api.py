"""POST /internal/offer-proposals(·/render·/batch): 인증, 입력 검증, JSON·PDF·DOCX 응답, 재검사, 일괄 처리 (#231, #236)."""
import csv
import hashlib
import io
import json
import zipfile
from uuid import uuid4

import httpx2 as httpx
from fastmcp.exceptions import ToolError
from pypdf import PdfReader

from negopilot_agent.api.app import create_app
from negopilot_agent.api.settings import ApiSettings
from negopilot_agent.offer_proposal.document import OfferDocument
from negopilot_agent.offer_proposal.supplier import docx_text
from negopilot_agent.offer_proposal.writer import NegotiationWriter
from tests.test_offer_documents import FakeBackend, good_draft
from tests.test_offer_proposal import FakeCaller, bench

KEY = {'X-Internal-Api-Key': 'test-offer-key'}


def body(**offer):
    return {'request_id': str(uuid4()),
            'offer': {'supplier': 'SUP-0007', 'rfq_id': 'RFQ-2026-001', 'as_of': '2026-10-23', **offer}}


def app_for(caller, writer=None):
    return create_app(ApiSettings(internal_api_key='test-offer-key'), generation_caller=caller,
                      offer_writer_factory=lambda: writer)


async def post(caller, payload, params=None, headers=KEY, path='/internal/offer-proposals', writer=None):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for(caller, writer)),
                                 base_url='http://test') as c:
        return await c.post(path, json=payload, params=params, headers=headers)


async def upload(caller, data: bytes, filename: str):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for(caller)), base_url='http://test') as c:
        return await c.post('/internal/offer-proposals/batch', content=data, headers=KEY, params={'filename': filename})


async def test_authentication_and_validation_before_execution():
    caller = FakeCaller(bench())
    assert (await post(caller, body(), headers={})).status_code == 401
    response = await post(caller, body(payment_days=0))
    assert response.status_code == 400 and response.json()['error']['code'] == 'VALIDATION_ERROR'
    assert (await post(caller, body(), params={'format': 'xml'})).status_code == 400
    assert caller.calls == []


async def test_json_response_has_document_briefing_and_check():
    payload = body(payment_days=90, notes='월 2회 분할 납품')
    response = await post(FakeCaller(bench(market__median=4300.0)), payload)
    assert response.status_code == 200
    data = response.json()
    assert data['request_id'] == payload['request_id']
    doc = OfferDocument.model_validate(data['offer_document'])
    assert doc.payment_days == 60 and doc.status == 'draft' and doc.delivery_notes == '월 2회 분할 납품'
    assert data['content_hash'] == doc.content_hash()
    assert data['check']['verdict_code'] == 'OK'
    briefing = data['internal_briefing']
    assert briefing['offer']['adjustments'][0] == '요청 조건 지급 90일 → 법정 60일 이내로 조정'
    assert [p['name'] for p in briefing['offer']['price_range']] == ['opening', 'settlement', 'walk_away']
    assert briefing['writing'] is None                       # 모델 문구를 끈 앱


async def test_model_writing_is_used_when_enabled():
    writer = NegotiationWriter(FakeBackend([good_draft(), good_draft()]))
    data = (await post(FakeCaller(bench(market__median=4300.0)), body(), writer=writer)).json()
    assert data['internal_briefing']['writing']['source'] == 'model'
    assert data['offer_document']['cover_letter'] == good_draft()['cover_letter']
    off = await post(FakeCaller(bench(market__median=4300.0)), body(), params={'writing': 'false'}, writer=writer)
    assert off.json()['internal_briefing']['writing'] is None


async def test_supplier_files_and_briefing_pdf():
    caller = FakeCaller(bench(market__median=4300.0))
    pdf = await post(caller, body(), params={'format': 'pdf'})
    assert pdf.status_code == 200 and pdf.headers['content-type'] == 'application/pdf'
    assert "filename*=UTF-8''%EC%A0%9C%EC%95%88%EC%84%9C_RFQ-2026-001_SUP-0007_v1.pdf" in pdf.headers['content-disposition']
    assert pdf.headers['x-content-hash'].startswith('sha256:')
    assert pdf.headers['x-file-sha256'] == hashlib.sha256(pdf.content).hexdigest()
    docx = await post(caller, body(), params={'format': 'docx'})
    assert docx.headers['content-type'].startswith('application/vnd.openxmlformats')
    assert '단가 및 거래조건 제안서' in docx_text(docx.content)
    briefing = await post(caller, body(), params={'format': 'briefing_pdf'})
    pages = PdfReader(io.BytesIO(briefing.content)).pages
    assert all('대외비 — 협력사 발송 금지' in p.extract_text() for p in pages)


async def test_render_rechecks_and_rejects_violations():
    caller = FakeCaller(bench(market__median=4300.0))
    doc = (await post(caller, body())).json()['offer_document']
    edited = doc | {'unit_price': 4000, 'version': 2, 'status': 'final'}
    ok = await post(caller, {'request_id': str(uuid4()), 'document': edited}, path='/internal/offer-proposals/render')
    assert ok.status_code == 200
    data = ok.json()
    assert data['document']['unit_price'] == 4000 and data['document']['version'] == 2
    assert data['content_hash'] == OfferDocument.model_validate(data['document']).content_hash()
    bad = doc | {'payment_days': 90}
    rejected = await post(caller, {'request_id': str(uuid4()), 'document': bad},
                          path='/internal/offer-proposals/render', params={'format': 'pdf'})
    assert rejected.status_code == 422
    assert rejected.json()['error']['code'] == 'OFFER_VIOLATION'
    assert rejected.json()['findings'][0]['code'] == 'PAYMENT_DAYS_OVER_60'


async def test_render_files_carry_the_document_hash():
    caller = FakeCaller(bench(market__median=4300.0))
    doc = (await post(caller, body())).json()['offer_document'] | {'status': 'final'}
    response = await post(caller, {'request_id': str(uuid4()), 'document': doc},
                          path='/internal/offer-proposals/render', params={'format': 'docx'})
    assert response.headers['x-content-hash'] == OfferDocument.model_validate(doc).content_hash()
    assert response.headers['x-document-version'] == '1'


async def test_batch_returns_zip_with_files_and_summary():
    data = ('협력사,RFQ,기준일,지급일수,연동,비고\n'
            'SUP-0007,RFQ-2026-001,2026-10-23,90,아니오,월 2회\n'
            'SUP-07,RFQ-2026-001,2026-10-23,60,,\n').encode('utf-8')
    response = await upload(FakeCaller(bench()), data, 'offers.csv')
    assert response.status_code == 200 and response.headers['content-type'] == 'application/zip'
    z = zipfile.ZipFile(io.BytesIO(response.content))
    assert {'002_제안서_RFQ-2026-001_SUP-0007_v1.pdf', '002_제안서_RFQ-2026-001_SUP-0007_v1.docx',
            '002_내부브리핑_RFQ-2026-001_SUP-0007.pdf', 'summary.csv'} <= set(z.namelist())
    rows = list(csv.DictReader(io.StringIO(z.read('summary.csv').decode('utf-8-sig'))))
    assert [r['status'] for r in rows] == ['offer', 'invalid']
    assert 'supplier' in rows[1]['error']


async def test_batch_rejects_bad_uploads():
    assert (await upload(FakeCaller(bench()), b'', 'a.csv')).status_code == 400
    assert (await upload(FakeCaller(bench()), b'x', 'a.exe')).status_code == 400


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


async def test_no_offer_has_no_supplier_document():
    unfixable = {'verdict_code': 'VIOLATION', 'warnings': [], 'violations': [{'code': 'X', 'message': '위반'}]}
    caller = FakeCaller(bench(), lambda args: unfixable)
    data = (await post(caller, body())).json()
    assert data['offer_document'] is None and data['content_hash'] is None
    assert (await post(caller, body(), params={'format': 'pdf'})).json()['error']['code'] == 'NO_OFFER'
    assert json.dumps(data['internal_briefing']['no_offer_reasons'], ensure_ascii=False).count('위반') == 3
