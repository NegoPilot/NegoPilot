"""협력사 제안서·내부 브리핑 분리, 모델 협상 문구 숫자 검사, 원재료 비중 입력 필요, 해시, 엑셀 입력 (#236)."""

import asyncio
import io
import json

import pytest
from pypdf import PdfReader

from negopilot_agent.offer_proposal import excel, supplier
from negopilot_agent.offer_proposal.document import (NEEDS_INPUT_WORDING, BuyerSettings, OfferDocument,
                                                     build_document)
from negopilot_agent.offer_proposal.pdf import render_briefing_pdf, sections
from negopilot_agent.offer_proposal.service import build_package, package_files
from negopilot_agent.offer_proposal.writer import NegotiationWriter, check_draft, payloads
from tests.test_offer_proposal import FakeCaller, bench, propose, request

BUYER = BuyerSettings(company='SKALA 자동차', department='구매1팀', contact_name='홍길동', phone='02-000-0000',
                      email='buyer@example.com')


def good_draft(**over) -> dict:
    """bench(market__median=4300.0) 협상안(제시 3,895 / 합의 3,998 / 수용 4,100, 쟁점 3개)에 맞는 모델 출력."""
    out = {'strategy': '제시가 3,895원에서 시작한다. 필요하면 목표 합의가 3,998원까지 양보한다. 최대 수용가 4,100원을 넘기지 않는다.',
           'objection_scripts': ['직전 견적 대비 인하 폭은 5% 이내입니다.', '시장 중앙값 4,300원의 91% 수준입니다.',
                                 '원재료 비중 40%를 반영해 연동 약정을 넣겠습니다.'],
           'opening_message': 'RFQ-2026-001 브래킷 건으로 단가 3,895원, 지급 45일을 제안드립니다.',
           'cover_letter': '귀사의 발전을 기원합니다. RFQ-2026-001 브래킷 건의 단가 및 거래조건을 아래와 같이 제안드립니다.'}
    return out | over


class FakeBackend:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.prompts = []

    async def generate(self, system, user):
        self.prompts.append(user)
        out = self.outputs.pop(0)
        return out if out is None or isinstance(out, str) else json.dumps(out, ensure_ascii=False)


def offer_and_doc(**bench_over):
    proposal = propose(FakeCaller(bench(**({'market__median': 4300.0} | bench_over))))
    return proposal, build_document(proposal, BUYER)


def write(outputs, **bench_over):
    proposal, doc = offer_and_doc(**bench_over)
    backend = FakeBackend(outputs)
    return asyncio.run(NegotiationWriter(backend).write(proposal, doc)), backend


# ---------- 모델 문구 검사 ----------

def test_good_draft_passes_first_try():
    writing, backend = write([good_draft()])
    assert writing.source == 'model' and writing.first_try_passed and writing.attempts == 1
    assert len(backend.prompts) == 1
    sent = json.loads(backend.prompts[0])
    assert set(sent) >= {'supplier_terms', 'price_range', 'concessions', 'objections', 'law_refs'}


def test_bad_number_is_retried_with_feedback_then_accepted():
    bad = good_draft(strategy='제시가 3,700원에서 시작한다.')   # 3,800원(목표 단가)은 입력에 있다
    writing, backend = write([bad, good_draft()])
    assert writing.source == 'model' and writing.attempts == 2 and not writing.first_try_passed
    assert '입력에 없는 숫자 3,700' in backend.prompts[1]


def test_repeated_bad_number_falls_back_to_code_text():
    bad = good_draft(opening_message='단가 3,500원으로 제안드립니다.')
    writing, _ = write([bad, bad])
    assert writing.source == 'fallback' and writing.attempts == 2
    assert any('3,500' in p for p in writing.problems)
    assert '3,895원' in writing.strategy and '3,998원' in writing.strategy
    assert writing.cover_letter.startswith('귀사의 무궁한 발전을 기원합니다')


@pytest.mark.parametrize('field,text,problem', [
    ('cover_letter', '최대 수용가까지 협의 가능합니다.', '"최대 수용가"'),
    ('cover_letter', '시장 견적과 비교해 정했습니다.', '"시장 견적"'),
    ('opening_message', '단가 4,100원까지 가능합니다.', '입력에 없는 숫자 4,100'),   # 수용가는 협력사에 말하면 안 됨
    ('strategy', '하도급법 제99조에 따라 정한다.', 'law_refs에 없는 조문 제99조'),
])
def test_guard_catches_leaks_and_unknown_articles(field, text, problem):
    proposal, doc = offer_and_doc()
    full, supplier_terms = payloads(proposal, doc)
    from negopilot_agent.offer_proposal.writer import NegotiationDraft
    problems = check_draft(NegotiationDraft(**good_draft(**{field: text})), full, supplier_terms,
                           proposal.offer.law_refs, len(proposal.offer.objections))
    assert any(problem in p for p in problems), problems


def test_objection_count_and_broken_output_fall_back():
    writing, _ = write([good_draft(objection_scripts=['하나']), None])
    assert writing.source == 'fallback'
    assert any('개수가 다름' in p for p in writing.problems) or writing.problems == ['모델 출력 없음 또는 형식 오류']
    writing, _ = write(['{not json', '{"strategy": 1}'])
    assert writing.source == 'fallback' and writing.problems == ['모델 출력 없음 또는 형식 오류']


# ---------- 협력사 제안서에는 내부 값이 없다 ----------

FORBIDDEN = ['목표 합의가', '최대 수용가', '수용가', '양보', '쟁점', '시장 견적', '중앙값', '직전 견적', '확인 필요',
             'check_offer', 'AI', '대외비', '3,998', '4,100', '4,300', 'CHECK_NEEDED', '경고']


def supplier_texts(doc: OfferDocument) -> list[str]:
    pdf = ''.join(p.extract_text() for p in PdfReader(io.BytesIO(supplier.render_pdf(doc))).pages)
    return [pdf, supplier.docx_text(supplier.render_docx(doc))]


def test_supplier_documents_contain_no_internal_position():
    writing, _ = write([good_draft()])
    proposal, doc = offer_and_doc()
    doc = doc.model_copy(update={'cover_letter': writing.cover_letter})
    for text in supplier_texts(doc):
        assert [w for w in FORBIDDEN if w in text] == []
        for expected in ('단가 및 거래조건 제안서', '3,895원', '목적물 수령일부터 45일 이내 현금 지급', 'SKALA 자동차',
                         '구매1팀', '홍길동', '연동 대상 원재료', '표준 하도급대금 연동계약서', '2026-10-30', '서명'):
            assert expected in text.replace('\n', ' ').replace('ㆍ', '·'), expected


def test_briefing_is_marked_confidential_and_has_strategy():
    writing, _ = write([good_draft()])
    proposal, _ = offer_and_doc()
    doc = dict(sections(proposal, writing))
    assert doc['협상 전략'][0] == ('p', 'AI 작성 문구 (숫자·조문을 협상안 값과 대조해 통과)')
    assert doc['제안 멘트(초안)'][0] == ('box', good_draft()['opening_message'])
    assert doc['예상 쟁점과 대응'][0][1][1][1] == good_draft()['objection_scripts'][0]
    pages = PdfReader(io.BytesIO(render_briefing_pdf(proposal, writing))).pages
    assert len(pages) >= 2 and all('대외비 — 협력사 발송 금지' in p.extract_text() for p in pages)


def test_waived_and_exempt_linkage_wording():
    proposal = propose(FakeCaller(bench()), linkage_waived_by_agreement=True)
    lines = build_document(proposal, BUYER).linkage_lines()
    assert lines == ['당사와 귀사는 하도급대금 연동을 하지 아니하기로 합의하며, 그 취지와 사유를 별도 서면에 분명하게 적습니다.']
    exempt = build_document(propose(FakeCaller(bench()), contract_days=60), BUYER)
    assert exempt.linkage_lines() == ['해당 없음']


# ---------- 원재료 비중을 모르면 입력 필요 ----------

def test_unknown_ratio_is_input_needed_not_silently_unlinked():
    proposal = propose(FakeCaller(bench(supplier__raw_material_ratio=None, supplier__price_linkage=False)))
    o = proposal.offer
    assert o.linkage_status == 'needs_input' and o.price_linkage is False
    assert [x.field for x in proposal.input_needed] == ['raw_material_ratio']
    assert 'RAW_MATERIAL_RATIO_REQUIRED' in [w['code'] for w in o.warnings]
    assert '연동 약정 확인 필요' in o.headline
    doc = build_document(proposal, BUYER)
    assert doc.linkage_needs_input and doc.linkage_lines() == [NEEDS_INPUT_WORDING]
    assert dict(sections(proposal))['입력 필요'] == [('list', ['원재료 비중을 입력하면 연동 의무를 판정합니다 (지금은 연동 약정 확인 필요)'])]
    given = propose(FakeCaller(bench(supplier__raw_material_ratio=None)), raw_material_ratio=0.3)
    assert given.offer.linkage_status == 'included' and given.input_needed == []


def test_unknown_ratio_with_existing_linkage_stays_linked():
    proposal = propose(FakeCaller(bench(supplier__raw_material_ratio=None, supplier__price_linkage=True)))
    assert proposal.offer.linkage_status == 'included' and proposal.input_needed == []


# ---------- 문서 모델과 해시 ----------

def mail_mcp_content_hash(document: dict, attachments: list[tuple[str, str, bytes]]) -> str:
    """기탁님 mail-mcp 계약(docs/contracts/mail.md 3절, #236 댓글)의 함수를 그대로 옮겼다. 바꾸지 않는다."""
    import hashlib
    doc = {k: v for k, v in document.items() if k != "content_hash"}
    files = sorted(({"filename": name, "content_type": ctype, "sha256": hashlib.sha256(data).hexdigest()}
                    for name, ctype, data in attachments), key=lambda f: f["filename"])
    payload = json.dumps({"document": doc, "attachments": files},
                         ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def test_content_hash_matches_mail_mcp_contract():
    _, doc = offer_and_doc()
    files = supplier.attachments(doc)
    hashed = doc.hashed(files)
    assert hashed.content_hash == mail_mcp_content_hash(hashed.model_dump(mode='json'), files)
    assert hashed.content_hash == mail_mcp_content_hash(doc.model_dump(mode='json'), list(reversed(files)))
    pdf_only = doc.hashed(files[:1]).content_hash
    assert pdf_only == mail_mcp_content_hash(doc.model_dump(mode='json'), files[:1]) != hashed.content_hash


def test_content_hash_is_sensitive_to_every_value():
    _, doc = offer_and_doc()
    files = supplier.attachments(doc)
    base = doc.hashed(files).content_hash
    for change in ({'version': 2}, {'status': 'final'}, {'unit_price': doc.unit_price + 1}, {'cover_letter': '다름'}):
        changed = doc.model_copy(update=change)
        assert changed.hashed(supplier.attachments(changed)).content_hash != base
    assert doc.document_id == 'NP-RFQ-2026-001-SUP-0007-20261023'
    assert doc.reply_by.isoformat() == '2026-10-30' and doc.verdict_code == 'OK'


def test_rendering_twice_gives_identical_bytes_and_hash():
    _, doc = offer_and_doc()
    first, second = supplier.attachments(doc), supplier.attachments(doc.model_copy())
    assert [f[2] for f in first] == [f[2] for f in second]
    assert doc.hashed(first).content_hash == doc.hashed(second).content_hash
    assert b'/CreationDate (D:20261023000000' in first[0][2]
    import zipfile
    assert {i.date_time for i in zipfile.ZipFile(io.BytesIO(first[1][2])).infolist()} == {(2026, 10, 23, 0, 0, 0)}


def test_document_rejects_unknown_fields():
    _, doc = offer_and_doc()
    with pytest.raises(ValueError):
        OfferDocument.model_validate(doc.model_dump(mode='json') | {'walk_away': 4100})


def test_buyer_settings_come_from_env(monkeypatch):
    monkeypatch.setenv('NEGOPILOT_BUYER_COMPANY', '테스트상사')
    monkeypatch.delenv('NEGOPILOT_BUYER_EMAIL', raising=False)
    buyer = BuyerSettings.from_env()
    assert buyer.company == '테스트상사' and buyer.email == '[이메일]'


def test_package_files_names():
    pkg = asyncio.run(build_package(request(), FakeCaller(bench()), buyer=BUYER))
    assert set(package_files(pkg)) == {'내부브리핑_RFQ-2026-001_SUP-0007.pdf', '제안서_RFQ-2026-001_SUP-0007_v1.pdf',
                                       '제안서_RFQ-2026-001_SUP-0007_v1.docx'}
    assert pkg.document.content_hash == pkg.document.hashed(supplier.attachments(pkg.document)).content_hash


# ---------- 엑셀 입력 ----------

def test_excel_template_rows_are_valid_requests():
    rows = excel.parse_rows(excel.template_bytes(), 'template.xlsx', '2026-10-23')
    assert [r.error for r in rows] == [None, None]
    first, second = (r.request for r in rows)
    assert (first.supplier, first.payment_days, first.price_linkage, first.notes) == ('SUP-0055', 90, False, '월 2회 분할 납품')
    assert (second.target_price, second.max_price, second.raw_material_ratio) == (1750, 2050, 0.42)


def test_csv_aliases_values_and_row_errors():
    data = ('협력사,RFQ,목표단가,최대수용가,지급일수,연동,원재료비중,계약기간(일),비고\n'
            'SUP-0007,RFQ-2026-001,"3,800",4100,45,포함,40%,365,월 2회\n'
            'SUP-0007,RFQ-2026-001,,,,모름,,,\n'
            'SUP-0007,RFQ-2026-001,4000,3900,,,,,\n'
            ',,,,,,,,\n').encode('utf-8')
    rows = excel.parse_rows(data, 'offers.csv', '2026-10-23')
    assert len(rows) == 3                                    # 빈 행은 건너뛴다
    ok = rows[0].request
    assert (ok.target_price, ok.max_price, ok.price_linkage, ok.raw_material_ratio, ok.as_of.isoformat()) == \
        (3800, 4100, True, 0.4, '2026-10-23')
    assert rows[1].error.startswith('예/아니오로') and rows[1].row == 3
    assert 'max_price' in rows[2].error


def test_unknown_header_is_reported_per_row():
    rows = excel.parse_rows('협력사,RFQ,할인율\nSUP-0007,RFQ-2026-001,3\n'.encode(), 'a.csv', '2026-10-23')
    assert rows[0].error == '알 수 없는 머리글: 할인율'


def test_batch_keeps_going_after_a_failing_row():
    rows = excel.parse_rows('협력사,RFQ\nSUP-0007,RFQ-2026-001\nSUP-0008,RFQ-2026-001\n'.encode(), 'a.csv',
                            '2026-10-23')

    async def produce(req):
        if req.supplier == 'SUP-0008':
            raise RuntimeError('[SUPPLIER_NOT_FOUND] 없음')
        return {'status': 'offer'}, {'a.pdf': b'%PDF'}
    summary, files = asyncio.run(excel.run_batch(rows, produce))
    assert [s['status'] for s in summary] == ['offer', 'error'] and list(files) == ['002_a.pdf']
    assert '[SUPPLIER_NOT_FOUND]' in summary[1]['error']


# ---------- 같은 뜻의 경고는 하나만 ----------

def test_ratio_warning_is_not_duplicated_when_check_offer_already_flags_it():
    def check(args):
        return {'verdict_code': 'CHECK_NEEDED', 'violations': [], 'law_refs': [],
                'warnings': [{'code': 'RAW_MATERIAL_RATIO_UNKNOWN', 'severity': 'warning', 'message': '원재료 비중을 몰라'}]}
    proposal = propose(FakeCaller(bench(supplier__raw_material_ratio=None, supplier__price_linkage=False), check))
    codes = [w['code'] for w in proposal.offer.warnings]
    assert codes == ['RAW_MATERIAL_RATIO_UNKNOWN']                      # 우리 RAW_MATERIAL_RATIO_REQUIRED는 붙이지 않는다
    assert [x.field for x in proposal.input_needed] == ['raw_material_ratio']   # 입력 필요는 그대로
    assert proposal.offer.linkage_status == 'needs_input'


# ---------- 발신 정보는 로그인 회원에서 ----------

MEMBER = {'name': '김가연', 'department': '구매전략팀', 'job_title': '매니저', 'email': 'gayeon@example.com'}


def member_document(monkeypatch, **sender_over):
    monkeypatch.setenv('NEGOPILOT_BUYER_COMPANY', 'SKALA 자동차')
    monkeypatch.delenv('NEGOPILOT_BUYER_PHONE', raising=False)
    pkg = asyncio.run(build_package(request(sender=MEMBER | sender_over), FakeCaller(bench(market__median=4300.0))))
    return pkg.document


def test_member_sender_fills_proposal_without_placeholders(monkeypatch):
    doc = member_document(monkeypatch)
    assert doc.sender.model_dump() == {'company': 'SKALA 자동차', 'department': '구매전략팀', 'contact_name': '김가연',
                                       'job_title': '매니저', 'email': 'gayeon@example.com'}   # 전화 없음은 아예 뺀다
    for text in supplier_texts(doc):
        flat = text.replace('\n', ' ')
        assert '김가연 매니저' in flat and '구매전략팀' in flat and 'gayeon@example.com' in flat
        assert '[' not in flat and '전화' not in flat                     # 자리 표시도, 빈 전화 줄도 없다


def test_phone_comes_from_request_then_env(monkeypatch):
    assert member_document(monkeypatch, phone='02-123-4567').sender.phone == '02-123-4567'
    monkeypatch.setenv('NEGOPILOT_BUYER_PHONE', '02-999-0000')
    pkg = asyncio.run(build_package(request(sender=MEMBER), FakeCaller(bench())))
    assert pkg.document.sender.phone == '02-999-0000'
    assert '전화 02-999-0000' in supplier_texts(pkg.document)[0]


def test_sender_is_part_of_the_content_hash(monkeypatch):
    doc = member_document(monkeypatch)
    other = doc.model_copy(update={'sender': doc.sender.model_copy(update={'email': 'someone@example.com'})})
    assert other.hashed(supplier.attachments(other)).content_hash != doc.content_hash


def test_missing_sender_falls_back_to_env_without_phone_placeholder(monkeypatch):
    for key in ('NEGOPILOT_BUYER_PHONE', 'NEGOPILOT_BUYER_EMAIL'):
        monkeypatch.delenv(key, raising=False)
    buyer = BuyerSettings.from_env()
    assert buyer.phone is None and buyer.email == '[이메일]'
    assert buyer.contact() == '[담당자] ([이메일])'


@pytest.mark.parametrize('field,value', [('email', 'not-an-email'), ('name', ''), ('extra', 'x'), ('department', 1)])
def test_sender_rejects_bad_values(field, value):
    with pytest.raises(ValueError):
        request(sender=MEMBER | {field: value})


def test_company_defaults_to_skala(monkeypatch):
    monkeypatch.delenv('NEGOPILOT_BUYER_COMPANY', raising=False)
    assert BuyerSettings.from_env().company == 'SKALA 자동차'
    sender = asyncio.run(build_package(request(sender=MEMBER), FakeCaller(bench()))).document.sender
    assert sender.company == 'SKALA 자동차' and sender.contact_name == '김가연'


def test_sender_passes_mail_mcp_placeholder_guard(monkeypatch):
    """mail-mcp(#247) check_document와 같은 규칙: 발신 정보에 빈 값·자리표시가 있으면 보내지 않는다."""
    import re
    placeholder = re.compile(r"^\s*\[[^\]]*\]\s*$")

    def blank(sender: dict) -> list[str]:
        return [k for k, v in sender.items() if not v or placeholder.match(str(v))]

    doc = member_document(monkeypatch).model_dump(mode='json')
    assert blank(doc['sender']) == [] and 'phone' not in doc['sender']
    assert OfferDocument.model_validate(doc).sender.phone is None         # 빠진 전화는 다시 읽어도 None
    no_title = member_document(monkeypatch, job_title='').model_dump(mode='json')['sender']
    assert 'job_title' not in no_title and blank(no_title) == []
    monkeypatch.delenv('NEGOPILOT_BUYER_CONTACT_NAME', raising=False)
    fallback = BuyerSettings.from_env().model_dump()
    assert 'contact_name' in blank(fallback)                               # 설정도 회원도 없으면 발송이 막힌다
