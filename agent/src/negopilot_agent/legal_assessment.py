"""Conservative, deterministic links from declared facts to proposal constraints.

This is a completeness/conditional-risk assessment, not a legal opinion. Evidence
references are supplied by the user and are not authenticated here.
"""
from datetime import date, timedelta
from typing import Any

from negopilot_shared.transaction import TransactionFacts

LAW_URL = "https://www.law.go.kr/법령/하도급거래공정화에관한법률"
HISTORICAL_LAW_URL = "https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq=288625"


def assess_transaction(
    facts: TransactionFacts | dict | None, terms: dict, as_of: date | str
) -> dict[str, Any]:
    facts = facts if isinstance(facts, TransactionFacts) else TransactionFacts.model_validate(facts or {})
    as_of = date.fromisoformat(as_of) if isinstance(as_of, str) else as_of
    snapshot = facts.model_dump(mode="json")
    findings: list[dict] = []

    def add(key, fields, summary, *, missing=(), constraints=(), blocked=False, articles=()):
        relevant = {
            "transaction.applicability": ["transaction_type", "party_requirements_confirmed", "contracted_on", "renewed_on"],
            "price.scope": ["price_scope", "previous_unit_price", "reduction_reason"],
            "payment.deadline": ["payment_basis", "receipt_date", "completion_date", "monthly_agreed_date", "agreed_due_date",
                                 "upstream_received_on", "upstream_payment_applicable", "payment_exception_claimed", "payment_exception_reason"],
            "linkage.agreement": ["linkage_details", "actual_contract_amount", "contracted_on", "renewed_on", "waiver_reason"],
            "duration.commitment": ["committed_quantity", "actual_contract_amount", "contracted_on", "renewed_on"],
        }
        extra_evidence = {
            "price.scope": facts.reduction_evidence,
            "payment.deadline": facts.payment_exception_evidence,
            "linkage.agreement": facts.waiver_evidence + [ref for detail in facts.linkage_details for ref in detail.evidence_references],
        }.get(key, [])
        findings.append({
            "id": key, "status": "blocked" if blocked else "needs_information" if missing else "supported",
            "affected_fields": list(fields), "summary": summary,
            "facts": {name: snapshot[name] for name in relevant[key]},
            "evidence_refs": list(dict.fromkeys(facts.evidence_references + extra_evidence)),
            "law_refs": [{"title": f"하도급법 제{article}조", "url": f"{LAW_URL}/제{article}조",
                          "source_url": HISTORICAL_LAW_URL if as_of == date(2026, 10, 23) else None,
                          "version": "법률 제21857호, 시행 2026-10-02" if as_of == date(2026, 10, 23) else "검토 기준일별 원문 대조 필요",
                          "as_of": as_of.isoformat()}
                         for article in articles],
            "missing_fields": list(missing), "constraints": list(constraints),
        })

    applicability_missing = []
    if facts.transaction_type != "manufacturing":
        applicability_missing.append("transaction_type")
    if facts.party_requirements_confirmed is not True:
        applicability_missing.append("party_requirements_confirmed")
    if not (facts.renewed_on or facts.contracted_on):
        applicability_missing.append("contracted_on")
    if any(value and value > as_of for value in (facts.contracted_on, facts.renewed_on)):
        applicability_missing.append("future_contract_date_review")
    transaction_label = {"manufacturing": "제조위탁", "purchase": "단순 구매", "unknown": "거래유형 미확인"}[facts.transaction_type]
    applicability_summary = f"입력 거래유형: {transaction_label}. "
    applicability_summary += "거래유형·당사자 요건·계약 시점 중 추가 확인이 필요한 항목이 있습니다." if applicability_missing else "사용자가 확인한 당사자 요건과 계약 시점을 전제로 조건부 검토합니다."
    if facts.transaction_type == "purchase":
        applicability_summary += " 단순 구매의 적용법은 별도 검토해야 합니다."
    add("transaction.applicability", ["unit_price", "payment_days", "price_linkage", "contract_days"], applicability_summary,
        missing=applicability_missing, articles=[2],
        constraints=[{"type": "conditional_assessment", "evidence_verified": False}])

    price_missing = []
    if facts.price_scope == "unknown":
        price_missing.append("price_scope")
    if facts.price_scope == "existing_order":
        if facts.previous_unit_price is None:
            price_missing.append("previous_unit_price")
        elif terms.get("unit_price", facts.previous_unit_price) < facts.previous_unit_price:
            if not facts.reduction_reason or not facts.reduction_reason.strip():
                price_missing.append("reduction_reason")
            if not facts.reduction_evidence:
                price_missing.append("reduction_evidence")
            # Documents permit review, not automatic approval of a retrospective reduction.
            price_missing.append("reduction_justification_review")
    if facts.price_scope == "new_order":
        price_summary = "신규 발주 가격 검토입니다. 제안 단가는 비교 사양·물량·협의 경위를 확인해야 하며, 시장가·직전 견적 비율은 법적 안전기준이 아닙니다."
    elif facts.price_scope == "existing_order":
        previous = f"{facts.previous_unit_price:,}원" if facts.previous_unit_price is not None else "미확인"
        proposed = f"{terms['unit_price']:,}원" if terms.get("unit_price") is not None else "미확인"
        price_summary = f"기존 발주 가격 검토: 기존 단가 {previous}, 제안 단가 {proposed}. "
        price_summary += "감액 여부 또는 정당성에 대한 추가 확인이 필요합니다." if price_missing else "입력된 기존 단가보다 감액되지 않았습니다. 다른 가격 결정 쟁점은 별도 확인 대상입니다."
    else:
        price_summary = "신규 발주인지 기존 발주분 변경인지 미확인입니다. 가격 검토에 적용할 쟁점을 먼저 구분해야 합니다."
    add("price.scope", ["unit_price"], price_summary,
        missing=price_missing, articles=[4, 11],
        constraints=[{"type": "no_automatic_retrospective_reduction"}, {"type": "benchmark_is_commercial_only"}])

    payment_missing = []
    if facts.payment_basis == "unknown":
        payment_missing.append("payment_basis")
    if facts.upstream_payment_applicable is None:
        payment_missing.append("upstream_payment_applicable")
    if facts.upstream_payment_applicable is True and not facts.upstream_received_on:
        payment_missing.append("upstream_received_on")
    if facts.payment_exception_claimed:
        payment_missing.append("payment_exception_legal_review")
        if not facts.payment_exception_reason or not facts.payment_exception_reason.strip():
            payment_missing.append("payment_exception_reason")
        if not facts.payment_exception_evidence:
            payment_missing.append("payment_exception_evidence")
    base = {"receipt": facts.receipt_date, "completion": facts.completion_date,
            "monthly_agreed": facts.monthly_agreed_date}.get(facts.payment_basis)
    days = terms.get("payment_days")
    due = base + timedelta(days=days) if base and days is not None else facts.agreed_due_date
    limits = []
    if base:
        limits.append(base + timedelta(days=60))
    if facts.upstream_payment_applicable and facts.upstream_received_on:
        limits.append(facts.upstream_received_on + timedelta(days=15))
    if facts.agreed_due_date:
        limits.append(facts.agreed_due_date)
    deadline = min(limits) if limits else None
    # A proposed future term need not invent a receipt date; an already fixed due date does.
    if facts.agreed_due_date and base is None:
        payment_missing.append({"receipt": "receipt_date", "completion": "completion_date",
                                "monthly_agreed": "monthly_agreed_date"}.get(facts.payment_basis, "payment_basis"))
    if facts.upstream_payment_applicable and deadline and due is None:
        payment_missing.append("payment_base_date")
    violation = (days is not None and days > 60) or bool(deadline and due and due > deadline)
    payment_constraints = [{"type": "payment_baseline", "max_days": 60,
                            "basis": facts.payment_basis, "conditional_on_applicability": True}]
    if deadline:
        payment_constraints.append({"type": "latest_payment_date", "value": deadline.isoformat()})
    if due:
        payment_constraints.append({"type": "proposed_payment_date", "value": due.isoformat()})
    basis_label = {"receipt": "목적물 수령일", "completion": "용역 완료일", "monthly_agreed": "월별 합의 기준일", "unknown": "미확인 기산점"}[facts.payment_basis]
    payment_summary = f"지급 제안: {basis_label}부터 {days if days is not None else '미확인'}일."
    if due:
        payment_summary += f" 계산된 지급 예정일은 {due.isoformat()}입니다."
    if deadline:
        payment_summary += f" 입력 사실 기준 지급 한도는 {deadline.isoformat()}입니다."
    if facts.payment_exception_claimed:
        payment_summary += " 지급기한 예외 주장이 있어 사유·증빙과 법적 적용을 별도 검토해야 합니다."
    elif violation:
        payment_summary += " 제안이 조건부 지급 기준을 넘으므로 적용 요건 확인 또는 지급 조건 조정이 필요합니다."
    elif payment_missing:
        payment_summary += " 기산점 또는 상위 발주대금 수령 사실에 대한 추가 확인이 필요합니다."
    else:
        payment_summary += " 입력된 지급 조건을 기준으로 검토했으며, 가능한 짧은 기한을 합의해야 합니다."
    add("payment.deadline", ["payment_days"], payment_summary,
        missing=payment_missing, constraints=payment_constraints,
        blocked=violation and not applicability_missing and not facts.payment_exception_claimed,
        articles=[13])
    if violation and applicability_missing:
        findings[-1]["missing_fields"] = sorted(set(payment_missing + ["transaction_applicability"]))
        findings[-1]["status"] = "needs_information"

    linkage_missing = []
    if terms.get("price_linkage") is True:
        if not facts.linkage_details:
            linkage_missing.append("linkage_details")
        for category, ratio_field in (("material", "raw_material_ratio"), ("energy", "energy_ratio")):
            ratio = terms.get(ratio_field)
            if ratio is not None and ratio >= 0.1 and not any(detail.category == category for detail in facts.linkage_details):
                linkage_missing.append(f"linkage_details.{category}")
        for index, detail in enumerate(facts.linkage_details):
            for field in ("cost_ratio", "indicator", "formula", "trigger_ratio", "frequency", "reference_period", "reflection_date"):
                value = getattr(detail, field)
                if value is None or (isinstance(value, str) and not value.strip()):
                    linkage_missing.append(f"linkage_details.{index}.{field}")
    else:
        # Actual amounts are never substituted with annual-volume estimates.
        if facts.actual_contract_amount is None:
            linkage_missing.append("actual_contract_amount")
        if not facts.waiver_reason or not facts.waiver_evidence:
            linkage_missing.append("linkage_exception_or_waiver_review")
        # A stated waiver or numeric threshold is not proof of a lawful exception.
        linkage_missing.append("linkage_exception_legal_review")
    if terms.get("price_linkage") is True:
        names = ", ".join(detail.name for detail in facts.linkage_details)
        linkage_summary = f"연동 제안에 {len(facts.linkage_details)}개 대상" + (f"({names})" if names else "") + "이 입력되었습니다. "
        linkage_summary += "대상별 약정 세부항목 또는 주요 원가 대상이 부족하여 추가 확인이 필요합니다." if linkage_missing else "대상별 지표·산식·조정요건·주기·반영시점이 입력되었습니다. 내용의 적정성과 실제 합의 여부는 별도 검토 대상입니다."
    else:
        linkage_summary = "미연동 제안입니다. 실제 계약금액·기간 및 예외 또는 미연동 합의 사유에 대한 검토가 필요합니다."
    add("linkage.agreement", ["price_linkage", "unit_price", "contract_days"], linkage_summary,
        missing=linkage_missing, articles=[2, 3],
        constraints=[{"type": "linkage_details_required"}, {"type": "no_estimated_amount_exemption"}])
    effective = facts.renewed_on or facts.contracted_on
    if effective and effective < date(2023, 10, 4):
        findings[-1]["missing_fields"].append("legacy_contract_linkage_review")
        findings[-1]["status"] = "needs_information"
        findings[-1]["summary"] += " 원재료 연동 시행 전 계약의 적용례 확인이 필요합니다."
    if effective and effective < date(2026, 8, 11) and any(d.category == "energy" for d in facts.linkage_details):
        findings[-1]["missing_fields"].append("energy_linkage_transition_review")
        findings[-1]["status"] = "needs_information"
        findings[-1]["summary"] += " 에너지 연동 확대 시행 전 계약의 적용례 확인이 필요합니다."

    duration_summary = f"제안 계약 기간은 {terms.get('contract_days', '미확인')}일입니다. "
    if facts.committed_quantity is not None:
        duration_summary += f"사용자가 확정 구매물량 {facts.committed_quantity:,}개를 입력했습니다. 약정 범위와 증빙을 확인해야 하며 기간 연장분에도 자동 적용되지 않습니다."
    else:
        duration_summary += "확정 구매물량은 미입력입니다. 기간 연장을 물량 보장이나 할인 정당성의 근거로 설명할 수 없습니다."
    add("duration.commitment", ["contract_days", "unit_price"], duration_summary,
        constraints=[{"type": "no_volume_guarantee_without_commitment", "committed_quantity": facts.committed_quantity},
                     {"type": "reassess_linkage_on_duration_change"}], articles=[3, 8])
    statuses = {f["status"] for f in findings}
    return {"status": "blocked" if "blocked" in statuses else "needs_information" if "needs_information" in statuses else "ready",
            "as_of": as_of.isoformat(), "facts": snapshot, "findings": findings,
            "missing_fields": sorted({m for f in findings for m in f["missing_fields"]}),
            "constraints": [c for f in findings for c in f["constraints"]],
            "notice": "사용자 제공 사실에 대한 조건부 검토이며 법률 적합성 인증이나 증빙 진위 확인이 아닙니다."}
