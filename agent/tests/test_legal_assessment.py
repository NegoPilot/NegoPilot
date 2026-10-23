from datetime import date

import pytest
from pydantic import ValidationError

from negopilot_agent.legal_assessment import assess_transaction
from negopilot_shared.transaction import TransactionFacts


def complete_facts(**changes):
    return {
        "transaction_type": "manufacturing", "party_requirements_confirmed": True,
        "price_scope": "new_order", "contracted_on": "2026-10-01",
        "actual_contract_amount": 200_000_000, "payment_basis": "receipt",
        "upstream_payment_applicable": False,
        "linkage_details": [{"category": "material", "name": "구리", "cost_ratio": 0.3,
            "indicator": "합의한 공개 구리 지표", "formula": "기준단가 + 대상원가 × 지표변동률",
            "trigger_ratio": 0.03, "frequency": "월별", "reference_period": "전월 평균",
            "reflection_date": "조정 다음달 납품분"}],
        **changes,
    }


TERMS = {"unit_price": 10000, "payment_days": 30, "price_linkage": True, "contract_days": 365}


def assess(facts=None, **terms):
    return assess_transaction(facts, TERMS | terms, date(2026, 10, 23))


def finding(result, key):
    return next(f for f in result["findings"] if f["id"] == key)


def test_absent_facts_never_become_approval():
    result = assess()
    assert result["status"] == "needs_information"
    assert "transaction_type" in result["missing_fields"]
    assert "linkage_details" in result["missing_fields"]


def test_complete_future_proposal_does_not_invent_receipt_date():
    result = assess(complete_facts())
    assert result["status"] == "ready"
    assert not any(c["type"] == "proposed_payment_date" for c in result["constraints"])
    assert "인증" in result["notice"]


def test_upstream_payment_requires_earlier_deadline():
    result = assess(complete_facts(receipt_date="2026-10-01", upstream_payment_applicable=True,
                                  upstream_received_on="2026-10-05"))
    assert result["status"] == "blocked"
    assert {"type": "latest_payment_date", "value": "2026-10-20"} in result["constraints"]
    assert {"type": "proposed_payment_date", "value": "2026-10-31"} in result["constraints"]


def test_agreed_earlier_due_date_is_not_replaced_by_60_day_baseline():
    result = assess(complete_facts(receipt_date="2026-10-01", agreed_due_date="2026-10-10",
                                  upstream_payment_applicable=True, upstream_received_on="2026-10-05"))
    assert result["status"] == "blocked"
    assert {"type": "latest_payment_date", "value": "2026-10-10"} in result["constraints"]
    assert {"type": "proposed_payment_date", "value": "2026-10-31"} in result["constraints"]


def test_unknown_applicability_is_not_a_definitive_violation():
    result = assess(complete_facts(party_requirements_confirmed=None), payment_days=90)
    assert result["status"] == "needs_information"
    assert finding(result, "payment.deadline")["status"] == "needs_information"


def test_same_reduction_treated_differently_for_existing_orders():
    new = assess(complete_facts(previous_unit_price=11000))
    existing = assess(complete_facts(price_scope="existing_order", previous_unit_price=11000))
    assert finding(new, "price.scope")["status"] == "supported"
    assert "reduction_evidence" in existing["missing_fields"]
    assert "reduction_justification_review" in existing["missing_fields"]


def test_supplied_reduction_documents_do_not_certify_justification():
    result = assess(complete_facts(price_scope="existing_order", previous_unit_price=11000,
                                  reduction_reason="합의", reduction_evidence=["agreement.pdf"]))
    assert "reduction_evidence" not in result["missing_fields"]
    assert "reduction_justification_review" in result["missing_fields"]


def test_linkage_checkbox_cannot_replace_agreement_terms():
    result = assess(complete_facts(linkage_details=[]))
    assert finding(result, "linkage.agreement")["status"] == "needs_information"
    assert "linkage_details" in result["missing_fields"]


def test_estimated_amount_not_used_for_exception():
    result = assess(complete_facts(actual_contract_amount=None), price_linkage=False,
                    contract_amount=50_000_000, annual_volume=1)
    assert "actual_contract_amount" in result["missing_fields"]
    assert "linkage_exception_legal_review" in result["missing_fields"]


def test_contract_date_selects_energy_transition_review():
    facts = complete_facts(contracted_on="2026-07-01")
    facts["linkage_details"][0]["category"] = "energy"
    assert "energy_linkage_transition_review" in assess(facts)["missing_fields"]
    facts["renewed_on"] = "2026-10-01"
    assert "energy_linkage_transition_review" not in assess(facts)["missing_fields"]


def test_duration_never_implies_volume_guarantee():
    result = assess(complete_facts(), contract_days=730)
    assert {"type": "no_volume_guarantee_without_commitment", "committed_quantity": None} in result["constraints"]


def test_strict_keys_and_ratio_units():
    with pytest.raises(ValidationError):
        TransactionFacts.model_validate({"unknown_fact": 1})
    facts = complete_facts()
    facts["linkage_details"][0]["cost_ratio"] = 30
    with pytest.raises(ValidationError):
        TransactionFacts.model_validate(facts)


@pytest.mark.parametrize("field,value", [("party_requirements_confirmed", "true"),
    ("upstream_payment_applicable", 1), ("actual_contract_amount", "1000"),
    ("committed_quantity", True), ("previous_unit_price", 1000.5),
    ("payment_exception_claimed", "false")])
def test_numeric_and_boolean_facts_do_not_coerce(field, value):
    with pytest.raises(ValidationError):
        TransactionFacts.model_validate({field: value})


def test_material_agreement_does_not_cover_major_energy():
    result = assess(complete_facts(), raw_material_ratio=0.3, energy_ratio=0.2)
    assert "linkage_details.energy" in result["missing_fields"]
    assert "linkage_details.material" not in result["missing_fields"]


def test_future_contract_date_remains_conditional():
    result = assess(complete_facts(contracted_on="2026-12-01"))
    assert result["status"] == "needs_information"
    assert "future_contract_date_review" in result["missing_fields"]


def test_claimed_payment_exception_requires_review_even_with_evidence():
    result = assess(complete_facts(payment_exception_claimed=True, payment_exception_reason="대등한 지위 주장",
                                  payment_exception_evidence=["negotiation.pdf"]), payment_days=90)
    assert result["status"] == "needs_information"
    assert "payment_exception_legal_review" in result["missing_fields"]
    assert "negotiation.pdf" in finding(result, "payment.deadline")["evidence_refs"]
    assert "negotiation.pdf" not in finding(result, "price.scope")["evidence_refs"]


def test_modifying_proposal_payment_term_rechecks_existing_agreed_due_date():
    facts = complete_facts(receipt_date="2026-10-01", agreed_due_date="2026-10-20")
    assert assess(facts, payment_days=15)["status"] == "ready"
    assert assess(facts, payment_days=30)["status"] == "blocked"


LAW_VERSION = {"law_key": "subcontract_act", "law_type": "법률", "in_force_effective_date": "2026-10-02",
               "in_force_promulgation_no": "21857", "source_url": "https://www.law.go.kr/법령/하도급거래공정화에관한법률"}


@pytest.mark.parametrize("as_of", ["2026-10-23", "2026-10-24", "2027-01-15"])
def test_source_version_comes_from_law_mcp_for_any_date(as_of):
    """판본은 law-mcp가 기준일에 시행 중이라고 돌려준 판에서 채운다. 날짜를 하드코딩하지 않는다 (#261)."""
    result = assess_transaction(complete_facts(), TERMS, as_of, LAW_VERSION)
    ref = result["findings"][0]["law_refs"][0]
    assert ref["version"] == "법률 제21857호, 시행 2026-10-02"
    assert ref["source_url"] == LAW_VERSION["source_url"] and ref["as_of"] == as_of
    assert "linkage_details" not in result["findings"][0]["facts"]


def test_version_is_not_invented_without_law_mcp():
    ref = assess_transaction(complete_facts(), TERMS, "2026-10-23")["findings"][0]["law_refs"][0]
    assert ref["version"] == "기준일 판본 확인 실패" and ref["source_url"] is None
