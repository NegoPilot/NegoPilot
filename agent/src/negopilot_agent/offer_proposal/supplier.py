"""협력사 발송용 "단가 및 거래조건 제안서" PDF·DOCX (#236).

OfferDocument의 값만 그린다. 내부 협상 값(목표 합의가, 최대 수용가, 양보 순서, 쟁점, 시장가·직전 견적 비교, 경고,
법규 검토 내부 내용)과 "AI" 표시는 넣지 않는다. PDF와 DOCX는 같은 sections()를 그린다.
같은 문서는 언제 다시 그려도 같은 바이트가 나온다(문서 날짜를 파일 시각으로 고정). 그래야 미리보기와 발송 사이에
다시 렌더해도 content_hash가 같다 (mail.md 3절).
"""

import zipfile
from collections.abc import Iterable
from datetime import datetime, time
from io import BytesIO
from urllib.parse import quote

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt

from .document import OfferDocument
from .pdf import Block, draw, won

PDF_TYPE = 'application/pdf'
DOCX_TYPE = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
KINDS = {'pdf': PDF_TYPE, 'docx': DOCX_TYPE}
DOCX_FONT = '맑은 고딕'


def ratio(value: float | None) -> str:
    return '-' if value is None else f'{value * 100:.0f}%'


def sections(d: OfferDocument) -> list[tuple[str, list[Block]]]:
    s = d.sender
    item = [['RFQ', d.rfq_id], ['품목', d.part_name]]
    if d.vehicle:
        item.append(['차종', d.vehicle])
    item.append(['연간 물량', f'{d.annual_volume:,}개'])
    linkage = ('포함' if d.price_linkage else '미연동 합의' if d.linkage_waived_by_agreement
               else '협의 후 결정' if d.linkage_needs_input else '해당 없음')
    return [
        ('단가 및 거래조건 제안서', [('table', [
            ['문서 번호', f'{d.document_id} (v{d.version})'], ['작성일', d.issued_on.isoformat()],
            ['수신', f'{d.supplier_name} ({d.supplier_id}) 귀중'],
            ['발신', f'{s.company} {s.department} {s.contact()}'],
            ['제목', d.title]])]),
        ('인사말', [('p', d.cover_letter)]),
        ('대상 품목', [('table', item)]),
        ('제안 조건', [('table', [
            ['단가', won(d.unit_price)], ['계약 기간', f'{d.contract_days}일'],
            ['예상 계약금액', f'{won(d.contract_amount)} (연간 물량 기준, 부가가치세 별도)'],
            ['지급 조건', d.payment_terms()], ['납품대금 연동', linkage]])]),
        ('납품대금 연동', [('list', d.linkage_lines())]),
        ('납품 및 비고', [('p', d.delivery_notes or '-')]),
        ('회신 요청', [('p', f'{d.reply_by.isoformat()}까지 회신 부탁드립니다. 문의: {s.contact()}')]),
        ('서명', [('table', [['발신', f'{s.company} {s.department}'],
                            ['담당자', f'{s.contact_name}{" " + s.job_title if s.job_title else ""} (서명/인)'],
                            ['수신 확인', f'{d.supplier_name} (서명/인)']])]),
    ]


def render_pdf(d: OfferDocument) -> bytes:
    return draw(sections(d), d.title, fixed_date=d.issued_on)


def render_docx(d: OfferDocument) -> bytes:
    doc = Document()
    style = doc.styles['Normal']
    style.font.name = DOCX_FONT
    style.font.size = Pt(10)
    style.element.rPr.rFonts.set(qn('w:eastAsia'), DOCX_FONT)
    for n, (name, blocks) in enumerate(sections(d)):
        heading = doc.add_heading(name, level=0 if n == 0 else 2)
        if n == 0:
            heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
        for kind, value in blocks:
            if kind == 'p':
                doc.add_paragraph(value)
            elif kind == 'list':
                for line in value:
                    doc.add_paragraph(line, style='List Bullet')
            else:
                table = doc.add_table(rows=0, cols=len(value[0]))
                table.style = 'Table Grid'
                for row in value:
                    cells = table.add_row().cells
                    for cell, content in zip(cells, row):
                        cell.text = str(content)
    stamp = datetime.combine(d.issued_on, time())
    props = doc.core_properties
    props.title, props.author, props.last_modified_by, props.revision = d.title, d.sender.company, d.sender.company, 1
    props.created = props.modified = props.last_printed = stamp
    buffer = BytesIO()
    doc.save(buffer)
    return fixed_zip(buffer.getvalue(), stamp)


def fixed_zip(data: bytes, stamp: datetime) -> bytes:
    """zip 항목 시각을 문서 날짜로 고정해 다시 묶는다. 같은 문서면 같은 바이트가 나온다."""
    out = BytesIO()
    with zipfile.ZipFile(BytesIO(data)) as src, zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            info = zipfile.ZipInfo(item.filename, date_time=stamp.timetuple()[:6])
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            dst.writestr(info, src.read(item.filename))
    return out.getvalue()


def attachments(d: OfferDocument, kinds: Iterable[str] = ('pdf', 'docx')) -> list[tuple[str, str, bytes]]:
    """메일에 붙일 협력사 제안서 파일 (파일 이름, Content-Type, 바이트). 내부 브리핑은 넣지 않는다."""
    render = {'pdf': render_pdf, 'docx': render_docx}
    return [(f'{d.file_stem()}.{kind}', KINDS[kind], render[kind](d)) for kind in kinds]


def docx_text(data: bytes) -> str:
    doc = Document(BytesIO(data))
    parts = [p.text for p in doc.paragraphs]
    parts += [c.text for t in doc.tables for row in t.rows for c in row.cells]
    return '\n'.join(parts)


def content_disposition(filename: str) -> str:
    ascii_name = filename.encode('ascii', 'replace').decode().replace('?', '_')
    return f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(filename)}'
