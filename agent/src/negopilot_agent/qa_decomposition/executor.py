"""Isolated QA executor with a fixed internal operation and the existing Router."""

from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import date
from decimal import Decimal, localcontext
import json
import re
from typing import Any

from langsmith import tracing_context
from pydantic import ValidationError

from negopilot_shared import RouteResult, load_intents
from negopilot_agent.routing import Router, ToolCaller
from negopilot_agent.routing.extract import RESULT_MAPS
from negopilot_agent.routing.router import REQUIRED

from .contracts import InternalStep, QAPlan, QuoteRef, RoutingStep, checked_decimal, normalize_plan


class ExecutionError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def apply_discount(amount: Decimal, discount_pct: Decimal) -> dict[str, str]:
    """Exact arithmetic for QA. This is not check_offer or a currency-rounding policy."""
    with localcontext() as context:
        context.prec = 64
        discount_amount = amount * discount_pct / Decimal(100)
        discounted_amount = amount - discount_amount
    return {"amount": decimal_text(amount), "discount_pct": decimal_text(discount_pct),
            "discount_amount": decimal_text(discount_amount), "discounted_amount": decimal_text(discounted_amount)}


def latest_quote_unit_price(routing: RouteResult, sub_query_id: str) -> Decimal:
    """Only a named successful supplier_detail result may supply an amount."""
    try:
        outcome = routing.outcome(sub_query_id)
    except StopIteration:
        raise ExecutionError("INVALID_REFERENCE", "The referenced sub-query was not executed") from None
    if outcome.status != "ok":
        raise ExecutionError("INVALID_REFERENCE", "The referenced sub-query did not succeed")
    calls = [call for call in outcome.calls if call.server == "business-mcp" and call.tool == "get_supplier"]
    if len(calls) != 1 or calls[0].error is not None:
        raise ExecutionError("AMBIGUOUS_QUOTE", "Exactly one successful supplier result is required")
    result = calls[0].result
    if not isinstance(result, dict):
        raise ExecutionError("INVALID_QUOTE", "The supplier response is not an object")
    quotes = result.get("recent_quotes")
    if not isinstance(quotes, list):
        raise ExecutionError("INVALID_QUOTE", "The supplier response has no valid recent_quotes array")
    if not quotes:
        raise ExecutionError("QUOTE_NOT_FOUND", "There is no recent quote to calculate from")
    dated: list[tuple[date, Decimal]] = []
    for quote in quotes:
        if not isinstance(quote, dict):
            raise ExecutionError("INVALID_QUOTE", "A quote is not an object")
        raw_date, price = quote.get("quoted_at"), quote.get("unit_price")
        if not isinstance(raw_date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_date):
            raise ExecutionError("INVALID_QUOTE", "Quote dates must use YYYY-MM-DD")
        try:
            quoted_at = date.fromisoformat(raw_date)
        except ValueError:
            raise ExecutionError("INVALID_QUOTE", "A quote date is invalid") from None
        if type(price) is not int or not 0 < price <= 10**18:
            raise ExecutionError("INVALID_QUOTE", "Quote unit_price must be a positive integer amount in won")
        dated.append((quoted_at, checked_decimal(price)))
    newest = max(day for day, _ in dated)
    candidates = [price for day, price in dated if day == newest]
    if len(candidates) != 1:
        raise ExecutionError("AMBIGUOUS_QUOTE", "More than one quote has the newest date")
    return candidates[0]


def _event(events: list[dict], kind: str, step_id: str, target: str, arguments: dict) -> dict:
    item = {"sequence": len(events) + 1, "kind": kind, "step_id": step_id,
            "target": target, "arguments": deepcopy(arguments), "result": None,
            "status": "running", "error": None}
    events.append(item)
    return item


class TracedCaller:
    def __init__(self, caller: ToolCaller, events: list[dict], step_id: str):
        self.caller, self.events, self.step_id = caller, events, step_id

    @asynccontextmanager
    async def session(self):
        session = getattr(self.caller, "session", None)
        if session:
            async with session():
                yield
        else:
            yield

    async def __call__(self, server: str, tool: str, args: dict) -> Any:
        event = _event(self.events, "mcp", self.step_id, f"{server}.{tool}", args)
        try:
            result = await self.caller(server, tool, args)
            try:
                result = json.loads(json.dumps(result, ensure_ascii=False, allow_nan=False))
            except (TypeError, ValueError, OverflowError, RecursionError):
                raise ExecutionError("INVALID_TOOL_RESULT", "The tool response must contain finite JSON data") from None
        except Exception as error:
            code = error.code if isinstance(error, ExecutionError) else "TOOL_ERROR"
            event.update(status="error", error={"code": code, "exception_type": type(error).__name__})
            raise
        event.update(status="ok", result=result)
        return result


def _classification(events: list[dict]) -> str:
    execution = [event["kind"] for event in events if event["kind"] in {"mcp", "internal"}]
    has_mcp, has_internal = "mcp" in execution, "internal" in execution
    if has_mcp and has_internal:
        return "mcp_then_internal" if execution.index("internal") > max(
            i for i, kind in enumerate(execution) if kind == "mcp") else "mixed"
    return "mcp" if has_mcp else "internal" if has_internal else "none"


def _preflight(plan: QAPlan) -> list[str]:
    """Check all required inputs before allowing the first side effect."""
    missing: list[str] = []
    intents = load_intents()
    for step in plan.steps:
        if isinstance(step, InternalStep):
            missing += [f"{step.id}.{name}" for name in ("amount", "discount_pct")
                        if getattr(step.arguments, name) is None]
            continue
        by_id = {sq.id: sq for sq in step.decomposition.sub_queries}
        for sq in step.decomposition.sub_queries:
            spec = intents[sq.intent]
            if spec.status == "planned" or sq.intent == "out_of_scope":
                raise ExecutionError("UNSUPPORTED_TARGET", "The requested execution target is not implemented")
            for ref in sq.inputs:
                source_map = RESULT_MAPS.get(by_id[ref.from_].intent)
                if source_map is None or ref.slot not in source_map.slots:
                    raise ExecutionError("INVALID_REFERENCE", "The source result cannot supply the requested input")
            supplied = {key for key, value in sq.entities.model_dump().items()
                        if value is not None and (not isinstance(value, str) or value.strip())}
            supplied.update(ref.slot for ref in sq.inputs)
            if spec.tools:
                missing += [f"{step.id}.{sq.id}.{key}" for key in REQUIRED.get(spec.tools[0], [])
                            if key not in supplied]
    return missing


def _base_report() -> dict:
    return {"status": "validation_failed", "classification": "none", "normalized_plan": None,
            "events": [], "step_results": {}, "final_result": None, "errors": [], "clarification": None}


def _clarify(report: dict, missing: list[str]) -> dict:
    report["status"] = "clarify"
    report["final_result"] = None
    report["errors"].append({"code": "MISSING_INPUT", "message": "Required input is missing", "missing": missing})
    report["clarification"] = {"reason": "필수 입력이 부족합니다.",
                               "question": "다음 정보를 알려 주세요: " + " / ".join(missing),
                               "options": ["필수 정보를 추가할게요", "질문을 다시 작성할게요"]}
    return report


async def execute(raw_output: Any, *, query: str, caller: ToolCaller) -> dict:
    # Context-local suppression also applies to Router's optional LangSmith spans.
    # The QA runner owns local evidence; inherited live tracing must not upload it.
    with tracing_context(enabled=False):
        return await _execute(raw_output, query=query, caller=caller)


async def _execute(raw_output: Any, *, query: str, caller: ToolCaller) -> dict:
    report = _base_report()
    try:
        plan = normalize_plan(raw_output, query=query)
        normalized_plan = json.loads(json.dumps(plan.model_dump(mode="json", by_alias=True), allow_nan=False))
    except (ValidationError, ValueError, TypeError) as error:
        # No model values or exception internals are needed to describe structural failures.
        detail = [{"location": list(item["loc"]), "type": item["type"]}
                  for item in error.errors(include_input=False, include_context=False)] if isinstance(error, ValidationError) else []
        report["errors"] = [{"code": "INVALID_DECOMPOSITION", "message": "The decomposition failed QA validation",
                             "details": detail}]
        return report
    report["normalized_plan"] = normalized_plan
    if plan.status == "clarify":
        report.update(status="clarify", clarification=plan.clarification.model_dump())
        return report
    if plan.status == "failed":
        report.update(status="failed", errors=[plan.error.model_dump()])
        return report
    try:
        missing = _preflight(plan)
    except ExecutionError as error:
        report["errors"] = [{"code": error.code, "message": str(error)}]
        return report
    if missing:
        return _clarify(report, missing)

    routed: dict[str, RouteResult] = {}
    try:
        for step in plan.steps:
            if isinstance(step, RoutingStep):
                route = await Router(TracedCaller(caller, report["events"], step.id)).route(step.decomposition)
                routed[step.id] = route
                result = route.model_dump(mode="json", by_alias=True)
                report["step_results"][step.id] = result
                missing = [f"{step.id}.{outcome.id}.{name}" for outcome in route.outcomes for name in outcome.missing]
                if missing:
                    _clarify(report, missing)
                    break
                unsuccessful = next((outcome for outcome in route.outcomes
                                     if outcome.status not in {"ok", "general_knowledge"}), None)
                if unsuccessful:
                    code = {"error": "TOOL_ERROR", "empty": "NO_RESULT", "unsupported": "UNSUPPORTED_TARGET",
                            "out_of_scope": "UNSUPPORTED_TARGET", "skipped": "DEPENDENCY_FAILED"}[unsuccessful.status]
                    raise ExecutionError(code, f"Routing sub-query {unsuccessful.id} did not produce an executable result")
            else:
                amount = step.arguments.amount
                if isinstance(amount, QuoteRef):
                    event = _event(report["events"], "binding", step.id, "latest_quote_unit_price",
                                   amount.model_dump())
                    try:
                        amount = latest_quote_unit_price(routed[amount.from_step], amount.from_sub_query)
                    except ExecutionError as error:
                        event.update(status="error", error={"code": error.code, "message": str(error)})
                        raise
                    event.update(status="ok", result=decimal_text(amount))
                event = _event(report["events"], "internal", step.id, "apply_discount",
                               {"amount": decimal_text(amount), "discount_pct": decimal_text(step.arguments.discount_pct)})
                try:
                    result = apply_discount(amount, step.arguments.discount_pct)
                except Exception as error:
                    event.update(status="error", error={"code": "EXECUTION_ERROR", "exception_type": type(error).__name__})
                    raise
                event.update(status="ok", result=result)
                report["step_results"][step.id] = result
            report["final_result"] = result
        else:
            report["status"] = "completed"
    except ExecutionError as error:
        report.update(status="execution_failed", final_result=None,
                      errors=[{"code": error.code, "message": str(error)}])
    except Exception as error:
        report.update(status="execution_failed", final_result=None, errors=[{"code": "EXECUTION_ERROR",
                                                         "message": "QA execution failed",
                                                         "exception_type": type(error).__name__}])
    report["classification"] = _classification(report["events"])
    return report
