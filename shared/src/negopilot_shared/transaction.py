"""User-declared transaction facts; unknown values must never become assumptions."""
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class LinkageDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: Literal["material", "energy"]
    name: str = Field(min_length=1)
    cost_ratio: float | None = Field(default=None, ge=0, le=1, strict=True)
    indicator: str | None = None
    formula: str | None = None
    trigger_ratio: float | None = Field(default=None, ge=0, le=0.1, strict=True)
    frequency: str | None = None
    reference_period: str | None = None
    reflection_date: str | None = None
    evidence_references: list[str] = Field(default_factory=list)


class TransactionFacts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transaction_type: Literal["manufacturing", "purchase", "unknown"] = "unknown"
    # An explicit user assertion, not a legal certification by this service.
    party_requirements_confirmed: bool | None = Field(default=None, strict=True)
    price_scope: Literal["new_order", "existing_order", "unknown"] = "unknown"
    contracted_on: date | None = None
    renewed_on: date | None = None
    actual_contract_amount: int | None = Field(default=None, gt=0, strict=True)
    committed_quantity: int | None = Field(default=None, gt=0, strict=True)
    previous_payment_days: int | None = Field(default=None, gt=0, strict=True, exclude_if=lambda value: value is None)
    buyer_financing_rate: float | None = Field(default=None, ge=0, le=1, strict=True, allow_inf_nan=False,
                                              exclude_if=lambda value: value is None)
    supplier_financing_rate: float | None = Field(default=None, ge=0, le=1, strict=True, allow_inf_nan=False,
                                                 exclude_if=lambda value: value is None)
    volume_option_quantity: int | None = Field(default=None, gt=0, strict=True, exclude_if=lambda value: value is None)
    volume_option_unit_price: int | None = Field(default=None, gt=0, strict=True, exclude_if=lambda value: value is None)
    previous_unit_price: int | None = Field(default=None, gt=0, strict=True)
    payment_basis: Literal["receipt", "completion", "monthly_agreed", "unknown"] = "unknown"
    receipt_date: date | None = None
    completion_date: date | None = None
    monthly_agreed_date: date | None = None
    agreed_due_date: date | None = None
    upstream_received_on: date | None = None
    upstream_payment_applicable: bool | None = Field(default=None, strict=True)
    payment_exception_claimed: bool | None = Field(default=None, strict=True)
    payment_exception_reason: str | None = None
    payment_exception_evidence: list[str] = Field(default_factory=list)
    reduction_reason: str | None = None
    reduction_evidence: list[str] = Field(default_factory=list)
    linkage_details: list[LinkageDetail] = Field(default_factory=list)
    waiver_reason: str | None = None
    waiver_evidence: list[str] = Field(default_factory=list)
    evidence_references: list[str] = Field(default_factory=list)
