"""rules.py의 법 규칙을 DB 없이 경계값으로 검증한다. tests/conftest.py를 사용하지 않는다."""

from datetime import date

import rules


def codes(findings: list[dict]) -> list[str]:
    return [f["code"] for f in findings]


def test_payment_days_boundary_is_60():
    assert payment_codes(60) == []
    assert payment_codes(61) == ["PAYMENT_DAYS_OVER_60"]
    assert rules.payment_days_exceeded(None) is False


def payment_codes(days: int) -> list[str]:
    return codes(rules.payment_findings(days))


def linkage(ratio, amount=500_000_000, days=365, waived=False, small=False, linkage=False):
    return codes(rules.linkage_findings(ratio, linkage, amount, days, waived, small))


def test_major_material_ratio_boundary_is_10_percent():
    assert linkage(0.0999) == []
    assert linkage(0.10) == ["LINKAGE_TERMS_MISSING"]


def test_linkage_agreement_or_unknown_ratio():
    assert linkage(0.4, linkage=True) == []
    assert linkage(None) == ["RAW_MATERIAL_RATIO_UNKNOWN"]
    assert linkage(0.4, waived=True) == ["LINKAGE_WAIVED_NEEDS_WRITTEN_REASON"]


def test_linkage_exemption_period_boundary_is_90_days():
    assert linkage(0.4, days=90) == ["LINKAGE_EXEMPT"]
    assert linkage(0.4, days=91) == ["LINKAGE_TERMS_MISSING"]


def test_linkage_exemption_amount_boundary_is_100_million_won():
    assert linkage(0.4, amount=100_000_000) == ["LINKAGE_EXEMPT"]
    assert linkage(0.4, amount=100_000_001) == ["LINKAGE_TERMS_MISSING"]


def test_small_business_buyer_is_exempt():
    assert linkage(0.4, small=True) == ["LINKAGE_EXEMPT"]


def test_exemption_wins_over_waiver():
    assert linkage(0.4, days=60, waived=True) == ["LINKAGE_EXEMPT"]


def test_price_rules():
    assert codes(rules.price_findings(9_000, 10_000, 3, None, 20_000)) == []
    assert codes(rules.price_findings(8_999, 10_000, 3, None, 20_000)) == ["PRICE_BELOW_MARKET"]
    assert codes(rules.price_findings(9_500, None, 0, 10_000, 20_000)) == []
    assert codes(rules.price_findings(9_499, None, 0, 10_000, 20_000)) == ["PRICE_CUT_FROM_LAST_QUOTE"]
    assert codes(rules.price_findings(10_001, None, 0, None, 10_000)) == ["ABOVE_TARGET_PRICE"]


def test_sme_rules():
    assert codes(rules.sme_findings(None)) == ["SME_STATUS_UNKNOWN"]
    assert codes(rules.sme_findings(1000)) == ["SUBCONTRACT_ACT_APPLIES"]
    assert codes(rules.sme_findings(1001)) == ["SME_STATUS_CHECK"]


def test_every_rule_has_a_paragraph_level_law_ref_unless_internal():
    for rule in rules.RULES:
        assert rule.refs, rule.code
        if not rule.internal:
            assert all(ref.paragraph for ref in rule.refs), rule.code


def test_rules_apply_without_an_end_date():
    for rule in rules.RULES:
        assert rule.applies_on(date(2026, 10, 22)), rule.code
