"""고친 제안서의 인사말 숫자 다시 검사 (#293): 지급 60일로 쓴 인사말을 45일로 고친 문서에 그대로 두지 않는다."""

import asyncio
import base64
from uuid import uuid4

from negopilot_agent.offer_proposal.writer import NegotiationWriter, cover_problems, refresh_cover
from tests.test_offer_api import post
from tests.test_offer_documents import FakeBackend, mail_mcp_content_hash, offer_and_doc
from tests.test_offer_proposal import FakeCaller, bench

STALE = 'RFQ-2026-001 브래킷 건으로 단가 3,895원, 지급 조건은 목적물 수령일부터 60일 이내 현금 지급으로 제안드립니다.'
FRESH = 'RFQ-2026-001 브래킷 건으로 단가 3,895원, 목적물 수령일부터 45일 이내 현금 지급 조건을 제안드립니다.'


def edited(**over):
    """지급 60일로 만든 제안서를 45일로 고친 상태. 인사말은 처음 문장(60일) 그대로."""
    _, doc = offer_and_doc(supplier__payment_days=60)
    return doc.model_copy(update={'payment_days': 45, 'cover_letter': STALE, **over})


def test_stale_number_is_detected_after_edit():
    doc = edited()
    assert cover_problems(STALE, doc) == ['cover_letter: 제안 조건에 없는 숫자 60']
    assert cover_problems(FRESH, doc) == []


def test_matching_cover_is_kept_as_written():
    doc = edited(cover_letter=FRESH)
    new, info = asyncio.run(refresh_cover(doc, FakeBackend([])))
    assert new.cover_letter == FRESH and (info.regenerated, info.source) == (False, 'kept')


def test_stale_cover_is_rewritten_by_guarded_model():
    backend = FakeBackend([{'cover_letter': FRESH}])
    new, info = asyncio.run(refresh_cover(edited(), backend))
    assert new.cover_letter == FRESH and (info.regenerated, info.source) == (True, 'model')
    assert info.problems == ['cover_letter: 제안 조건에 없는 숫자 60']
    assert '"payment_terms": "목적물 수령일부터 45일 이내 현금 지급"' in backend.prompts[0]


def test_model_that_repeats_the_old_number_falls_back_to_code_cover():
    backend = FakeBackend([{'cover_letter': STALE}, {'cover_letter': STALE}])
    new, info = asyncio.run(refresh_cover(edited(), backend))
    assert info.source == 'fallback' and cover_problems(new.cover_letter, new) == []
    assert '60' not in new.cover_letter and '이전 출력이 검사에 걸렸다' in backend.prompts[1]


def test_without_model_falls_back_and_numbers_match_table():
    new, info = asyncio.run(refresh_cover(edited(), None))
    assert info.source == 'fallback' and new.cover_letter.startswith('귀사의 무궁한 발전을 기원합니다')


def test_internal_terms_typed_into_cover_are_removed():
    doc = edited(cover_letter='최대 수용가까지 협의 가능합니다.')
    new, info = asyncio.run(refresh_cover(doc, None))
    assert info.regenerated and '수용가' not in new.cover_letter


async def test_render_rewrites_stale_cover_and_hash_covers_the_new_text():
    caller = FakeCaller(bench(market__median=4300.0))
    doc = (await post(caller, {'request_id': str(uuid4()), 'offer': {
        'supplier': 'SUP-0007', 'rfq_id': 'RFQ-2026-001', 'as_of': '2026-10-23'}}))
    original = doc.json()['offer_document']
    edited_doc = original | {'payment_days': original['payment_days'] - 15, 'version': 2,
                             'cover_letter': f'지급 조건은 목적물 수령일부터 {original["payment_days"]}일 이내입니다.'}
    writer = NegotiationWriter(FakeBackend([]), cover_backend=FakeBackend([{'cover_letter': '아래 조건으로 제안드립니다.'}]))
    response = await post(caller, {'request_id': str(uuid4()), 'document': edited_doc},
                          path='/internal/offer-proposals/render', writer=writer, params={'include_cover_status': 'true'})
    data = response.json()
    assert response.status_code == 200 and response.headers['x-cover-letter'] == 'model'
    assert data['cover_letter'] == {'regenerated': True, 'source': 'model',
                                    'problems': [f'cover_letter: 제안 조건에 없는 숫자 {original["payment_days"]}']}
    assert data['document']['cover_letter'] == '아래 조건으로 제안드립니다.'
    files = [(a['filename'], a['content_type'], base64.b64decode(a['data_base64'])) for a in data['attachments']]
    assert data['document']['content_hash'] == mail_mcp_content_hash(data['document'], files)


async def test_render_keeps_cover_that_still_matches():
    caller = FakeCaller(bench(market__median=4300.0))
    original = (await post(caller, {'request_id': str(uuid4()), 'offer': {
        'supplier': 'SUP-0007', 'rfq_id': 'RFQ-2026-001', 'as_of': '2026-10-23'}})).json()['offer_document']
    response = await post(caller, {'request_id': str(uuid4()), 'document': original | {'version': 2}},
                          path='/internal/offer-proposals/render')
    # 기본 응답의 최상위 키는 Spring OfferResponseValidator가 기대하는 4개 그대로다
    assert set(response.json()) == {'request_id', 'document', 'attachments', 'check'}
    assert response.headers['x-cover-letter'] == 'kept'
    assert response.json()['document']['cover_letter'] == original['cover_letter']


async def test_render_file_download_reports_cover_status_in_header():
    caller = FakeCaller(bench(market__median=4300.0))
    original = (await post(caller, {'request_id': str(uuid4()), 'offer': {
        'supplier': 'SUP-0007', 'rfq_id': 'RFQ-2026-001', 'as_of': '2026-10-23'}})).json()['offer_document']
    stale = original | {'payment_days': 30, 'cover_letter': '지급 99일 조건입니다.'}
    response = await post(caller, {'request_id': str(uuid4()), 'document': stale},
                          path='/internal/offer-proposals/render', params={'format': 'pdf'})
    assert response.status_code == 200 and response.headers['x-cover-letter'] == 'fallback'
