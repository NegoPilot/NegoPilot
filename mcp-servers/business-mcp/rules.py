"""협상안 검사에 쓰는 법 규칙과 기준값 (business-mcp).

규칙마다 코드, 기준값, 근거 조문(항까지), 적용 기간을 둔다. 근거 조문은 law-mcp 색인(law_key, 조문 번호, 항)과
같은 표기를 쓴다. 이 파일의 함수는 DB를 읽지 않으므로 DB 없이 경계값을 테스트할 수 있다.
"""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class LawRef:
    law: str
    article_no: str
    paragraph: str | None = None


@dataclass(frozen=True)
class Rule:
    code: str
    value: int | float
    refs: tuple[LawRef, ...]
    effective_from: date | None = None   # None이면 색인의 가장 오래된 시행판 이전부터
    effective_to: date | None = None     # None이면 현재까지. 이 날은 포함하지 않는다
    internal: bool = False               # True면 법 기준이 아닌 내부 기준

    def applies_on(self, as_of: date) -> bool:
        return ((self.effective_from is None or self.effective_from <= as_of)
                and (self.effective_to is None or as_of < self.effective_to))


ACT = "subcontract_act"
DECREE = "subcontract_decree"

# 하도급법 제13조①: 목적물 수령일부터 60일 이내 지급
PAYMENT_DAYS_LIMIT = Rule("PAYMENT_DAYS_LIMIT", 60, (LawRef(ACT, "제13조", "①"),))
# 하도급법 제2조⑯: 비용이 하도급대금의 100분의 10 이상이면 주요 원재료
MAJOR_MATERIAL_RATIO = Rule("MAJOR_MATERIAL_RATIO", 0.10, (LawRef(ACT, "제2조", "⑯"),))
# 하도급법 제3조④2, 시행령 제3조③: 거래 기간 90일 이내면 연동 사항을 적지 않을 수 있다
LINKAGE_EXEMPT_DAYS = Rule("LINKAGE_EXEMPT_DAYS", 90, (LawRef(ACT, "제3조", "④"), LawRef(DECREE, "제3조", "③")))
# 하도급법 제3조④3, 시행령 제3조④: 하도급대금 1억원 이하면 연동 사항을 적지 않을 수 있다
LINKAGE_EXEMPT_AMOUNT = Rule("LINKAGE_EXEMPT_AMOUNT", 100_000_000,
                             (LawRef(ACT, "제3조", "④"), LawRef(DECREE, "제3조", "④")))
# 시장 중앙값의 90% 미만이면 제4조(부당한 하도급대금 결정) 위험으로 본다 (내부 기준)
MARKET_PRICE_FLOOR = Rule("MARKET_PRICE_FLOOR", 0.90, (LawRef(ACT, "제4조"),), internal=True)
# 직전 견적보다 5% 넘게 낮으면 인하 근거 확인 (내부 기준)
LAST_QUOTE_CUT = Rule("LAST_QUOTE_CUT", 0.05, (LawRef(ACT, "제4조"),), internal=True)
# 중소기업기본법 시행령 별표1 자동차 제조업 평균매출 기준(억 원). law-mcp 색인에 별표가 없어 확인 필요
SME_REVENUE_EOK = Rule("SME_REVENUE_EOK", 1000, (LawRef(ACT, "제2조"),), internal=True)

RULES = (PAYMENT_DAYS_LIMIT, MAJOR_MATERIAL_RATIO, LINKAGE_EXEMPT_DAYS, LINKAGE_EXEMPT_AMOUNT,
         MARKET_PRICE_FLOOR, LAST_QUOTE_CUT, SME_REVENUE_EOK)


def finding(code: str, severity: str, message: str, *refs: tuple[str, str]) -> dict:
    """검사 결과 한 건. law_refs는 law-mcp get_article(law, article_no)로 그대로 넘길 수 있다."""
    return {"code": code, "severity": severity, "message": message,
            "law_refs": [{"law": law, "article_no": no} for law, no in refs]}


def payment_days_exceeded(payment_days: int | None) -> bool:
    return bool(payment_days) and payment_days > PAYMENT_DAYS_LIMIT.value


def payment_findings(payment_days: int) -> list[dict]:
    if payment_days_exceeded(payment_days):
        limit = PAYMENT_DAYS_LIMIT.value
        return [finding("PAYMENT_DAYS_OVER_60", "violation",
                        f"지급 기한 {payment_days}일: 목적물 수령일부터 {limit}일 이내로 정해야 한다 "
                        "(대등한 지위에서 정했거나 업종 특수성이 인정되는 예외 제외). 초과 기간에는 지연이자가 붙는다",
                        (ACT, "제13조"))]
    return []


def linkage_findings(raw_material_ratio: float | None, price_linkage: bool, contract_amount: int | None,
                     contract_days: int | None, waived: bool = False, buyer_is_small_business: bool = False) -> list[dict]:
    """주요 원재료 연동 약정(하도급법 제3조②3·④, 제2조⑯) 검사."""
    if price_linkage:
        return []
    if raw_material_ratio is None:
        return [finding("RAW_MATERIAL_RATIO_UNKNOWN", "warning",
                        "원재료 비중을 몰라 연동 약정 대상인지 판단할 수 없다. 원재료 비용이 하도급대금의 10% 이상이면 "
                        "연동 사항을 서면에 적어야 한다", (ACT, "제2조"), (ACT, "제3조"))]
    if raw_material_ratio < MAJOR_MATERIAL_RATIO.value:
        return []
    pct = f"{raw_material_ratio * 100:.0f}%"
    exemptions = []
    if buyer_is_small_business:
        exemptions.append("원사업자가 소기업")
    if contract_days is not None and contract_days <= LINKAGE_EXEMPT_DAYS.value:
        exemptions.append(f"거래 기간 {contract_days}일(90일 이내)")
    if contract_amount is not None and contract_amount <= LINKAGE_EXEMPT_AMOUNT.value:
        exemptions.append(f"하도급대금 {contract_amount:,}원(1억 원 이하)")
    if exemptions:
        return [finding("LINKAGE_EXEMPT", "info",
                        f"원재료 비중 {pct}로 연동 대상이지만 예외에 해당할 수 있다: {', '.join(exemptions)}. "
                        "기간·금액 기준은 시행령 값을 확인한다", (ACT, "제3조"))]
    if waived:
        return [finding("LINKAGE_WAIVED_NEEDS_WRITTEN_REASON", "warning",
                        f"원재료 비중 {pct}로 연동 대상인데 연동하지 않기로 합의했다. 그 취지와 사유를 서면에 분명하게 "
                        "적어야 하고, 거래상 지위를 남용해 합의를 유도하면 안 된다", (ACT, "제3조"))]
    return [finding("LINKAGE_TERMS_MISSING", "violation",
                    f"원재료 비중 {pct}로 주요 원재료(10% 이상)에 해당하는데 연동 약정이 없다. 연동 대상 원재료, 조정요건, "
                    "기준 지표, 산식 등을 서면에 적어야 한다", (ACT, "제2조"), (ACT, "제3조"))]


def price_findings(unit_price: int, market_median: float | None, market_n: int,
                   last_price: int | None, target_price: int) -> list[dict]:
    """단가가 일반적으로 지급되는 대가보다 낮은지 (제4조①, ②5 일방적으로 낮은 단가, ②1 일률적 인하)."""
    out = []
    if market_median and unit_price < market_median * MARKET_PRICE_FLOOR.value:
        out.append(finding(
            "PRICE_BELOW_MARKET", "warning",
            f"단가 {unit_price:,}원이 같은 공정·소재 최근 견적 중앙값 {market_median:,.0f}원({market_n}곳)의 "
            f"{unit_price / market_median * 100:.0f}%다. 일반적으로 지급되는 대가보다 낮게 정하면 부당한 하도급대금 "
            "결정이 될 수 있으니 원가 근거와 협의 기록을 남긴다", (ACT, "제4조")))
    if last_price and unit_price < last_price * (1 - LAST_QUOTE_CUT.value):
        out.append(finding(
            "PRICE_CUT_FROM_LAST_QUOTE", "warning",
            f"이 협력사의 직전 견적 {last_price:,}원보다 {(1 - unit_price / last_price) * 100:.1f}% 낮다. "
            "정당한 사유 없는 일률적 인하나 일방적으로 낮은 단가 결정이 되지 않도록 인하 근거를 확인한다",
            (ACT, "제4조")))
    if unit_price > target_price:
        out.append(finding("ABOVE_TARGET_PRICE", "info",
                           f"단가가 RFQ 목표 단가 {target_price:,}원보다 "
                           f"{(unit_price / target_price - 1) * 100:.1f}% 높다"))
    return out


def sme_findings(revenue_eok: float | None) -> list[dict]:
    """하도급법 적용 대상(수급사업자 = 중소기업자)인지."""
    if revenue_eok is None:
        return [finding("SME_STATUS_UNKNOWN", "info",
                        "매출 정보가 없어 수급사업자(중소기업자) 해당 여부를 판단할 수 없다", (ACT, "제2조"))]
    if revenue_eok <= SME_REVENUE_EOK.value:
        return [finding("SUBCONTRACT_ACT_APPLIES", "info",
                        f"최근 매출 {revenue_eok:,}억 원으로 중소기업일 가능성이 높아 하도급법상 수급사업자로 "
                        "보고 검사했다 (중소기업 기준은 중소기업기본법 시행령 별표1로 확인)", (ACT, "제2조"))]
    return [finding("SME_STATUS_CHECK", "info",
                    f"최근 매출 {revenue_eok:,}억 원으로 중소기업이 아닐 수 있다. 수급사업자 해당 여부를 "
                    "확인하고, 중견기업이면 지급 조항 확대 적용 대상인지 본다", (ACT, "제2조"), (ACT, "제13조"))]
