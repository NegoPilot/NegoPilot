"""Strict supplier models shared by the v2 lookup and the Generation stub fixture."""

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False, revalidate_instances="always")


Text = Annotated[str, StringConstraints(min_length=1, max_length=100, pattern=r"\S")]
SUPPLIER_ID_PATTERN = r"^[A-Za-z0-9_-]{1,100}$"
SupplierId = Annotated[str, StringConstraints(min_length=1, max_length=100, pattern=SUPPLIER_ID_PATTERN)]
LongText = Annotated[str, StringConstraints(min_length=1, max_length=4000, pattern=r"\S")]
FilterText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Number = int | float


class Capability(StrictModel):
    process: Text
    material: Text
    monthly_capacity: int = Field(ge=0)


class Provenance(StrictModel):
    profile: Literal["STUB"]
    financials: Literal["STUB"]
    operations: Literal["STUB"]


class Quality(StrictModel):
    defect_ppm: Annotated[Number, Field(ge=0)] | None
    on_time_delivery_rate: Annotated[Number, Field(ge=0, le=100)] | None


class Financials(StrictModel):
    year: int = Field(ge=1900, le=9999)
    revenue_krw: Number | None
    operating_profit_krw: Number | None
    debt_ratio: Number | None
    source: Literal["STUB"]


class RecentQuote(StrictModel):
    process: Text
    material: Text
    unit_price_krw: Number = Field(ge=0)
    quoted_at: Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}$")]

    @field_validator("quoted_at")
    @classmethod
    def valid_date(cls, value: str) -> str:
        date.fromisoformat(value)
        return value


class ExistingContract(StrictModel):
    payment_days: int = Field(ge=0)
    price_linkage: bool


class SupplierSummary(StrictModel):
    supplier_id: SupplierId
    name: Text
    region: Text
    iatf16949: bool
    iso14001: bool
    matched_capabilities: list[Capability] = Field(min_length=1)
    provenance: Provenance


class SupplierDetail(StrictModel):
    supplier_id: SupplierId
    name: Text
    region: Text
    iatf16949: bool
    iso14001: bool
    capabilities: list[Capability] = Field(min_length=1)
    quality: Quality
    financials: list[Financials]
    recent_quotes: list[RecentQuote]
    existing_contract: ExistingContract | None
    provenance: Provenance


class CatalogProcess(StrictModel):
    process: Text
    materials: list[Text]

    @field_validator("materials")
    @classmethod
    def unique_materials(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("Materials must be unique")
        return values


class CatalogData(StrictModel):
    processes: list[CatalogProcess]
    regions: list[Text]

    @model_validator(mode="after")
    def unique_values(self) -> "CatalogData":
        if len(self.regions) != len(set(self.regions)):
            raise ValueError("Regions must be unique")
        if len(self.processes) != len({item.process for item in self.processes}):
            raise ValueError("Processes must be unique")
        return self


class SupplierSearch(StrictModel):
    process: FilterText
    material: FilterText | None = None
    region: FilterText | None = None
    iatf_required: bool = False
    iso14001_required: bool = False
    min_monthly_capacity: int | None = Field(default=None, ge=1, le=10_000_000)
    limit: int = Field(default=20, ge=1, le=100)


class SupplierListData(StrictModel):
    items: list[SupplierSummary] = Field(max_length=100)
    returned_count: int = Field(ge=0, le=100)
    limit: int = Field(ge=1, le=100)

    @model_validator(mode="after")
    def consistent_count(self) -> "SupplierListData":
        ids = [item.supplier_id for item in self.items]
        if self.returned_count != len(ids) or len(ids) > self.limit:
            raise ValueError("Returned count or limit does not match items")
        if ids != sorted(set(ids)):
            raise ValueError("Suppliers must be unique and sorted by ID")
        return self


class Context(StrictModel):
    selected_supplier_ids: list[SupplierId] = Field(max_length=1)

    @field_validator("selected_supplier_ids")
    @classmethod
    def valid_ids(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)) or any(not value.strip() for value in values):
            raise ValueError("Supplier IDs must be nonblank and unique")
        return values


class SupplierFixture(StrictModel):
    schema_version: Literal["0.2"]
    mode: Literal["stub"]
    notice: LongText
    supported_queries: list[LongText] = Field(min_length=3, max_length=3)
    suppliers: list[SupplierDetail]

    @model_validator(mode="after")
    def unique_entries(self) -> "SupplierFixture":
        if len(self.supported_queries) != len(set(self.supported_queries)):
            raise ValueError("Supported queries must be unique")
        return self
