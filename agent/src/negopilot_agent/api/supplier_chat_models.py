"""Strict v0.2 supplier demo contracts independent of the v0.1 core models."""

from datetime import date
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False, revalidate_instances="always")


Text = Annotated[str, StringConstraints(min_length=1, max_length=100, pattern=r"\S")]
SUPPLIER_ID_PATTERN = r"^[A-Za-z0-9_-]{1,100}$"
SupplierId = Annotated[str, StringConstraints(min_length=1, max_length=100, pattern=SUPPLIER_ID_PATTERN)]
LongText = Annotated[str, StringConstraints(min_length=1, max_length=4000, pattern=r"\S")]
UuidString = Annotated[str, StringConstraints(
    pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)]
FilterText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
QueryText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
Number = int | float
# 조회 경로는 stub이면 fixture, live면 business-mcp DB를 읽는다. 대화 경로는 stub 전용이다.
LookupMode = Literal["stub", "live"]
# STUB은 fixture, DART는 공시 값, MOCK은 business-mcp의 가상 값이다.
Source = Literal["STUB", "DART", "MOCK"]


class Capability(StrictModel):
    process: Text
    material: Text
    monthly_capacity: int = Field(ge=0)


class Provenance(StrictModel):
    profile: Source
    financials: Source
    operations: Source


class Quality(StrictModel):
    defect_ppm: Annotated[Number, Field(ge=0)] | None
    on_time_delivery_rate: Annotated[Number, Field(ge=0, le=100)] | None


class Financials(StrictModel):
    year: int = Field(ge=1900, le=9999)
    revenue_krw: Number | None
    operating_profit_krw: Number | None
    debt_ratio: Number | None
    source: Source


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
    matched_capabilities: list[Capability] = Field(min_length=1)
    provenance: Provenance


class SupplierDetail(StrictModel):
    supplier_id: SupplierId
    name: Text
    region: Text
    iatf16949: bool
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


class ChatHistoryEntry(StrictModel):
    role: Literal["user", "assistant"]
    content: Annotated[str, StringConstraints(min_length=1, max_length=4000)]

    @field_validator("content")
    @classmethod
    def nonblank_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("History content must not be blank")
        return value


class ChatRequest(StrictModel):
    request_id: UuidString
    conversation_id: UuidString
    query: QueryText
    history: list[ChatHistoryEntry] = Field(max_length=20)
    context: Context


class SupplierCard(StrictModel):
    type: Literal["supplier_detail"]
    supplier: SupplierDetail
    evidence_ids: list[Text] = Field(min_length=1)


class StubEvidence(StrictModel):
    evidence_id: Text
    server: Literal["business-mcp"]
    tool: Literal["get_supplier"]
    supplier_id: SupplierId
    source: Literal["stub"]
    executed: Literal[False]

    @field_validator("executed", mode="before")
    @classmethod
    def executed_is_boolean(cls, value: object) -> bool:
        if type(value) is not bool:
            raise ValueError("executed must be a boolean")
        return value


class Presentation(StrictModel):
    status: Literal["READY", "NEEDS_INPUT"]
    renderer: Literal["template"]
    text: LongText
    cards: list[SupplierCard] = Field(max_length=1)


class Clarification(StrictModel):
    missing_fields: list[Literal["context.selected_supplier_ids", "query"]] = Field(min_length=1, max_length=1)
    question: LongText
    options: list[LongText] = Field(min_length=3, max_length=3)


class ChatResponse(StrictModel):
    schema_version: Literal["0.2"]
    mode: Literal["stub"]
    request_id: UuidString
    conversation_id: UuidString
    response_id: UuidString
    execution_status: Literal["COMPLETED", "NEEDS_INPUT"]
    generation_status: Literal["NOT_IMPLEMENTED"]
    resolved_context: Context
    presentation: Presentation
    citations: list[Any] = Field(max_length=0)
    evidence: list[StubEvidence] = Field(max_length=1)
    clarification: Clarification | None

    @model_validator(mode="after")
    def matching_state_and_supplier(self) -> "ChatResponse":
        selected = self.resolved_context.selected_supplier_ids
        if self.execution_status == "COMPLETED":
            if (self.presentation.status != "READY" or self.clarification is not None
                    or len(selected) != 1 or len(self.presentation.cards) != 1 or len(self.evidence) != 1):
                raise ValueError("Completed response requires exactly one selected supplier and evidence")
            card = self.presentation.cards[0]
            evidence = self.evidence[0]
            if card.supplier.supplier_id != selected[0] or evidence.supplier_id != selected[0]:
                raise ValueError("Supplier context does not match card and evidence")
            if card.evidence_ids != [evidence.evidence_id]:
                raise ValueError("Card evidence reference is invalid")
        else:
            if (self.presentation.status != "NEEDS_INPUT" or self.presentation.cards or self.evidence
                    or self.clarification is None or self.clarification.question != self.presentation.text):
                raise ValueError("Clarification response has inconsistent presentation or evidence")
            expected = ["query"] if selected else ["context.selected_supplier_ids"]
            if self.clarification.missing_fields != expected:
                raise ValueError("Clarification does not match missing context or query")
        return self


class CatalogResponse(StrictModel):
    schema_version: Literal["0.2"]
    mode: LookupMode
    data: CatalogData


class SupplierSearchResponse(StrictModel):
    schema_version: Literal["0.2"]
    mode: LookupMode
    data: SupplierListData


class SupplierDetailResponse(StrictModel):
    schema_version: Literal["0.2"]
    mode: LookupMode
    data: SupplierDetail


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
