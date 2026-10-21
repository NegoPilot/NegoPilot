"""v2 live supplier lookup contracts. Same shapes as v0.2 with business-mcp data sources."""

from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from .supplier_chat_models import (
    CatalogData, Financials, StrictModel, SupplierDetail, SupplierListData, SupplierSummary,
)

LIVE_SUPPLIER_ID_PATTERN = r"^SUP-[0-9]{4,18}$"
LiveSupplierId = Annotated[str, StringConstraints(
    min_length=8, max_length=22, pattern=LIVE_SUPPLIER_ID_PATTERN,
)]
FinancialSource = Literal["DART", "MOCK"]


class LiveProvenance(StrictModel):
    profile: Literal["DART", "MOCK", "MANUAL"]
    financials: Literal["DART", "MOCK", "UNKNOWN", "MIXED"]
    operations: Literal["MOCK", "MANUAL", "MIXED"]


class LiveFinancials(Financials):
    source: FinancialSource


class LiveSupplierSummary(SupplierSummary):
    supplier_id: LiveSupplierId
    provenance: LiveProvenance


class LiveSupplierDetail(SupplierDetail):
    supplier_id: LiveSupplierId
    financials: list[LiveFinancials]
    provenance: LiveProvenance


class LiveSupplierListData(SupplierListData):
    items: list[LiveSupplierSummary] = Field(max_length=100)


class LiveCatalogResponse(StrictModel):
    schema_version: Literal["0.3"]
    mode: Literal["live"]
    data: CatalogData


class LiveSupplierSearchResponse(StrictModel):
    schema_version: Literal["0.3"]
    mode: Literal["live"]
    data: LiveSupplierListData


class LiveSupplierDetailResponse(StrictModel):
    schema_version: Literal["0.3"]
    mode: Literal["live"]
    data: LiveSupplierDetail
