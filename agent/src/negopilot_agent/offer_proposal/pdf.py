"""협상안을 PDF 한 부로 만든다 (#231). 모델을 부르지 않고 Proposal의 값만 옮긴다.

한글은 reportlab 내장 CID 글꼴(HYGothic-Medium)을 써서 글꼴 파일이 필요 없다. 이 글꼴에 없는 가운뎃점(·)은
한글 가운뎃점(ㆍ)으로 바꿔 그린다.
sections()가 문서 내용(제목과 문단·표)을 만들고 render_pdf()는 그것을 그대로 그린다.
평가는 sections()의 숫자가 Proposal 값과 같은지 본다.
"""

from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from xml.sax.saxutils import escape

from .generator import Proposal

BODY_FONT = HEAD_FONT = 'HYGothic-Medium'
VERDICTS = {'OK': '문제 없음', 'CHECK_NEEDED': '확인 필요', 'VIOLATION': '위반 소지 있음'}
LAWS = {'subcontract_act': '하도급법', 'subcontract_decree': '하도급법 시행령', 'sme_act': '중소기업기본법',
        'sme_decree': '중소기업기본법 시행령'}
FIELDS = {'target_price': '목표 단가', 'max_price': '최대 수용가', 'payment_days': '지급 일수',
          'contract_days': '계약 기간(일)', 'raw_material_ratio': '원재료 비중', 'price_linkage': '연동 약정'}
NOTICE = '이 문서는 초안이며 최종 결정과 발송은 담당자가 한다. 법률 최종 판단은 법무 확인을 거친다.'

Block = tuple[str, object]   # ('p', 문장) | ('table', [[칸, ...], ...]) | ('list', [문장, ...])


def won(value: int | float | None) -> str:
    return '-' if value is None else f'{value:,.0f}원'


def signed(value: float | None) -> str:
    return '-' if value is None else f'{value:+.1f}%'


def filled_value(field: str, value) -> str:
    if value is None:
        return '모름'
    if field in ('target_price', 'max_price'):
        return won(value)
    if field == 'raw_material_ratio':
        return f'{value * 100:.0f}%'
    return f'{value}일' if field.endswith('_days') else str(value)


def sections(p: Proposal) -> list[tuple[str, list[Block]]]:
    o, bench = p.offer, p.benchmarks
    market, last = bench['market'], bench['supplier']['last_price']
    out = [('협상안', [('table', [['작성 기준일', p.as_of], ['RFQ', p.rfq_id], ['품목', p.part_name],
                                  ['협력사', f'{p.supplier_name} ({p.supplier_id})']])])]
    if o is None:
        out.append(('결론', [('p', '제시할 수 있는 협상안이 없다. 모든 후보에 위반 소지가 남았다.')]))
        out.append(('이유와 바꿔야 할 조건', [('list', p.no_offer_reasons)]))
    else:
        out.append(('결론', [('p', o.headline)]))
        linkage = '포함' if o.price_linkage else ('미연동 합의(사유 서면 필요)' if o.linkage_waived_by_agreement else '없음')
        ratio = '-' if o.raw_material_ratio is None else f'{o.raw_material_ratio * 100:.0f}%'
        energy = '' if o.energy_ratio is None else f', 에너지 비중 {o.energy_ratio * 100:.0f}%'
        out.append(('제시 조건', [('table', [
            ['단가', won(o.unit_price)], ['연간 물량', f'{o.annual_volume:,}개'],
            ['계약 기간', f'{o.contract_days}일'], ['예상 계약금액', won(o.contract_amount)],
            ['지급 조건', f'목적물 수령일부터 {o.payment_days}일 이내 지급'],
            ['연동 약정', f'{linkage} (주요 원재료 비중 {ratio}{energy})'],
            ['납품·비고', o.notes or '-']])]))
        rows = [['구분', '단가', '목표 단가 대비', f'시장 중앙값 대비 ({won(market["median"])})',
                 f'직전 견적 대비 ({won(last)})', '검사']]
        rows += [[x.label, won(x.unit_price), signed(x.vs_target_pct), signed(x.vs_market_pct),
                  signed(x.vs_last_quote_pct), VERDICTS[x.verdict_code]] for x in o.price_range]
        out.append(('협상 범위', [('table', rows), ('p', f'목표 단가 {won(p.target_price)} 기준')]))
        out.append(('양보 순서', [('list', [f'{n}. {text}' for n, text in enumerate(o.concessions, 1)])]))
        out.append(('예상 쟁점과 대응', [('table', [['쟁점', '대응']] + [[x.issue, x.response] for x in o.objections])]
                    if o.objections else [('p', '데이터에서 찾은 쟁점이 없다.')]))
        refs = ', '.join(dict.fromkeys(f'{LAWS.get(r["law"], r["law"])} {r["article_no"]}{r.get("paragraph", "")}'
                                       for r in o.law_refs))
        review = [('p', f'check_offer 판정: {VERDICTS[o.verdict_code]} ({o.verdict_code}, 위반 소지 0건)')]
        if o.warnings:
            review.append(('list', [f'확인 필요: {w["message"]}' for w in o.warnings]))
        review.append(('p', f'근거 조문: {refs or "-"}'))
        out.append(('법규 검토 결과', review))
        out.append(('숫자 근거', [('list', [b.note for b in o.rationale])]))
    changed = []
    if o is not None:
        changed += [f'조정: {a}' for a in o.adjustments]
        changed += [f'고침: {r.code} → {r.field} {r.before} → {r.after}' for r in o.revisions]
    changed += [f'자동 입력: {FIELDS.get(f.field, f.field)} = {filled_value(f.field, f.value)} ({f.source})' for f in p.filled]
    out.append(('조정한 조건과 자동으로 채운 값', [('list', changed or ['없음'])]))
    out.append(('고지', [('p', NOTICE)]))
    return out


def text(value) -> str:
    return escape(str(value).replace('·', 'ㆍ'))


def register_fonts():
    for name in {BODY_FONT, HEAD_FONT}:
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(UnicodeCIDFont(name))


def column_widths(rows: list[list]) -> list[float]:
    width = A4[0] - 36 * mm
    if len(rows[0]) == 2:
        first = 60 * mm if rows[0][0] == '쟁점' else 35 * mm
        return [first, width - first]
    return [width / len(rows[0])] * len(rows[0])


def render_pdf(proposal: Proposal) -> bytes:
    register_fonts()
    body = ParagraphStyle('body', fontName=BODY_FONT, fontSize=9.5, leading=14)
    head = ParagraphStyle('head', fontName=HEAD_FONT, fontSize=12, leading=18, spaceBefore=8, spaceAfter=4)
    title = ParagraphStyle('title', fontName=HEAD_FONT, fontSize=20, leading=26, spaceAfter=6)
    cell = ParagraphStyle('cell', fontName=BODY_FONT, fontSize=8.5, leading=12)
    story = []
    for n, (name, blocks) in enumerate(sections(proposal)):
        story.append(Paragraph(text(name), title if n == 0 else head))
        for kind, value in blocks:
            if kind == 'p':
                story.append(Paragraph(text(value), body))
            elif kind == 'list':
                story += [Paragraph(text(item), body) for item in value]
            else:
                data = [[Paragraph(text(c), cell) for c in row] for row in value]
                table = Table(data, hAlign='LEFT', colWidths=column_widths(value))
                table.setStyle(TableStyle([('GRID', (0, 0), (-1, -1), 0.4, colors.grey),
                                           ('BACKGROUND', (0, 0), (0, -1), colors.whitesmoke),
                                           ('VALIGN', (0, 0), (-1, -1), 'TOP')]))
                story.append(table)
        story.append(Spacer(1, 3 * mm))
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
                            bottomMargin=16 * mm, title=f'협상안 {proposal.rfq_id} {proposal.supplier_id}')
    doc.build(story)
    return buffer.getvalue()
