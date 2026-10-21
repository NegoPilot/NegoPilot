"""NegoPilot 컴포넌트 간 공용 계약."""

from negopilot_shared.decomposition import (
    CLARIFY_CONFIDENCE,
    MAX_SUB_QUERIES,
    SCHEMA_VERSION,
    Clarification,
    DecompositionResult,
    Entities,
    InputRef,
    SubQuery,
)
from negopilot_shared.intents import IntentSpec, load_intents
from negopilot_shared.routing import ROUTING_SCHEMA_VERSION, RouteResult, SubQueryOutcome, ToolCall

__all__ = [
    "CLARIFY_CONFIDENCE",
    "MAX_SUB_QUERIES",
    "ROUTING_SCHEMA_VERSION",
    "SCHEMA_VERSION",
    "Clarification",
    "DecompositionResult",
    "Entities",
    "InputRef",
    "IntentSpec",
    "RouteResult",
    "SubQuery",
    "SubQueryOutcome",
    "ToolCall",
    "load_intents",
]
