"""Only one replacement module is selected. No network/model calls in these tests."""

import types

import pytest

from negopilot_agent.qa_decomposition import loader


@pytest.fixture(autouse=True)
def clear_qa_env(monkeypatch):
    monkeypatch.delenv("NEGOPILOT_QA_DECOMPOSER", raising=False)
    monkeypatch.delenv("NEGOPILOT_QA_DECOMPOSER_FACTORY", raising=False)


def test_default_existing_module_requires_explicit_model_opt_in(monkeypatch):
    import negopilot_agent.decomposition as module
    monkeypatch.setattr(module, "QueryDecomposer", lambda: pytest.fail("Must not instantiate a live module"))
    with pytest.raises(loader.ModuleConfigurationError) as error:
        loader.select_decomposer()
    assert error.value.code == "MODEL_CALLS_NOT_ALLOWED"


@pytest.mark.asyncio
async def test_existing_selection_uses_one_original_module_and_forwards_inputs(monkeypatch):
    import negopilot_agent.decomposition as module
    constructed, inputs = [], []

    class Original:
        def __init__(self):
            constructed.append(self)

        async def decompose(self, query, history=None, *, context=None):
            inputs.append((query, history, context))
            return {"original": True}

    monkeypatch.setattr(module, "QueryDecomposer", Original)
    monkeypatch.setenv("AGENT_EXECUTION_MODE", "live")
    selected = loader.select_decomposer("existing", allow_model_calls=True)
    history = [{"role": "user", "content": "앞선 질문"}]
    context = {"selected_supplier_ids": ["SUP-0001"]}
    assert await selected.decomposer.decompose("이번 질문", history, context=context) == {"original": True}
    assert inputs == [("이번 질문", history, context)] and len(constructed) == 1
    assert selected.metadata["decomposer_is_test_double"] is False
    assert loader.os.environ["AGENT_EXECUTION_MODE"] == "live"


@pytest.mark.asyncio
async def test_qa_replaces_original_without_creating_a_front_classifier(monkeypatch):
    import negopilot_agent.decomposition as module
    monkeypatch.setattr(module, "QueryDecomposer", lambda: pytest.fail("Original must not be instantiated"))
    calls = []

    class Replacement:
        async def decompose(self, query, history=None, *, context=None):
            calls.append((query, history, context))
            return {"replacement": True}

    monkeypatch.setattr(loader.importlib, "import_module", lambda _: types.SimpleNamespace(create=Replacement))
    monkeypatch.setenv("NEGOPILOT_QA_DECOMPOSER", "qa")
    monkeypatch.setenv("NEGOPILOT_QA_DECOMPOSER_FACTORY", "example.qa:create")
    selected = loader.select_decomposer(allow_model_calls=True)
    assert await selected.decomposer.decompose("q", [], context={"selected_supplier_ids": []}) == {"replacement": True}
    assert calls == [("q", [], {"selected_supplier_ids": []})]
    assert selected.metadata["factory"] == "example.qa:create"


@pytest.mark.parametrize("module,factory,code", [
    ("unknown", None, "UNKNOWN_DECOMPOSER"),
    ("qa", None, "INVALID_FACTORY"),
    ("qa", "module:factory()", "INVALID_FACTORY"),
    ("qa", "module:object.factory", "INVALID_FACTORY"),
    ("existing", "module:factory", "UNEXPECTED_FACTORY"),
    ("qa", "example.qa:create", "MODEL_CALLS_NOT_ALLOWED"),
])
def test_bad_configuration_never_imports_or_falls_back(monkeypatch, module, factory, code):
    monkeypatch.setattr(loader.importlib, "import_module", lambda _: pytest.fail("No imports expected"))
    with pytest.raises(loader.ModuleConfigurationError) as error:
        loader.select_decomposer(module, factory=factory)
    assert error.value.code == code


def test_factory_errors_do_not_expose_internal_diagnostics(monkeypatch):
    def fail():
        raise RuntimeError("private model credential or internal URL")

    monkeypatch.setattr(loader.importlib, "import_module", lambda _: types.SimpleNamespace(create=fail))
    with pytest.raises(loader.ModuleConfigurationError) as error:
        loader.select_decomposer("qa", factory="example.qa:create", allow_model_calls=True)
    assert error.value.code == "FACTORY_INITIALIZATION_FAILED"
    assert "private" not in str(error.value)


def test_sync_decomposer_is_rejected(monkeypatch):
    monkeypatch.setattr(loader.importlib, "import_module", lambda _: types.SimpleNamespace(create=lambda: object()))
    with pytest.raises(loader.ModuleConfigurationError) as error:
        loader.select_decomposer("qa", factory="example.qa:create", allow_model_calls=True)
    assert error.value.code == "INVALID_MODULE"
