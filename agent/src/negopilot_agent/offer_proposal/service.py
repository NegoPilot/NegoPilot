"""요청 → 협상안 → 협력사 제안서 + 내부 브리핑, 고친 제안서 재검사 (#236). API·CLI·일괄 처리가 같이 쓴다."""

from pydantic import BaseModel

from negopilot_agent.routing.mcp_client import McpToolCaller

from . import supplier
from .document import BuyerSettings, OfferDocument, build_document
from .generator import OfferProposer, Proposal, ToolCaller
from .pdf import briefing_filename, render_briefing_pdf
from .request import OfferProposalRequest
from .writer import NegotiationWriter, Writing, apply_writing


class OfferPackage(BaseModel):
    proposal: Proposal
    document: OfferDocument | None        # content_hash는 제안서 PDF·DOCX 두 첨부로 계산해 채운다
    writing: Writing | None

    def check(self) -> dict:
        o = self.proposal.offer
        return {'verdict_code': o.verdict_code if o else None,
                'warnings': [{'code': w['code'], 'message': w['message']} for w in (o.warnings if o else [])]}

    def briefing(self) -> dict:
        return self.proposal.model_dump(mode='json') | {
            'writing': self.writing.model_dump(mode='json') if self.writing else None}


class OfferViolation(Exception):
    def __init__(self, findings: list[dict]):
        super().__init__('제안서 조건에 위반 소지가 있음')
        self.findings = findings


async def build_package(req: OfferProposalRequest, caller: ToolCaller | None = None,
                        writer: NegotiationWriter | None = None, buyer: BuyerSettings | None = None) -> OfferPackage:
    caller = caller or McpToolCaller()
    proposal = await OfferProposer(caller).propose(req)
    if proposal.offer is None:
        return OfferPackage(proposal=proposal, document=None, writing=None)
    document = build_document(proposal, buyer)
    writing = await writer.write(proposal, document) if writer else None
    if writing:
        document = apply_writing(document, writing)
    return OfferPackage(proposal=proposal, document=document.hashed(supplier.attachments(document)), writing=writing)


def package_files(pkg: OfferPackage) -> dict[str, bytes]:
    """협력사 제안서 PDF·DOCX와 내부 브리핑 PDF. 협상안이 없으면 브리핑만."""
    files = {briefing_filename(pkg.proposal): render_briefing_pdf(pkg.proposal, pkg.writing)}
    if pkg.document:
        files |= {name: data for name, _, data in supplier.attachments(pkg.document)}
    return files


async def recheck(document: OfferDocument, caller: ToolCaller | None = None) -> tuple[OfferDocument, dict]:
    """고친 제안서 조건으로 check_offer를 다시 부른다. 위반이면 OfferViolation.

    계약금액과 verdict_code는 check_offer 값으로 맞추고, 받은 content_hash는 지운다(첨부를 정한 뒤 다시 계산한다).
    """
    caller = caller or McpToolCaller()
    args = {'supplier': document.supplier_id, 'rfq_id': document.rfq_id, 'unit_price': document.unit_price,
            'payment_days': document.payment_days, 'price_linkage': document.price_linkage,
            'contract_days': document.contract_days,
            'linkage_waived_by_agreement': document.linkage_waived_by_agreement, 'as_of': document.as_of.isoformat()}
    for key in ('raw_material_ratio', 'energy_ratio'):
        if getattr(document, key) is not None:
            args[key] = getattr(document, key)
    result = await caller('business-mcp', 'check_offer', args)
    if result.get('violations'):
        raise OfferViolation([{k: v.get(k) for k in ('code', 'message', 'law_refs')} for v in result['violations']])
    update = {'verdict_code': result.get('verdict_code'), 'content_hash': None}
    if (amount := (result.get('offer') or {}).get('contract_amount')) is not None:
        update['contract_amount'] = amount
    document = document.model_copy(update=update)
    check = {'verdict_code': result.get('verdict_code'),
             'warnings': [{'code': w['code'], 'message': w['message']} for w in result.get('warnings') or []]}
    return document, check
