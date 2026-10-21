"""Fixture-backed supplier search and deterministic chat presentation for v0.2."""

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from .supplier_chat_models import (
    CatalogData,
    ChatRequest,
    ChatResponse,
    SupplierDetail,
    SupplierFixture,
    SupplierListData,
    SupplierSearch,
)


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
    """The requested supplier ID is absent from the demo fixture."""


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


def _number(value: int | float) -> str:
    # No thousands separators: the Korean prose convention forbids commas.
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _metric(label: str, value: int | float | None, unit: str = "") -> str:
    if value is None:
        return f"{label} 정보는 없습니다."
    return f"{label}은 {_number(value)}{unit}입니다."


class SupplierChatService:
    """Read only the shared fixture. No model, MCP, database, or mail calls."""

    def __init__(self, fixture_path: Path | None = None) -> None:
        path = fixture_path if fixture_path is not None else FIXTURE_PATH
        fixture = SupplierFixture.model_validate(json.loads(path.read_text(encoding="utf-8")))
        self.notice = fixture.notice
        self.supported_queries = list(fixture.supported_queries)
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

    async def catalog(self) -> CatalogData:
        materials_by_process: dict[str, set[str]] = {}
        regions: set[str] = set()
        for supplier in self._suppliers.values():
            regions.add(supplier.region)
            for capability in supplier.capabilities:
                materials_by_process.setdefault(capability.process, set()).add(capability.material)
        return CatalogData.model_validate({
            "processes": [
                {"process": process, "materials": sorted(materials)}
                for process, materials in sorted(materials_by_process.items())
            ],
            "regions": sorted(regions),
        })

    async def search(self, filters: SupplierSearch) -> SupplierListData:
        items = []
        for supplier in self._suppliers.values():
            if filters.region is not None and supplier.region != filters.region:
                continue
            if filters.iatf_required and not supplier.iatf16949:
                continue
            matched = [
                capability.model_dump(mode="json")
                for capability in supplier.capabilities
                if capability.process == filters.process
                and (filters.material is None or capability.material == filters.material)
                and (filters.min_monthly_capacity is None
                     or capability.monthly_capacity >= filters.min_monthly_capacity)
            ]
            if not matched:
                continue
            items.append({
                "supplier_id": supplier.supplier_id,
                "name": supplier.name,
                "region": supplier.region,
                "iatf16949": supplier.iatf16949,
                "matched_capabilities": matched,
                "provenance": supplier.provenance.model_dump(mode="json"),
            })
            if len(items) == filters.limit:
                break
        return SupplierListData.model_validate({
            "items": items,
            "returned_count": len(items),
            "limit": filters.limit,
        })

    async def detail(self, supplier_id: str) -> SupplierDetail:
        supplier = self._suppliers.get(supplier_id)
        if supplier is None:
            raise SupplierNotFound("Supplier does not exist.")
        return supplier.model_copy(deep=True)

    async def respond(self, body: ChatRequest) -> ChatResponse:
        selected = body.context.selected_supplier_ids
        if not selected:
            return self._needs_input(
                body,
                "context.selected_supplier_ids",
                "테스트 기업 한 곳을 먼저 선택해 주세요.",
            )

        supplier = await self.detail(selected[0])
        query = body.query.strip()
        if query == OVERVIEW_ALIAS:
            query = OVERVIEW_QUERY
        if query not in self.supported_queries or query not in {
            OVERVIEW_QUERY, FINANCIAL_QUERY, QUALITY_QUERY,
        }:
            return self._needs_input(body, "query", "지원하는 테스트 질문을 선택해 주세요.")

        evidence_id = f"stub-supplier-{uuid4()}"
        return ChatResponse.model_validate({
            **self._response_fields(body),
            "execution_status": "COMPLETED",
            "presentation": {
                "status": "READY",
                "renderer": "template",
                "text": self._presentation(supplier, query),
                "cards": [{
                    "type": "supplier_detail",
                    "supplier": supplier.model_dump(mode="json"),
                    "evidence_ids": [evidence_id],
                }],
            },
            "evidence": [{
                "evidence_id": evidence_id,
                "server": "business-mcp",
                "tool": "get_supplier",
                "supplier_id": supplier.supplier_id,
                "source": "stub",
                "executed": False,
            }],
            "clarification": None,
        })

    @staticmethod
    def _response_fields(body: ChatRequest) -> dict[str, Any]:
        return {
            "schema_version": "0.2",
            "mode": "stub",
            "request_id": body.request_id,
            "conversation_id": body.conversation_id,
            "response_id": str(uuid4()),
            "generation_status": "NOT_IMPLEMENTED",
            "resolved_context": body.context.model_dump(mode="json"),
            "citations": [],
        }

    def _needs_input(self, body: ChatRequest, missing_field: str, question: str) -> ChatResponse:
        return ChatResponse.model_validate({
            **self._response_fields(body),
            "execution_status": "NEEDS_INPUT",
            "presentation": {
                "status": "NEEDS_INPUT",
                "renderer": "template",
                "text": question,
                "cards": [],
            },
            "evidence": [],
            "clarification": {
                "missing_fields": [missing_field],
                "question": question,
                "options": list(self.supported_queries),
            },
        })

    def _presentation(self, supplier: SupplierDetail, query: str) -> str:
        record = supplier.model_dump(mode="json")
        sentences = [self.notice, f"{supplier.name}의 테스트 데이터입니다."]
        if query == OVERVIEW_QUERY:
            sentences.append(f"소재지는 {supplier.region}입니다.")
            certification = "있음" if supplier.iatf16949 else "없음"
            sentences.append(f"IATF 16949 인증은 {certification}으로 기록돼 있습니다.")
            for capability in record["capabilities"]:
                sentences.append(
                    f"{capability['process']} 공정의 {capability['material']} 소재 "
                    f"월 생산능력은 {_number(capability['monthly_capacity'])}입니다."
                )
        if query in {OVERVIEW_QUERY, FINANCIAL_QUERY}:
            if not record["financials"]:
                sentences.append("재무 정보는 없습니다.")
            for financial in record["financials"]:
                year = financial["year"]
                sentences.extend([
                    _metric(f"{year}년 매출", financial["revenue_krw"], "원"),
                    _metric(f"{year}년 영업이익", financial["operating_profit_krw"], "원"),
                    _metric(f"{year}년 부채비율", financial["debt_ratio"], "%"),
                ])
        if query in {OVERVIEW_QUERY, QUALITY_QUERY}:
            quality = record["quality"]
            sentences.extend([
                _metric("불량률", quality["defect_ppm"], " PPM"),
                _metric("정시 납품률", quality["on_time_delivery_rate"], "%"),
            ])
        return " ".join(sentences)
