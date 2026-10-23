"""저장본 브리핑 렌더의 버전 표시와 결정성 및 기존 PDF 호환을 검사한다."""

import hashlib
import io
from datetime import date
from urllib.parse import unquote
from uuid import uuid4

import httpx2 as httpx
import pytest
from pypdf import PdfReader

from negopilot_agent.api.app import create_app
from negopilot_agent.api.briefing_api import StoredBriefing
from negopilot_agent.api.settings import ApiSettings
from negopilot_agent.offer_proposal.pdf import BriefingState, render_briefing_pdf, sections
from negopilot_agent.offer_proposal.writer import Writing
from tests.test_offer_documents import good_draft
from tests.test_offer_proposal import FakeCaller, bench, propose

KEY = {'X-Internal-Api-Key': 'test-briefing-key'}
DAY = date(2026, 10, 23)
# 변경 전 main의 sections와 draw를 같은 입력 및 fixed_date로 실행한 SHA-256.
LEGACY_SHA256 = 'fefa9f1f7f617dda51ae592aabb5bfe5f39922fb0e36c13249cf960a7c9b6fe1'


@pytest.fixture
def briefing():
    proposal = propose(FakeCaller(bench(market__median=4300.0)))
    writing = Writing(source='model', attempts=1, first_try_passed=True, **good_draft())
    return StoredBriefing.model_validate(proposal.model_dump() | {'writing': writing.model_dump()})


def state(**over):
    return BriefingState(current_revision=3, **({'validation_status': 'recheck_required',
                          'checked_revision': 1, 'derived_stale': True,
                          'user_edited_paths': ['/offer/payment_days', '/writing/strategy'],
                          'limitations': ['양보 순서의 모든 조건 조합을 검사하지는 않았습니다']} | over))


def pdf_text(raw):
    return '\n'.join(page.extract_text() for page in PdfReader(io.BytesIO(raw)).pages)


def render(briefing, current_state=None):
    return render_briefing_pdf(briefing, briefing.writing, state=current_state, fixed_date=DAY)


def test_state_omitted_has_identical_legacy_bytes(briefing):
    raw = render(briefing)
    assert hashlib.sha256(raw).hexdigest() == LEGACY_SHA256
    assert raw == render_briefing_pdf(briefing, briefing.writing, state=None, fixed_date=DAY)


def test_recheck_hides_old_verdict_and_passed_writing(briefing):
    raw = render(briefing, state())
    text = pdf_text(raw)
    assert '버전 r3' in text and '검사 상태: 재검사 필요' in text
    assert '현재 버전은 검사하지 않았습니다(마지막 검사: r1)' in text
    assert '위반 소지 0건' not in text and '대조해 통과' not in text
    assert '사용자 수정 문구 (검사하지 않음)' in text
    assert '검사 범위의 한계' in text
    assert text.index('검사 범위의 한계') < text.index('고지')
    assert all('대외비 — 협력사 발송 금지' in page.extract_text()
               for page in PdfReader(io.BytesIO(raw)).pages)


@pytest.mark.parametrize('status', ['not_checked', 'checking', 'failed', 'recheck_required', 'checked'])
def test_older_result_is_not_presented_as_current(status, briefing):
    text = pdf_text(render(briefing, state(validation_status=status, user_edited_paths=[])))
    assert 'check_offer 판정' not in text
    assert '대조해 통과' not in text
    assert '현재 버전은 검사하지 않았습니다' in text


def test_never_checked_shows_no_previous_revision(briefing):
    text = pdf_text(render(briefing, state(validation_status='not_checked', checked_revision=None)))
    assert '마지막 검사: 없음' in text


def test_checked_current_shows_verdict_without_inventing_violation_count(briefing):
    text = pdf_text(render(briefing, state(validation_status='checked', checked_revision=3,
                                         derived_stale=False, user_edited_paths=[])))
    assert 'check_offer 판정: 문제 없음 (OK)' in text
    assert '위반 소지 0건' not in text
    assert '대조해 통과' in text


@pytest.mark.parametrize('violations', [[], [{'code': 'PAYMENT_DAYS_OVER_60', 'message': '지급 초과'}]])
def test_checked_counts_only_actual_violations(violations, briefing):
    briefing.offer.violations = violations
    briefing.offer.verdict_code = 'VIOLATION' if violations else 'OK'
    text = pdf_text(render(briefing, state(validation_status='checked', checked_revision=3)))
    assert f'위반 소지 {len(violations)}건' in text
    if violations:
        assert '위반 소지 0건' not in text


@pytest.mark.parametrize(('conclusion', 'label', 'old_verdict'), [
    ('no_risk_identified_in_scope', '검사 범위에서 위험 미발견', 'VIOLATION'),
    ('conditional', '조건부', 'OK'),
    ('risk_identified', '위험 발견', 'OK'),
])
@pytest.mark.parametrize('inherited_from', [None, 1])
def test_current_conclusion_overrides_stored_verdict(conclusion, label, old_verdict, inherited_from, briefing):
    briefing.offer.verdict_code = old_verdict
    current = state(validation_status='checked', checked_revision=1 if inherited_from else 3,
                    inherited_from=inherited_from, conclusion=conclusion)
    text = pdf_text(render(briefing, current))
    assert f'현재 검사 결론: {label}' in text
    assert 'check_offer 판정' not in text


@pytest.mark.parametrize('conclusion', [None, 'risk_identified'])
def test_risk_result_never_claims_zero_violations(conclusion, briefing):
    briefing.offer.violations = []
    briefing.offer.verdict_code = 'VIOLATION' if conclusion is None else 'OK'
    current = state(validation_status='checked', checked_revision=3, conclusion=conclusion)
    assert '위반 소지 0건' not in pdf_text(render(briefing, current))


def test_stale_conclusion_is_not_used(briefing):
    text = pdf_text(render(briefing, state(conclusion='risk_identified')))
    assert '현재 검사 결론' not in text
    assert '현재 버전은 검사하지 않았습니다' in text


def test_inherited_check_keeps_verdict_but_marks_edited_section(briefing):
    current = state(validation_status='checked', inherited_from=1, derived_stale=False)
    blocks = dict(sections(briefing, briefing.writing, current))
    assert blocks['협상 전략'][0] == ('label', '사용자 수정 문구 (검사하지 않음)')
    assert '대조해 통과' in blocks['제안 멘트(초안)'][0][1]
    assert 'check_offer 판정' in blocks['법규 검토 결과'][0][1]


@pytest.mark.parametrize(('path', 'section'), [
    ('/writing/strategy', '협상 전략'),
    ('/writing/opening_message', '제안 멘트(초안)'),
    ('/writing/objection_scripts/0', '예상 쟁점과 대응'),
    ('/offer/objections/0/response', '예상 쟁점과 대응'),
])
def test_edited_section_gets_unchecked_label(path, section, briefing):
    current = state(validation_status='checked', checked_revision=3, user_edited_paths=[path])
    blocks = dict(sections(briefing, briefing.writing, current))
    assert blocks[section][0] == ('label', '사용자 수정 문구 (검사하지 않음)')


def test_edited_objection_uses_saved_response(briefing):
    briefing.offer.objections[0].response = '담당자가 수정한 실제 대응 문구'
    current = state(user_edited_paths=['/offer/objections/0/response'])
    text = pdf_text(render(briefing, current))
    assert '담당자가 수정한 실제 대응 문구' in text
    assert good_draft()['objection_scripts'][0] not in text


@pytest.mark.parametrize('paths', [[], ['/writing/objection_scripts'],
                                  ['/offer/objections', '/writing/objection_scripts']])
def test_shorter_saved_scripts_do_not_drop_objections(paths, briefing):
    for index, objection in enumerate(briefing.offer.objections, 1):
        objection.issue = f'쟁점{index}'
        objection.response = f'저장대응{index}'
    briefing.writing.objection_scripts = ['수정대응1']
    current = state(user_edited_paths=paths)
    rows = dict(sections(briefing, briefing.writing, current))['예상 쟁점과 대응'][-1][1]
    assert rows == [['쟁점', '대응 멘트'], ['쟁점1', '수정대응1'],
                    ['쟁점2', '저장대응2'], ['쟁점3', '저장대응3']]
    text = pdf_text(render(briefing, current))
    assert all(value in text for row in rows[1:] for value in row)


def test_stale_price_range_hides_percentages_and_old_checks(briefing):
    current = state()
    rows = dict(sections(briefing, briefing.writing, current))['협상 범위'][0][1]
    for row in rows[1:]:
        assert row[2:] == ['갱신 대기'] * 4
    assert '갱신 대기' in pdf_text(render(briefing, current))


def test_no_offer_keeps_legal_review_section_absent(briefing):
    briefing.offer = None
    briefing.writing = None
    briefing.status = 'no_offer'
    blocks = dict(sections(briefing, None, state()))
    assert '법규 검토 결과' not in blocks


class NoCalls:
    async def __call__(self, *args):
        raise AssertionError('저장본 렌더는 MCP를 호출하지 않는다')


async def post(payload, headers=KEY):
    app = create_app(ApiSettings(internal_api_key='test-briefing-key'), generation_caller=NoCalls(),
                     offer_writer_factory=lambda: pytest.fail('저장본 렌더는 모델을 호출하지 않는다'))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        return await client.post('/internal/offer-proposals/briefing/render', json=payload, headers=headers)


async def test_api_uses_saved_briefing_and_deterministic_revision_headers(briefing):
    briefing.writing.strategy = '담당자가 저장한 수정 전략'
    payload = {'request_id': str(uuid4()), 'briefing': briefing.model_dump(mode='json'),
               'state': state().model_dump(mode='json')}
    first = await post(payload)
    second = await post(payload | {'request_id': str(uuid4())})
    assert first.status_code == second.status_code == 200
    assert first.headers['content-type'] == 'application/pdf'
    assert first.content == second.content
    assert '담당자가 저장한 수정 전략' in pdf_text(first.content)
    assert first.headers['x-file-sha256'] == hashlib.sha256(first.content).hexdigest()
    assert '내부브리핑_RFQ-2026-001_SUP-0007_r3.pdf' in unquote(first.headers['content-disposition'])
    assert PdfReader(io.BytesIO(first.content)).metadata.creation_date.date() == DAY


async def test_api_without_state_preserves_fixed_date_legacy_output(briefing):
    result = await post({'request_id': str(uuid4()), 'briefing': briefing.model_dump(mode='json')})
    assert result.status_code == 200
    assert hashlib.sha256(result.content).hexdigest() == LEGACY_SHA256


async def test_api_accepts_explicit_issue_date(briefing):
    result = await post({'request_id': str(uuid4()), 'briefing': briefing.model_dump(mode='json'),
                         'issued_on': '2026-10-24', 'state': state().model_dump(mode='json')})
    assert result.status_code == 200
    assert PdfReader(io.BytesIO(result.content)).metadata.creation_date.date() == date(2026, 10, 24)


async def test_api_requires_authentication(briefing):
    result = await post({'request_id': str(uuid4()), 'briefing': briefing.model_dump(mode='json')}, headers={})
    assert result.status_code == 401 and result.json()['error']['code'] == 'UNAUTHORIZED'


@pytest.mark.parametrize('invalid', [
    {'unexpected': True},
    {'state': {'current_revision': 0, 'validation_status': 'checked'}},
    {'state': {'current_revision': 1, 'validation_status': 'invalid'}},
    {'state': {'current_revision': 1, 'validation_status': 'checked', 'unexpected': True}},
    {'issued_on': 'not-a-date'},
])
async def test_api_rejects_invalid_inputs(invalid, briefing):
    result = await post({'request_id': str(uuid4()), 'briefing': briefing.model_dump(mode='json')} | invalid)
    assert result.status_code == 400 and result.json()['error']['code'] == 'VALIDATION_ERROR'


async def test_api_rejects_invalid_stored_date(briefing):
    result = await post({'request_id': str(uuid4()),
                         'briefing': briefing.model_dump(mode='json') | {'as_of': 'not-a-date'}})
    assert result.status_code == 400
