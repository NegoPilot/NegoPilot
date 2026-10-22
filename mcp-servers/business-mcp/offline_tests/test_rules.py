"""rules.py의 법 규칙을 DB 없이 경계값으로 검증하고, 근거 조문을 law-mcp 색인과 대조한다.
tests/conftest.py를 사용하지 않는다.
"""

import json
from datetime import date
from pathlib import Path

import pytest

import rules

LAW_INDEX = Path(__file__).resolve().parents[2] / "law-mcp" / "data" / "index"
AFTER_ENERGY = date(2026, 10, 22)
BEFORE_ENERGY = date(2026, 8, 10)


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


def test_sme_boundary_is_1200_eok_for_auto_parts():
    assert codes(rules.sme_findings(None)) == ["SME_STATUS_UNKNOWN"]
    assert codes(rules.sme_findings(1200)) == ["SUBCONTRACT_ACT_APPLIES"]
    assert codes(rules.sme_findings(1201))[0] == "SME_STATUS_CHECK"


def test_midsize_payment_rules_boundaries():
    """제13조⑪: C30 2,000억 미만이면 대기업집단 또는 매출 2조 초과 원사업자, 3천억 미만이면 대기업집단만."""
    may_apply = "MIDSIZE_PAYMENT_RULES_MAY_APPLY"
    [_, below] = rules.sme_findings(1999)
    assert below["code"] == may_apply and "2조 원" in below["message"]
    [_, middle] = rules.sme_findings(2000)
    assert middle["code"] == may_apply and "2조 원" not in middle["message"]
    assert codes(rules.sme_findings(2999)) == ["SME_STATUS_CHECK", may_apply]
    assert codes(rules.sme_findings(3000)) == ["SME_STATUS_CHECK"]
    assert {"law": "subcontract_act", "article_no": "제13조", "paragraph": "⑪"} in below["law_refs"]


def energy(ratio, amount=500_000_000, days=365, waived=False, small=False, linkage=False, as_of=AFTER_ENERGY):
    return codes(rules.energy_linkage_findings(ratio, linkage, amount, days, as_of, waived, small))


def test_major_energy_ratio_boundary_is_10_percent():
    assert energy(0.0999) == []
    assert energy(0.10) == ["ENERGY_LINKAGE_TERMS_MISSING"]


def test_energy_linkage_exemptions_and_agreements():
    assert energy(0.12, days=90) == ["ENERGY_LINKAGE_EXEMPT"]
    assert energy(0.12, amount=100_000_000) == ["ENERGY_LINKAGE_EXEMPT"]
    assert energy(0.12, small=True) == ["ENERGY_LINKAGE_EXEMPT"]
    assert energy(0.12, waived=True) == ["LINKAGE_WAIVED_NEEDS_WRITTEN_REASON"]
    assert energy(0.12, linkage=True) == []
    assert energy(None) == ["ENERGY_RATIO_UNKNOWN"]
    assert energy(None, linkage=True) == []


def test_energy_rule_starts_on_2026_08_11():
    assert energy(0.12, as_of=BEFORE_ENERGY) == ["ENERGY_RULE_NOT_IN_FORCE"]
    assert energy(None, as_of=BEFORE_ENERGY) == []
    assert energy(0.12, as_of=BEFORE_ENERGY, linkage=True) == []
    assert energy(0.12, as_of=date(2026, 8, 11)) == ["ENERGY_LINKAGE_TERMS_MISSING"]


def test_skipped_energy_check_explains_why_without_citing_a_later_law():
    [skipped] = rules.energy_linkage_findings(0.15, False, 500_000_000, 365, date(2026, 7, 1))
    assert skipped["severity"] == "info" and skipped["law_refs"] == []
    assert "2026년 7월 1일" in skipped["message"] and "2026년 8월 11일" in skipped["message"]
    assert "15%" in skipped["message"]


def test_verdict_code():
    assert rules.verdict_code([]) == "OK"
    assert rules.verdict_code(rules.sme_findings(None)) == "OK"
    assert rules.verdict_code(rules.price_findings(1, 10_000, 3, None, 20_000)) == "CHECK_NEEDED"
    assert rules.verdict_code(rules.payment_findings(61) + rules.price_findings(1, 10_000, 3, None, 20_000)) == "VIOLATION"


def test_law_versions_on():
    assert rules.law_versions_on(date(2026, 10, 22)) == {"subcontract_act": "2026-10-02",
                                                         "subcontract_decree": "2026-08-11", "sme_decree": "2026-01-02"}
    assert rules.law_versions_on(date(2026, 10, 1)) == {"subcontract_act": "2026-08-11",
                                                        "subcontract_decree": "2026-08-11", "sme_decree": "2026-01-02"}
    assert rules.law_versions_on(date(2024, 1, 1)) == {"subcontract_act": None, "subcontract_decree": None,
                                                       "sme_decree": None}


def test_law_refs_include_paragraph_only_when_known():
    [payment] = rules.payment_findings(61)
    assert payment["law_refs"] == [{"law": "subcontract_act", "article_no": "제13조", "paragraph": "①"}]
    [_, midsize] = rules.sme_findings(1500)
    assert {"law": "subcontract_decree", "article_no": "제7조의5"} in midsize["law_refs"]


# ---------- law-mcp 색인 대조 ----------

def load_index(law: str) -> dict:
    return json.loads((LAW_INDEX / f"{law}.json").read_text(encoding="utf-8"))


def versions_while_rule_applies(rule: rules.Rule, law: str) -> list[dict]:
    """규칙이 적용되는 기간에 시행된 판. 기간 시작일에 이미 시행 중이던 판도 포함한다."""
    versions = sorted(load_index(law)["versions"], key=lambda v: v["effective_date"])
    out = []
    for i, v in enumerate(versions):
        start = date.fromisoformat(v["effective_date"])
        end = date.fromisoformat(versions[i + 1]["effective_date"]) if i + 1 < len(versions) else None
        if (rule.effective_to is None or start < rule.effective_to) and (
                rule.effective_from is None or end is None or end > rule.effective_from):
            out.append(v)
    return out


def paragraph_text(version: dict, ref: rules.LawRef) -> str:
    article = next(a for a in version["articles"] if a["no"] == ref.article_no)
    if ref.paragraph is None:
        return article["text"]
    return next(p["text"] for p in article["paragraphs"] if p["no"] == ref.paragraph)


# 인용한 항에 있어야 하는 기준값 표기
EXPECTED_TEXT = {
    ("PAYMENT_DAYS_LIMIT", "subcontract_act", "제13조", "①"): ["60일"],
    ("MAJOR_MATERIAL_RATIO", "subcontract_act", "제2조", "⑯"): ["주요 원재료", "100분의 10"],
    ("MAJOR_ENERGY_RATIO", "subcontract_act", "제2조", "⑰"): ["주요 에너지", "100분의 10"],
    ("LINKAGE_EXEMPT_DAYS", "subcontract_act", "제3조", "④"): ["90일"],
    ("LINKAGE_EXEMPT_DAYS", "subcontract_decree", "제3조", "③"): ["90일"],
    ("LINKAGE_EXEMPT_AMOUNT", "subcontract_act", "제3조", "④"): ["1억 원"],
    ("LINKAGE_EXEMPT_AMOUNT", "subcontract_decree", "제3조", "④"): ["1억원"],
    # 별표 값(1,200억, 2,000억)은 색인에 없어 별표를 가리키는 조문까지만 대조한다
    ("SME_REVENUE_EOK", "sme_decree", "제3조", "①"): ["별표 1"],
    ("MIDSIZE_REVENUE_EOK", "subcontract_act", "제13조", "⑪"): ["중견기업", "대통령령으로 정하는 금액"],
    ("MIDSIZE_REVENUE_EOK", "subcontract_decree", "제7조의5", None): ["제13조제11항", "별표 1의2"],
    ("MIDSIZE_REVENUE_LARGE_GROUP_EOK", "subcontract_act", "제13조", "⑪"): ["3천억원", "상호출자제한기업집단"],
    ("BUYER_REVENUE_EOK", "subcontract_act", "제13조", "⑪"): ["연간매출액이 대통령령으로 정하는 금액을 초과"],
    ("BUYER_REVENUE_EOK", "subcontract_decree", "제7조의6", None): ["제13조제11항제2호", "2조원"],
}


def test_every_law_rule_value_is_checked_against_the_index():
    law_refs = {(rule.code, ref.law, ref.article_no, ref.paragraph)
                for rule in rules.RULES if not rule.internal for ref in rule.refs}
    assert law_refs == set(EXPECTED_TEXT)


@pytest.mark.parametrize("key", sorted(EXPECTED_TEXT))
def test_cited_paragraph_contains_the_rule_value(key):
    code, law, article_no, paragraph = key
    rule = next(r for r in rules.RULES if r.code == code)
    versions = versions_while_rule_applies(rule, law)
    assert versions
    for version in versions:
        text = paragraph_text(version, rules.LawRef(law, article_no, paragraph))
        for expected in EXPECTED_TEXT[key]:
            assert expected in text, (version["effective_date"], key, expected)


def test_every_cited_article_and_paragraph_exists_in_the_index():
    refs = {ref for rule in rules.RULES for ref in rule.refs}
    refs |= {rules.LINKAGE_TERMS, rules.LINKAGE_EXEMPTIONS, rules.SUBCONTRACTOR}
    latest = {law: max(load_index(law)["versions"], key=lambda v: v["effective_date"]) for law in rules.LAW_VERSIONS}
    for ref in refs:
        assert paragraph_text(latest[ref.law], ref), ref


def test_law_versions_match_the_index():
    for law, days in rules.LAW_VERSIONS.items():
        assert [d.isoformat() for d in days] == sorted(v["effective_date"] for v in load_index(law)["versions"])


def test_energy_rule_starts_when_the_definition_appears():
    versions = sorted(load_index("subcontract_act")["versions"], key=lambda v: v["effective_date"])
    first = next(v for v in versions if "주요 에너지" in paragraph_text(v, rules.LawRef("subcontract_act", "제2조")))
    assert date.fromisoformat(first["effective_date"]) == rules.MAJOR_ENERGY_RATIO.effective_from


def test_every_rule_cites_a_paragraph_when_the_article_has_paragraphs():
    latest = {law: max(load_index(law)["versions"], key=lambda v: v["effective_date"]) for law in rules.LAW_VERSIONS}
    for rule in rules.RULES:
        assert rule.refs, rule.code
        for ref in rule.refs:
            article = next(a for a in latest[ref.law]["articles"] if a["no"] == ref.article_no)
            if not rule.internal and any(p["no"] for p in article["paragraphs"]):
                assert ref.paragraph, (rule.code, ref)


def test_rules_apply_today():
    for rule in rules.RULES:
        assert rule.applies_on(AFTER_ENERGY), rule.code
