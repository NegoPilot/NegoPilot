"""Live supplier directory contracts, independent of chatbot fixture/top-N models."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator, model_validator

from .supplier_chat_models import (
    Capability, CatalogData, FilterText, Financials, Number, RecentQuote, StrictModel, Text,
)

LIVE_SUPPLIER_ID_PATTERN = r"^SUP-[0-9]{4,18}$"
LiveSupplierId = Annotated[str, StringConstraints(min_length=8, max_length=22, pattern=LIVE_SUPPLIER_ID_PATTERN)]
FinancialSource = Literal["DART", "MOCK"]
SupplierSort = Literal[
    "name", "region", "founded_year", "monthly_capacity", "defect_ppm", "on_time_delivery_rate",
    "audit_grade", "years_dealing", "payment_days", "raw_material_ratio", "latest_quote_date",
    "latest_quote_price",
]
SortDirection = Literal["asc", "desc"]
QuoteSort = Literal["quoted_at", "unit_price_krw"]


class LiveSupplierSearch(StrictModel):
    q: FilterText | None = None
    process: FilterText | None = None
    material: FilterText | None = None
    region: FilterText | None = None
    iatf_required: bool = False
    iso14001_required: bool = False
    min_monthly_capacity: int | None = Field(default=None, ge=1, le=10_000_000)
    page: int = Field(default=0, ge=0, le=1_000_000)
    size: int = Field(default=20, ge=1, le=100)
    sort: SupplierSort = "name"
    direction: SortDirection = "asc"

    @field_validator("q", mode="before")
    @classmethod
    def blank_query_is_absent(cls, value):
        return None if isinstance(value, str) and not value.strip() else value

    @model_validator(mode="after")
    def comparable_price(self) -> "LiveSupplierSearch":
        if self.sort == "latest_quote_price" and (self.process is None or self.material is None):
            raise ValueError("Quote price sorting requires a process and material")
        return self


class LiveQuoteSearch(StrictModel):
    process: FilterText | None = None
    material: FilterText | None = None
    page: int = Field(default=0, ge=0, le=1_000_000)
    size: int = Field(default=20, ge=1, le=100)
    sort: QuoteSort = "quoted_at"
    direction: SortDirection = "desc"

    @model_validator(mode="after")
    def comparable_price(self) -> "LiveQuoteSearch":
        if self.sort == "unit_price_krw" and (self.process is None or self.material is None):
            raise ValueError("Quote price sorting requires a process and material")
        return self


class LiveProvenance(StrictModel):
    profile: Literal["DART", "MOCK", "MANUAL"]
    financials: Literal["DART", "MOCK", "UNKNOWN", "MIXED"]
    operations: Literal["MOCK", "MANUAL", "MIXED"]


class LiveQuality(StrictModel):
    defect_ppm: Annotated[Number, Field(ge=0)] | None
    on_time_delivery_rate: Annotated[Number, Field(ge=0, le=100)] | None
    audit_grade: Literal["A", "B", "C", "D"] | None
    years_dealing: Annotated[int, Field(ge=0)] | None


class LiveExistingContract(StrictModel):
    payment_days: int = Field(ge=1)
    price_linkage: bool
    raw_material_ratio: Number = Field(ge=0, le=1)
    auto_renewal: bool


class LiveFinancials(Financials):
    source: FinancialSource
    fs_div: Literal["CFS", "OFS"]
    net_income_krw: Number | None
    total_assets_krw: Number | None
    total_liabilities_krw: Number | None
    total_equity_krw: Number | None


class LiveQuote(RecentQuote):
    quote_id: int = Field(ge=1)
    source: Literal["MOCK", "MANUAL"]


class LiveProfile(StrictModel):
    supplier_id: LiveSupplierId
    name: Text
    region: Text
    iatf16949: bool
    iso14001: bool
    founded_year: Annotated[int, Field(ge=1, le=9999)] | None
    employees: Annotated[int, Field(ge=1)] | None
    quality: LiveQuality
    existing_contract: LiveExistingContract | None
    provenance: LiveProvenance
    updated_at: str | None

    @field_validator("updated_at")
    @classmethod
    def valid_timestamp(cls, value: str | None) -> str | None:
        if value is not None and datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is None:
            raise ValueError("Timestamp requires timezone")
        return value


class LiveSupplierSummary(LiveProfile):
    matched_capabilities: list[Capability]
    max_monthly_capacity: Annotated[int, Field(ge=0)] | None
    latest_quote: LiveQuote | None


class LiveSupplierDetail(LiveProfile):
    capabilities: list[Capability]
    financials: list[LiveFinancials]
    recent_quotes: list[LiveQuote] = Field(max_length=5)
    corp_code: Text | None
    stock_code: Text | None
    induty_code: Text | None
    biz_no_masked: Text | None
    created_at: str | None

    @field_validator("created_at")
    @classmethod
    def valid_created_at(cls, value: str | None) -> str | None:
        return cls.valid_timestamp(value)


class LivePage(StrictModel):
    page: int = Field(ge=0, le=1_000_000)
    size: int = Field(ge=1, le=100)
    returned_count: int = Field(ge=0, le=100)
    total: int = Field(ge=0)
    total_pages: int = Field(ge=0)
    direction: SortDirection

    @model_validator(mode="after")
    def consistent_page(self) -> "LivePage":
        expected_pages = (self.total + self.size - 1) // self.size
        expected_count = min(self.size, max(0, self.total - self.page * self.size))
        if self.total_pages != expected_pages or self.returned_count != expected_count:
            raise ValueError("Page metadata is inconsistent")
        if self.returned_count != len(self.items):
            raise ValueError("Page count does not match items")
        return self


class LiveSupplierListData(LivePage):
    items: list[LiveSupplierSummary] = Field(max_length=100)
    sort: SupplierSort

    @model_validator(mode="after")
    def unique_suppliers(self) -> "LiveSupplierListData":
        if len({item.supplier_id for item in self.items}) != len(self.items):
            raise ValueError("Duplicate suppliers")
        return self


class LiveQuoteListData(LivePage):
    supplier_id: LiveSupplierId
    items: list[LiveQuote] = Field(max_length=100)
    sort: QuoteSort

    @model_validator(mode="after")
    def unique_quotes(self) -> "LiveQuoteListData":
        if len({item.quote_id for item in self.items}) != len(self.items):
            raise ValueError("Duplicate quotes")
        return self


class LiveCatalogResponse(StrictModel):
    schema_version: Literal["0.4"]
    mode: Literal["live"]
    data: CatalogData


class LiveSupplierSearchResponse(StrictModel):
    schema_version: Literal["0.4"]
    mode: Literal["live"]
    data: LiveSupplierListData


class LiveSupplierDetailResponse(StrictModel):
    schema_version: Literal["0.4"]
    mode: Literal["live"]
    data: LiveSupplierDetail


class LiveQuoteListResponse(StrictModel):
    schema_version: Literal["0.4"]
    mode: Literal["live"]
    data: LiveQuoteListData
