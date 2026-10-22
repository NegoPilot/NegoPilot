"""Choose one decomposer for the QA runner. Never prepend a classification model.

These variables are read only by the lab. Production live settings are unchanged.
Factory paths are trusted developer configuration, never model-generated actions.
"""

import importlib
import inspect
import os
import re
from dataclasses import dataclass
from typing import Any, Protocol

FIXTURE_FACTORY = "negopilot_agent.qa_decomposition.fixtures:create_decomposer"
_FACTORY = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*", re.ASCII)


class Decomposer(Protocol):
    async def decompose(self, query: str, history: list[dict] | None = None, *,
                        context: dict[str, list[str]] | None = None) -> Any: ...


class ModuleConfigurationError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class SelectedDecomposer:
    decomposer: Decomposer
    metadata: dict[str, Any]


def select_decomposer(module: str | None = None, *, factory: str | None = None,
                      allow_model_calls: bool = False) -> SelectedDecomposer:
    """Load exactly one module. Unknown or unavailable modules never fall back.

Only the bundled scripted fixture can run without the explicit model-call opt-in.
This guard prevents accidental paid evaluation; it is not a sandbox for plugins.
"""
    module = os.getenv("NEGOPILOT_QA_DECOMPOSER", "existing") if module is None else module
    factory = os.getenv("NEGOPILOT_QA_DECOMPOSER_FACTORY") if factory is None else factory
    if module not in {"existing", "qa"}:
        raise ModuleConfigurationError("UNKNOWN_DECOMPOSER", "Choose existing or qa for the QA decomposer.")
    if module == "existing" and factory:
        raise ModuleConfigurationError("UNEXPECTED_FACTORY", "A QA factory requires module=qa.")
    if module == "qa" and (not factory or not _FACTORY.fullmatch(factory)):
        raise ModuleConfigurationError("INVALID_FACTORY", "Configure a trusted Python module:factory for module=qa.")

    test_double = module == "qa" and factory == FIXTURE_FACTORY
    if not test_double and not allow_model_calls:
        raise ModuleConfigurationError(
            "MODEL_CALLS_NOT_ALLOWED",
            "Only the bundled fixture is enabled without --allow-model-calls. No model was called.",
        )
    if module == "existing":
        from negopilot_agent.decomposition import QueryDecomposer

        decomposer = QueryDecomposer()
        factory = "negopilot_agent.decomposition:QueryDecomposer"
    else:
        module_name, factory_name = factory.split(":")
        try:
            create = getattr(importlib.import_module(module_name), factory_name)
            if not callable(create) or inspect.iscoroutinefunction(create):
                raise TypeError("Factory must be synchronous")
            decomposer = create()
        except Exception:
            raise ModuleConfigurationError(
                "FACTORY_INITIALIZATION_FAILED", "The configured QA factory could not be initialized.",
            ) from None
    if not inspect.iscoroutinefunction(getattr(decomposer, "decompose", None)):
        raise ModuleConfigurationError("INVALID_MODULE", "The module must provide async decompose(query, history, context=...).")
    return SelectedDecomposer(decomposer=decomposer, metadata={
        "module": module,
        "factory": factory,
        "model_calls_allowed": allow_model_calls,
        "decomposer_is_test_double": test_double,
        "classification_performance_evaluated": False,
    })
