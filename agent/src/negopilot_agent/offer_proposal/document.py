"""협력사 발송용 "단가 및 거래조건 제안서" 문서 모델 (#236).

내부 협상 브리핑(목표 합의가, 최대 수용가, 양보 순서, 예상 쟁점, 시장가·직전 견적 비교, 내부 경고)과 분리한다.
이 모델에는 협력사에게 보여도 되는 값만 둔다. 화면에서 고친 문서는 render API가 check_offer로 다시 검사한다.
내용 해시는 메일 발송(mail-mcp, #237)이 승인된 확정본과 첨부를 그대로 보내는지 확인하는 데 쓴다.
해시 규칙과 필드 이름은 mail-mcp 계약(docs/contracts/mail.md 3절)과 같다.
계약: docs/contracts/offer-document.md
"""

import hashlib
import json
import os
from collections.abc import Iterable
from datetime import date, timedelta
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from .generator import Proposal

SCHEMA_VERSION = '1'
DEFAULT_RESPONSE_DAYS = 7
LINKAGE_REFERENCE = '세부 사항은 공정거래위원회 표준 하도급대금 연동계약서(2026)에 따릅니다.'
LINKAGE_BLANKS = ['기준 지표', '조정 요건(변동률)', '조정 주기', '조정 산식']   # 협의해 채울 칸
WAIVED_WORDING = ('당사와 귀사는 하도급대금 연동을 하지 아니하기로 합의하며, 그 취지와 사유를 별도 서면에 분명하게 적습니다.')
NEEDS_INPUT_WORDING = '하도급대금 연동 여부는 주요 원재료 비중을 확인한 뒤 협의하여 정합니다.'


class BuyerSettings(BaseModel):
    """발신(구매사) 정보. 코드에 적지 않고 환경 변수로 받는다 (.env.example 참고)."""
    model_config = ConfigDict(extra='forbid')
    company: str = Field(max_length=100)
    department: str = Field(max_length=100)
    contact_name: str = Field(max_length=50)
    phone: str = Field(max_length=50)
    email: str = Field(max_length=200)

    @classmethod
    def from_env(cls) -> 'BuyerSettings':
        return cls(company=os.getenv('NEGOPILOT_BUYER_COMPANY', '[구매사명]'),
                   department=os.getenv('NEGOPILOT_BUYER_DEPARTMENT', '[구매 부서]'),
                   contact_name=os.getenv('NEGOPILOT_BUYER_CONTACT_NAME', '[담당자]'),
                   phone=os.getenv('NEGOPILOT_BUYER_PHONE', '[전화번호]'),
                   email=os.getenv('NEGOPILOT_BUYER_EMAIL', '[이메일]'))


class OfferDocument(BaseModel):
    """협력사에게 보내는 제안서. 화면에서 고칠 수 있는 값이다. 숫자는 render 때 check_offer로 다시 검사한다."""
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    schema_version: Literal['1'] = SCHEMA_VERSION
    document_id: str = Field(min_length=1, max_length=60)
    version: int = Field(default=1, ge=1)
    status: Literal['draft', 'final'] = 'draft'
    issued_on: Annotated[date, Field(strict=False)]
    reply_by: Annotated[date, Field(strict=False)]
    as_of: Annotated[date, Field(strict=False)]          # check_offer 기준일
    supplier_id: str = Field(pattern=r'^SUP-\d{4}$')
    supplier_name: str = Field(min_length=1, max_length=100)
    sender: BuyerSettings
    title: str = Field(min_length=1, max_length=200)
    rfq_id: str = Field(pattern=r'^RFQ-\d{4}-\d{3}$')
    part_name: str = Field(max_length=100)
    vehicle: str | None = Field(default=None, max_length=50)
    material: str | None = Field(default=None, max_length=50)
    annual_volume: int = Field(gt=0)
    unit_price: int = Field(gt=0)
    contract_days: int = Field(gt=0)
    contract_amount: int = Field(ge=0)                     # render가 check_offer 값으로 다시 채운다
    payment_days: int = Field(gt=0)
    payment_method: str = Field(default='현금', max_length=30)
    price_linkage: bool
    linkage_waived_by_agreement: bool = False
    linkage_needs_input: bool = False                       # 원재료 비중을 몰라 연동 여부 미정
    raw_material_ratio: float | None = Field(default=None, ge=0, le=1)
    energy_ratio: float | None = Field(default=None, ge=0, le=1)
    linkage_terms: dict[str, str] = Field(default_factory=lambda: dict.fromkeys(LINKAGE_BLANKS, ''))
    delivery_notes: str | None = Field(default=None, max_length=500)
    cover_letter: str = Field(max_length=1500)
    verdict_code: Literal['OK', 'CHECK_NEEDED', 'VIOLATION'] | None = None   # 마지막 check_offer 판정
    content_hash: str | None = Field(default=None, pattern=r'^sha256:[0-9a-f]{64}$')   # 첨부까지 포함한 해시

    def payment_terms(self) -> str:
        return f'목적물 수령일부터 {self.payment_days}일 이내 {self.payment_method} 지급'

    def linkage_lines(self) -> list[str]:
        """제안서의 납품대금 연동 조항 요약. 산식 등은 채울 칸으로 남긴다."""
        if self.price_linkage:
            ratio = f' (하도급대금 대비 약 {self.raw_material_ratio * 100:.0f}%)' if self.raw_material_ratio is not None else ''
            lines = [f'연동 대상 원재료: {self.material or "주요 원재료"}{ratio}']
            lines += [f'{k}: {v or "(협의하여 기입)"}' for k, v in self.linkage_terms.items()]
            return lines + [LINKAGE_REFERENCE]
        if self.linkage_waived_by_agreement:
            return [WAIVED_WORDING]
        if self.linkage_needs_input:
            return [NEEDS_INPUT_WORDING]
        return ['해당 없음']

    def hashed(self, attachments: Iterable[tuple[str, str, bytes]]) -> 'OfferDocument':
        """content_hash를 채운 복사본. attachments는 메일에 실제로 붙일 (파일 이름, Content-Type, 바이트)."""
        return self.model_copy(update={'content_hash': content_hash(self.model_dump(mode='json'), attachments)})

    def file_stem(self) -> str:
        return f'제안서_{self.rfq_id}_{self.supplier_id}_v{self.version}'


def content_hash(document: dict, attachments: Iterable[tuple[str, str, bytes]]) -> str:
    """mail-mcp 계약 3절과 같은 계산. content_hash 키를 뺀 문서 + 첨부(파일 이름, 형식, sha256)를 정렬한 JSON의 sha256."""
    doc = {k: v for k, v in document.items() if k != 'content_hash'}
    files = sorted(({'filename': name, 'content_type': ctype, 'sha256': hashlib.sha256(data).hexdigest()}
                    for name, ctype, data in attachments), key=lambda f: f['filename'])
    payload = json.dumps({'document': doc, 'attachments': files},
                         ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return 'sha256:' + hashlib.sha256(payload).hexdigest()


def default_cover(rfq_id: str, part_name: str) -> str:
    return (f'귀사의 무궁한 발전을 기원합니다. 당사는 {rfq_id} {part_name} 건과 관련하여 아래와 같이 단가 및 거래조건을 '
            '제안드리오니, 검토하신 뒤 회신 기한까지 의견을 주시면 감사하겠습니다.')


def build_document(proposal: Proposal, buyer: BuyerSettings | None = None,
                   cover_letter: str | None = None) -> OfferDocument:
    """협상안 결과에서 제안서를 만든다. 내부 값(합의가·수용가·비교·경고)은 옮기지 않는다."""
    o = proposal.offer
    if o is None:
        raise ValueError('협상안이 없어 제안서를 만들 수 없음')
    rfq = proposal.benchmarks['rfq']
    issued = date.fromisoformat(proposal.as_of)
    return OfferDocument(
        document_id=f'NP-{proposal.rfq_id}-{proposal.supplier_id}-{issued:%Y%m%d}',
        issued_on=issued, reply_by=issued + timedelta(days=DEFAULT_RESPONSE_DAYS), as_of=issued,
        supplier_id=proposal.supplier_id, supplier_name=proposal.supplier_name,
        sender=buyer or BuyerSettings.from_env(),
        title=f'{proposal.rfq_id} {proposal.part_name} 단가 및 거래조건 제안',
        rfq_id=proposal.rfq_id, part_name=proposal.part_name, vehicle=rfq.get('vehicle'),
        material=rfq.get('material'), annual_volume=o.annual_volume, unit_price=o.unit_price,
        contract_days=o.contract_days, contract_amount=o.contract_amount, payment_days=o.payment_days,
        price_linkage=o.price_linkage, linkage_waived_by_agreement=o.linkage_waived_by_agreement,
        linkage_needs_input=o.linkage_status == 'needs_input', raw_material_ratio=o.raw_material_ratio,
        energy_ratio=o.energy_ratio, delivery_notes=o.notes,
        cover_letter=cover_letter or default_cover(proposal.rfq_id, proposal.part_name), verdict_code=o.verdict_code)
