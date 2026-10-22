"""QA-only execution extension. Public Agent schemas and intent registry stay unchanged."""

from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from negopilot_shared import Clarification, DecompositionResult


class QAObject(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QAError(QAObject):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)


class QuoteRef(QAObject):
    from_step: str = Field(min_length=1)
    from_sub_query: str = Field(pattern=r"^q\d+$")
    selector: Literal["latest_quote_unit_price"]


def checked_decimal(value: Any) -> Decimal:
    """Bound numeric precision without Python evaluation or binary-float arithmetic."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError("A finite decimal number is required")
    if isinstance(value, str) and (not value.strip() or len(value) > 80):
        raise ValueError("A finite decimal number is required")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("A finite decimal number is required") from None
    if not result.is_finite():
        raise ValueError("A finite decimal number is required")
    if len(result.as_tuple().digits) > 28 or not -8 <= result.as_tuple().exponent <= 18:
        raise ValueError("QA arithmetic supports at most 28 digits and 8 decimal places")
    return result


class DiscountArguments(QAObject):
    amount: Decimal | QuoteRef | None = None
    discount_pct: Decimal | None = None

    @field_validator("amount", mode="before")
    @classmethod
    def validate_amount(cls, value: Any) -> Any:
        if value is None or isinstance(value, (dict, QuoteRef)):
            return value
        number = checked_decimal(value)
        if not 0 <= number <= Decimal("1e18"):
            raise ValueError("Amount must be between 0 and 1e18")
        return number

    @field_validator("discount_pct", mode="before")
    @classmethod
    def validate_discount(cls, value: Any) -> Any:
        if value is None:
            return None
        number = checked_decimal(value)
        if not 0 <= number <= 100:
            raise ValueError("Discount percentage must be between 0 and 100")
        return number


class RoutingStep(QAObject):
    id: str = Field(min_length=1, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    kind: Literal["routing"]
    decomposition: DecompositionResult

    @model_validator(mode="after")
    def executable_core(self) -> "RoutingStep":
        if self.decomposition.status != "ok":
            raise ValueError("A routing step requires an ok DecompositionResult")
        return self


class InternalStep(QAObject):
    id: str = Field(min_length=1, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    kind: Literal["internal"]
    operation: Literal["apply_discount"]
    arguments: DiscountArguments = Field(default_factory=DiscountArguments)


Step = Annotated[RoutingStep | InternalStep, Field(discriminator="kind")]


class QAPlan(QAObject):
    schema_version: Literal["qa-decomposition.v1"] = "qa-decomposition.v1"
    status: Literal["ok", "clarify", "failed"]
    original_query: str = Field(min_length=1)
    steps: list[Step] = Field(default_factory=list)
    clarification: Clarification | None = None
    error: QAError | None = None

    @model_validator(mode="after")
    def validate_plan(self) -> "QAPlan":
        if self.status == "ok":
            if not self.steps or self.clarification is not None or self.error is not None:
                raise ValueError("An ok plan requires steps without clarification or error")
        elif self.status == "clarify":
            if self.steps or self.clarification is None or self.error is not None:
                raise ValueError("A clarify plan requires only clarification")
        elif self.steps or self.clarification is not None or self.error is None:
            raise ValueError("A failed plan requires only an error")

        previous: dict[str, Step] = {}
        for step in self.steps:
            if step.id in previous:
                raise ValueError("Step IDs must be unique")
            if isinstance(step, RoutingStep):
                if step.decomposition.original_query != self.original_query:
                    raise ValueError("Routing original_query must match the QA input")
            elif isinstance(step.arguments.amount, QuoteRef):
                ref = step.arguments.amount
                source = previous.get(ref.from_step)
                if not isinstance(source, RoutingStep):
                    raise ValueError("Quote references must name an earlier routing step")
                sq = next((q for q in source.decomposition.sub_queries if q.id == ref.from_sub_query), None)
                if sq is None or sq.intent != "supplier_detail":
                    raise ValueError("Quote references must name a supplier_detail sub-query")
            previous[step.id] = step
        return self


def normalize_plan(raw_output: Any, *, query: str) -> QAPlan:
    """Revalidate even model instances so constructed/mutated objects cannot bypass checks."""
    if isinstance(raw_output, BaseModel):
        raw_output = raw_output.model_dump(mode="json", by_alias=True)
    if not isinstance(raw_output, dict):
        raise ValueError("Decomposer output must be a result object")
    if raw_output.get("schema_version") == "qa-decomposition.v1":
        plan = QAPlan.model_validate(raw_output)
    else:
        core = DecompositionResult.model_validate(raw_output)
        plan = QAPlan(
            status=core.status,
            original_query=core.original_query,
            steps=[RoutingStep(id="routing", kind="routing", decomposition=core)] if core.status == "ok" else [],
            clarification=core.clarification,
            error=QAError(code="DECOMPOSITION_FAILED", message="The decomposer returned failed")
            if core.status == "failed" else None,
        )
    if plan.original_query != query:
        raise ValueError("The output original_query must equal the current input query")
    return plan
