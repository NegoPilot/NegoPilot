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

    def as_dict(self) -> dict:
        out = {"law": self.law, "article_no": self.article_no}
        if self.paragraph:
            out["paragraph"] = self.paragraph
        return out


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
SME_DECREE = "sme_decree"

# 판정에 쓰는 법령별 시행판의 시행일. law-mcp 색인(data/index)과 같아야 한다 (offline_tests/test_rules.py가 대조)
LAW_VERSIONS = {
    ACT: (date(2025, 1, 21), date(2025, 4, 1), date(2025, 12, 17), date(2026, 8, 11), date(2026, 10, 2),
          date(2027, 9, 9)),
    DECREE: (date(2024, 5, 28), date(2024, 6, 8), date(2024, 12, 27), date(2025, 10, 1), date(2026, 7, 1),
             date(2026, 8, 11)),
    SME_DECREE: (date(2024, 7, 10), date(2024, 8, 21), date(2025, 9, 1), date(2025, 10, 1), date(2025, 12, 23),
                 date(2026, 1, 2)),
}

# 협력사는 모두 자동차 부품 제조사라 주된 업종을 자동차 및 트레일러 제조업(C30)으로 본다.
# 아래 별표 값은 law-mcp 색인에 없어(원문이 첨부 파일) law.go.kr 별표 PDF에서 옮겼다. 둘 다 <개정 2025. 10. 1.>판
# - 중소기업기본법 시행령 별표 1 (C30 평균매출액등 1,200억원 이하): https://www.law.go.kr/LSW/flDownload.do?flSeq=159973059
# - 하도급법 시행령 별표 1의2 (C30 연간매출액 2,000억원): https://www.law.go.kr/LSW/flDownload.do?flSeq=167754029

# 하도급법 제13조①: 목적물 수령일부터 60일 이내 지급
PAYMENT_DAYS_LIMIT = Rule("PAYMENT_DAYS_LIMIT", 60, (LawRef(ACT, "제13조", "①"),))
# 하도급법 제2조⑯: 비용이 하도급대금의 100분의 10 이상이면 주요 원재료
MAJOR_MATERIAL_RATIO = Rule("MAJOR_MATERIAL_RATIO", 0.10, (LawRef(ACT, "제2조", "⑯"),))
# 하도급법 제2조⑰: 비용이 하도급대금의 100분의 10 이상이면 주요 에너지.
# 2026-08-11 시행, 그 뒤 체결·갱신하는 하도급계약부터 적용 (부칙 <제21340호> 제2조)
MAJOR_ENERGY_RATIO = Rule("MAJOR_ENERGY_RATIO", 0.10, (LawRef(ACT, "제2조", "⑰"),), effective_from=date(2026, 8, 11))
# 하도급법 제3조④2, 시행령 제3조③: 거래 기간 90일 이내면 연동 사항을 적지 않을 수 있다
LINKAGE_EXEMPT_DAYS = Rule("LINKAGE_EXEMPT_DAYS", 90, (LawRef(ACT, "제3조", "④"), LawRef(DECREE, "제3조", "③")))
# 하도급법 제3조④3, 시행령 제3조④: 하도급대금 1억원 이하면 연동 사항을 적지 않을 수 있다
LINKAGE_EXEMPT_AMOUNT = Rule("LINKAGE_EXEMPT_AMOUNT", 100_000_000,
                             (LawRef(ACT, "제3조", "④"), LawRef(DECREE, "제3조", "④")))
# 시장 중앙값의 90% 미만이면 제4조①(부당한 하도급대금 결정) 위험으로 본다 (내부 기준)
MARKET_PRICE_FLOOR = Rule("MARKET_PRICE_FLOOR", 0.90, (LawRef(ACT, "제4조", "①"),), internal=True)
# 직전 견적보다 5% 넘게 낮으면 제4조②(일률적 인하 등) 인하 근거 확인 (내부 기준)
LAST_QUOTE_CUT = Rule("LAST_QUOTE_CUT", 0.05, (LawRef(ACT, "제4조", "②"),), internal=True)
# 중소기업기본법 시행령 제3조①1가, 별표 1: C30 평균매출액등 1,200억원 이하면 중소기업 (억 원)
SME_REVENUE_EOK = Rule("SME_REVENUE_EOK", 1200, (LawRef(SME_DECREE, "제3조", "①"),))
# 하도급법 제13조⑪, 시행령 제7조의5, 별표 1의2: C30 연간매출액 2,000억원 미만 중견기업에도 지급 조항(①~⑩) 적용 (억 원)
MIDSIZE_REVENUE_EOK = Rule("MIDSIZE_REVENUE_EOK", 2000, (LawRef(ACT, "제13조", "⑪"), LawRef(DECREE, "제7조의5")))
# 하도급법 제13조⑪: 상호출자제한기업집단 소속 회사와 거래하면 그 기준이 3천억원 (억 원)
MIDSIZE_REVENUE_LARGE_GROUP_EOK = Rule("MIDSIZE_REVENUE_LARGE_GROUP_EOK", 3000, (LawRef(ACT, "제13조", "⑪"),))
# 하도급법 제13조⑪2, 시행령 제7조의6: 위탁하는 쪽이 연간매출액 2조원 초과 사업자일 때 적용 (억 원)
BUYER_REVENUE_EOK = Rule("BUYER_REVENUE_EOK", 20000, (LawRef(ACT, "제13조", "⑪"), LawRef(DECREE, "제7조의6")))

RULES = (PAYMENT_DAYS_LIMIT, MAJOR_MATERIAL_RATIO, MAJOR_ENERGY_RATIO, LINKAGE_EXEMPT_DAYS, LINKAGE_EXEMPT_AMOUNT,
         MARKET_PRICE_FLOOR, LAST_QUOTE_CUT, SME_REVENUE_EOK, MIDSIZE_REVENUE_EOK, MIDSIZE_REVENUE_LARGE_GROUP_EOK,
         BUYER_REVENUE_EOK)

# 자주 쓰는 근거 조문
LINKAGE_TERMS = LawRef(ACT, "제3조", "②")       # 서면에 연동 사항을 적어야 한다 (②3)
LINKAGE_EXEMPTIONS = LawRef(ACT, "제3조", "④")  # 연동 사항을 적지 않아도 되는 경우 (④1~4)
SUBCONTRACTOR = LawRef(ACT, "제2조", "③")       # 수급사업자 = 중소기업자


def korean_date(day: date) -> str:
    return f"{day.year}년 {day.month}월 {day.day}일"


def law_versions_on(as_of: date) -> dict[str, str | None]:
    """as_of에 시행 중인 법령별 시행판의 시행일. 색인보다 이른 날이면 None."""
    out = {}
    for law, days in LAW_VERSIONS.items():
        in_force = [d for d in days if d <= as_of]
        out[law] = in_force[-1].isoformat() if in_force else None
    return out


def finding(code: str, severity: str, message: str, *refs: LawRef) -> dict:
    """검사 결과 한 건. law_refs의 {law, article_no}는 law-mcp get_article로 그대로 넘길 수 있다."""
    return {"code": code, "severity": severity, "message": message,
            "law_refs": [ref.as_dict() for ref in refs]}


def payment_days_exceeded(payment_days: int | None) -> bool:
    return bool(payment_days) and payment_days > PAYMENT_DAYS_LIMIT.value


def payment_findings(payment_days: int) -> list[dict]:
    if payment_days_exceeded(payment_days):
        limit = PAYMENT_DAYS_LIMIT.value
        return [finding("PAYMENT_DAYS_OVER_60", "violation",
                        f"지급 기한 {payment_days}일: 목적물 수령일부터 {limit}일 이내로 정해야 한다 "
                        "(대등한 지위에서 정했거나 업종 특수성이 인정되는 예외 제외). 초과 기간에는 지연이자가 붙는다",
                        *PAYMENT_DAYS_LIMIT.refs)]
    return []


def linkage_exemptions(contract_amount: int | None, contract_days: int | None,
                       buyer_is_small_business: bool) -> list[str]:
    """연동 사항을 적지 않아도 되는 사유 (제3조④1~3)."""
    exemptions = []
    if buyer_is_small_business:
        exemptions.append("원사업자가 소기업")
    if contract_days is not None and contract_days <= LINKAGE_EXEMPT_DAYS.value:
        exemptions.append(f"거래 기간 {contract_days}일(90일 이내)")
    if contract_amount is not None and contract_amount <= LINKAGE_EXEMPT_AMOUNT.value:
        exemptions.append(f"하도급대금 {contract_amount:,}원(1억 원 이하)")
    return exemptions


def linkage_findings(raw_material_ratio: float | None, price_linkage: bool, contract_amount: int | None,
                     contract_days: int | None, waived: bool = False, buyer_is_small_business: bool = False) -> list[dict]:
    """주요 원재료 연동 약정(하도급법 제3조②3·④, 제2조⑯) 검사."""
    if price_linkage:
        return []
    if raw_material_ratio is None:
        return [finding("RAW_MATERIAL_RATIO_UNKNOWN", "warning",
                        "원재료 비중을 몰라 연동 약정 대상인지 판단할 수 없다. 원재료 비용이 하도급대금의 10% 이상이면 "
                        "연동 사항을 서면에 적어야 한다", *MAJOR_MATERIAL_RATIO.refs, LINKAGE_TERMS)]
    if raw_material_ratio < MAJOR_MATERIAL_RATIO.value:
        return []
    pct = f"{raw_material_ratio * 100:.0f}%"
    exemptions = linkage_exemptions(contract_amount, contract_days, buyer_is_small_business)
    if exemptions:
        return [finding("LINKAGE_EXEMPT", "info",
                        f"원재료 비중 {pct}로 연동 대상이지만 예외에 해당할 수 있다: {', '.join(exemptions)}. "
                        "기간·금액 기준은 시행령 값을 확인한다", LINKAGE_EXEMPTIONS)]
    if waived:
        return [finding("LINKAGE_WAIVED_NEEDS_WRITTEN_REASON", "warning",
                        f"원재료 비중 {pct}로 연동 대상인데 연동하지 않기로 합의했다. 그 취지와 사유를 서면에 분명하게 "
                        "적어야 하고, 거래상 지위를 남용해 합의를 유도하면 안 된다", LINKAGE_EXEMPTIONS)]
    return [finding("LINKAGE_TERMS_MISSING", "violation",
                    f"원재료 비중 {pct}로 주요 원재료(10% 이상)에 해당하는데 연동 약정이 없다. 연동 대상 원재료, 조정요건, "
                    "기준 지표, 산식 등을 서면에 적어야 한다", *MAJOR_MATERIAL_RATIO.refs, LINKAGE_TERMS)]


def energy_linkage_findings(energy_ratio: float | None, price_linkage: bool, contract_amount: int | None,
                            contract_days: int | None, as_of: date, waived: bool = False,
                            buyer_is_small_business: bool = False) -> list[dict]:
    """주요 에너지 연동 약정(하도급법 제3조②3·④, 제2조⑰) 검사.

    연동 약정(price_linkage)과 연동하지 않기로 한 합의(waived)는 원재료와 에너지에 함께 적용한다.
    계약 시점이 규정 시행 전이면 검사하지 않고, 에너지 비중을 받았을 때는 그 이유를 info로 남긴다.
    """
    if price_linkage:
        return []
    if not MAJOR_ENERGY_RATIO.applies_on(as_of):
        if energy_ratio is None or MAJOR_ENERGY_RATIO.effective_from is None:
            return []
        # 근거 조문(제2조⑰)은 그 시점 시행판에 없어 law_refs를 달지 않는다. 날짜는 답변이 옮겨 적기 쉽게 한글로 쓴다
        return [finding("ENERGY_RULE_NOT_IN_FORCE", "info",
                        f"계약 시점 {korean_date(as_of)}은 주요 에너지 연동 규정 시행일"
                        f"({korean_date(MAJOR_ENERGY_RATIO.effective_from)}) 전이라 에너지 비중 "
                        f"{energy_ratio * 100:.0f}%의 연동 약정은 검사하지 않았다. 이 규정은 시행 이후 체결·갱신하는 "
                        "하도급계약부터 적용된다")]
    if energy_ratio is None:
        return [finding("ENERGY_RATIO_UNKNOWN", "info",
                        "에너지 비중을 입력하지 않아 주요 에너지 연동 검사를 건너뛰었다. 에너지 비용이 하도급대금의 "
                        "10% 이상이면 연동 사항을 서면에 적어야 한다", *MAJOR_ENERGY_RATIO.refs, LINKAGE_TERMS)]
    if energy_ratio < MAJOR_ENERGY_RATIO.value:
        return []
    pct = f"{energy_ratio * 100:.0f}%"
    exemptions = linkage_exemptions(contract_amount, contract_days, buyer_is_small_business)
    if exemptions:
        return [finding("ENERGY_LINKAGE_EXEMPT", "info",
                        f"에너지 비중 {pct}로 연동 대상이지만 예외에 해당할 수 있다: {', '.join(exemptions)}. "
                        "기간·금액 기준은 시행령 값을 확인한다", LINKAGE_EXEMPTIONS)]
    if waived:
        return [finding("LINKAGE_WAIVED_NEEDS_WRITTEN_REASON", "warning",
                        f"에너지 비중 {pct}로 연동 대상인데 연동하지 않기로 합의했다. 그 취지와 사유를 서면에 분명하게 "
                        "적어야 하고, 거래상 지위를 남용해 합의를 유도하면 안 된다", LINKAGE_EXEMPTIONS)]
    return [finding("ENERGY_LINKAGE_TERMS_MISSING", "violation",
                    f"에너지 비중 {pct}로 주요 에너지(10% 이상)에 해당하는데 연동 약정이 없다. 연동 대상 에너지, 조정요건, "
                    "기준 지표, 산식 등을 서면에 적어야 한다", *MAJOR_ENERGY_RATIO.refs, LINKAGE_TERMS)]


def price_findings(unit_price: int, market_median: float | None, market_n: int,
                   last_price: int | None, target_price: int) -> list[dict]:
    """단가가 일반적으로 지급되는 대가보다 낮은지 (제4조①, ②5 일방적으로 낮은 단가, ②1 일률적 인하)."""
    out = []
    if market_median and unit_price < market_median * MARKET_PRICE_FLOOR.value:
        out.append(finding(
            "PRICE_BELOW_MARKET", "warning",
            f"단가 {unit_price:,}원이 같은 공정·소재 최근 견적 중앙값 {market_median:,.0f}원({market_n}곳)의 "
            f"{unit_price / market_median * 100:.0f}%다. 일반적으로 지급되는 대가보다 낮게 정하면 부당한 하도급대금 "
            "결정이 될 수 있으니 원가 근거와 협의 기록을 남긴다", *MARKET_PRICE_FLOOR.refs))
    if last_price and unit_price < last_price * (1 - LAST_QUOTE_CUT.value):
        out.append(finding(
            "PRICE_CUT_FROM_LAST_QUOTE", "warning",
            f"이 협력사의 직전 견적 {last_price:,}원보다 {(1 - unit_price / last_price) * 100:.1f}% 낮다. "
            "정당한 사유 없는 일률적 인하나 일방적으로 낮은 단가 결정이 되지 않도록 인하 근거를 확인한다",
            *LAST_QUOTE_CUT.refs))
    if unit_price > target_price:
        out.append(finding("ABOVE_TARGET_PRICE", "info",
                           f"단가가 RFQ 목표 단가 {target_price:,}원보다 "
                           f"{(unit_price / target_price - 1) * 100:.1f}% 높다"))
    return out


def sme_findings(revenue_eok: float | None) -> list[dict]:
    """하도급법 적용 대상(수급사업자 = 중소기업자)인지, 중견기업이면 지급 조항 확대 적용(제13조⑪) 대상인지."""
    sme = SME_REVENUE_EOK.refs[0]
    if revenue_eok is None:
        return [finding("SME_STATUS_UNKNOWN", "info",
                        "매출 정보가 없어 수급사업자(중소기업자) 해당 여부를 판단할 수 없다", SUBCONTRACTOR)]
    if revenue_eok <= SME_REVENUE_EOK.value:
        return [finding("SUBCONTRACT_ACT_APPLIES", "info",
                        f"최근 매출 {revenue_eok:,}억 원으로 중소기업일 가능성이 높아 하도급법상 수급사업자로 보고 검사했다 "
                        f"(자동차 및 트레일러 제조업 중소기업 기준 평균매출액 {SME_REVENUE_EOK.value:,}억 원 이하, "
                        "중소기업기본법 시행령 별표 1)", SUBCONTRACTOR, sme)]
    out = [finding("SME_STATUS_CHECK", "info",
                   f"최근 매출 {revenue_eok:,}억 원으로 중소기업 기준(자동차 및 트레일러 제조업 평균매출액 "
                   f"{SME_REVENUE_EOK.value:,}억 원 이하)을 넘어 수급사업자가 아닐 수 있다", SUBCONTRACTOR, sme)]
    midsize, large_group, buyer = (MIDSIZE_REVENUE_EOK.value, MIDSIZE_REVENUE_LARGE_GROUP_EOK.value,
                                   BUYER_REVENUE_EOK.value)
    if revenue_eok < midsize:
        condition = (f"우리(원사업자)가 상호출자제한기업집단 소속이거나 연간매출액 {buyer // 10000:,}조 원을 넘으면")
    elif revenue_eok < large_group:
        condition = "우리(원사업자)가 상호출자제한기업집단 소속이면"
    else:
        return out
    out.append(finding("MIDSIZE_PAYMENT_RULES_MAY_APPLY", "info",
                       f"최근 매출 {revenue_eok:,}억 원으로, 이 협력사가 중견기업이고 {condition} 하도급법 제13조⑪에 따라 "
                       "대금 지급 조항(①~⑩, 60일 이내 지급 등)이 그대로 적용된다 (자동차 및 트레일러 제조업 기준 "
                       f"{midsize:,}억 원 미만, 상호출자제한기업집단과 거래하면 {large_group:,}억 원 미만)",
                       LawRef(ACT, "제13조", "⑪"), LawRef(DECREE, "제7조의5"), LawRef(DECREE, "제7조의6")))
    return out


def verdict_code(findings: list[dict]) -> str:
    severities = {f["severity"] for f in findings}
    return "VIOLATION" if "violation" in severities else "CHECK_NEEDED" if "warning" in severities else "OK"
