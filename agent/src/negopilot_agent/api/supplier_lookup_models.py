"""v2 live supplier lookup contracts. Same shapes as v0.2 with business-mcp data sources."""

from typing import Literal

from pydantic import Field

from .supplier_chat_models import (
    CatalogData, Financials, StrictModel, SupplierDetail, SupplierListData, SupplierSummary,
)

# DART는 공시 값, MOCK은 business-mcp의 가상 값이다.
LiveSource = Literal["DART", "MOCK"]


class LiveProvenance(StrictModel):
    profile: LiveSource
    financials: LiveSource
    # 생산능력·품질·계약·견적은 모두 가상 데이터다.
    operations: Literal["MOCK"]


class LiveFinancials(Financials):
    source: LiveSource


class LiveSupplierSummary(SupplierSummary):
    provenance: LiveProvenance


class LiveSupplierDetail(SupplierDetail):
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
