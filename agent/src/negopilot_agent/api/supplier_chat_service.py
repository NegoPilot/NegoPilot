"""Fixture-backed supplier details for the Generation stub tool caller."""

import json
from pathlib import Path
from typing import Any

from .supplier_chat_models import SupplierDetail, SupplierFixture


FIXTURE_PATH = (
    Path(__file__).resolve().parents[4]
    / "docs"
    / "contracts"
    / "fixtures"
    / "supplier-chat.json"
)

OVERVIEW_QUERY = "이 회사에 대해 알려줘"
FINANCIAL_QUERY = "이 회사의 재무 상태를 알려줘"
QUALITY_QUERY = "납기와 품질은 어때?"
OVERVIEW_ALIAS = "이 회사의 재무 상태와 납기 지표를 알려줘"


class SupplierNotFound(LookupError):
    """The requested supplier ID does not exist."""


def _unique_capabilities(capabilities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for capability in capabilities:
        key = (
            capability["process"],
            capability["material"],
            capability["monthly_capacity"],
        )
        unique.setdefault(key, capability)
    return list(unique.values())


class SupplierChatService:
    """Read only the shared fixture. No model, MCP, database, or mail calls."""

    def __init__(self, fixture_path: Path | None = None) -> None:
        path = fixture_path if fixture_path is not None else FIXTURE_PATH
        fixture = SupplierFixture.model_validate(json.loads(path.read_text(encoding="utf-8")))
        records: dict[str, dict[str, Any]] = {}
        for supplier in fixture.suppliers:
            record = supplier.model_dump(mode="json")
            supplier_id = record["supplier_id"]
            if supplier_id in records:
                previous = records[supplier_id]
                # Duplicate rows may describe more capabilities for the same company.
                # Conflicting identity or financial data cannot be merged safely.
                if any(
                    previous[key] != value
                    for key, value in record.items()
                    if key != "capabilities"
                ):
                    raise ValueError("Conflicting supplier fixture records")
                previous["capabilities"] = _unique_capabilities(
                    previous["capabilities"] + record["capabilities"]
                )
            else:
                record["capabilities"] = _unique_capabilities(record["capabilities"])
                records[supplier_id] = record
        self._suppliers = {
            supplier_id: SupplierDetail.model_validate(record)
            for supplier_id, record in sorted(records.items())
        }

    async def detail(self, supplier_id: str) -> SupplierDetail:
        supplier = self._suppliers.get(supplier_id)
        if supplier is None:
            raise SupplierNotFound("Supplier does not exist.")
        return supplier.model_copy(deep=True)
