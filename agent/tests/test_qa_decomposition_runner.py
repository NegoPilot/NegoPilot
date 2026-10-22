"""Wiring checks with deterministic doubles, never actual model evaluation."""

from copy import deepcopy
import json
from pathlib import Path
import socket

from langsmith import get_tracing_context
import pytest
from pydantic import ValidationError

from negopilot_agent.decomposition import DecomposerSettings, QueryDecomposer
from negopilot_agent.qa_decomposition import runner
from negopilot_agent.qa_decomposition.fixtures import (
    FixtureDecomposer, FixtureToolCaller, INTERNAL_QUERY, LOOKUP_QUERY, routing_step,
)
from negopilot_agent.qa_decomposition.loader import FIXTURE_FACTORY

CASES_PATH = Path(__file__).resolve().parents[1] / 'qa/decomposer/cases.json'
META = {'module': 'qa', 'factory': FIXTURE_FACTORY, 'decomposer_is_test_double': True,
        'model_calls_allowed': False, 'classification_performance_evaluated': False}


def no_network(*args, **kwargs):
    raise AssertionError('Wiring verification must not connect to a network')


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    monkeypatch.setattr(socket.socket, 'connect', no_network)
    monkeypatch.setattr(socket.socket, 'connect_ex', no_network)


async def test_complete_fixture_suite_and_exact_history_context_forwarding(monkeypatch):
    monkeypatch.setenv('LANGSMITH_TRACING', 'true')
    monkeypatch.setenv('LANGSMITH_API_KEY', 'test-key-not-real')
    cases = runner.load_cases(CASES_PATH)
    before = [case.model_dump() for case in cases]
    decomposer = FixtureDecomposer()
    report = await runner.run_cases(cases, decomposer=decomposer, metadata=META)
    assert report['summary'] == {'total': 8, 'matched': 8, 'mismatched': 0}
    assert report['classification_performance_evaluated'] is False
    assert report['purpose'] == 'wiring_validation'
    assert decomposer.inputs == [case.model_dump(mode='json', exclude={'id', 'expected'}) for case in cases]
    assert decomposer.inputs[6]['history'] == before[6]['history']
    assert decomposer.inputs[6]['context'] == {'selected_supplier_ids': ['SUP-0001']}
    assert [case.model_dump() for case in cases] == before
    blocked = report['cases'][5]
    assert blocked['raw_output']['steps'][1]['operation'] == 'unsupported_operation'
    assert blocked['execution']['events'] == []
    assert blocked['execution']['normalized_plan'] is None
    assert json.loads(json.dumps(report)) == report


async def test_scoped_tracing_disabled_for_decomposer_and_caller(monkeypatch):
    monkeypatch.setenv('LANGSMITH_TRACING', 'true')
    observed = []

    class Decomposer(FixtureDecomposer):
        async def decompose(self, query, history=None, *, context=None):
            observed.append(get_tracing_context()['enabled'])
            return await super().decompose(query, history, context=context)

    class Caller(FixtureToolCaller):
        async def __call__(self, *args):
            observed.append(get_tracing_context()['enabled'])
            return await super().__call__(*args)

    report = await runner.run_cases([runner.load_cases(CASES_PATH)[1]], decomposer=Decomposer(),
                                    metadata=META, caller_factory=Caller)
    assert report['summary']['matched'] == 1
    assert observed == [False, False, False]


async def test_existing_query_decomposer_contract_with_backend_double():
    class Backend:
        def __init__(self):
            self.messages = []

        async def generate(self, system, user):
            self.messages.append(user)
            return json.dumps(routing_step(LOOKUP_QUERY)['decomposition'], ensure_ascii=False)

    backend = Backend()
    decomposer = QueryDecomposer(settings=DecomposerSettings(backend='openai', model='unused-test-model'),
                                 backend=backend)
    report = await runner.run_cases([runner.load_cases(CASES_PATH)[1]], decomposer=decomposer,
                                    metadata={**META, 'module': 'existing', 'backend_is_test_double': True})
    assert report['summary']['matched'] == 1
    assert len(backend.messages) == 1
    assert LOOKUP_QUERY in backend.messages[0]
    assert report['cases'][0]['raw_output']['schema_version'] == '0.1'
    assert report['cases'][0]['execution']['normalized_plan']['steps'][0]['id'] == 'routing'
    assert report['classification_performance_evaluated'] is False


async def test_case_failure_continues_without_exposing_exception_message():
    class FailsOnce(FixtureDecomposer):
        async def decompose(self, query, history=None, *, context=None):
            if query == INTERNAL_QUERY:
                raise RuntimeError('SECRET credentials or internal details')
            return await super().decompose(query, history, context=context)

    report = await runner.run_cases(runner.load_cases(CASES_PATH)[:2], decomposer=FailsOnce(), metadata=META)
    assert report['summary'] == {'total': 2, 'matched': 1, 'mismatched': 1}
    first = report['cases'][0]
    assert first['execution']['errors'][0]['code'] == 'DECOMPOSER_ERROR'
    assert first['comparison']['failures']
    assert report['cases'][1]['comparison']['matched']
    assert 'SECRET' not in json.dumps(report)


@pytest.mark.parametrize('raw', ['{invalid JSON', ['wrong shape'], {'unknown': 'retained', 'schema_version': '0.1'}])
async def test_invalid_raw_output_retained_and_calls_blocked(raw):
    class Invalid:
        async def decompose(self, *args, **kwargs):
            return deepcopy(raw)

    caller = FixtureToolCaller()
    report = await runner.run_cases([runner.load_cases(CASES_PATH)[0]], decomposer=Invalid(), metadata=META,
                                    caller_factory=lambda: caller)
    assert report['cases'][0]['raw_output'] == raw
    assert report['cases'][0]['execution']['status'] == 'validation_failed'
    assert caller.calls == []


async def test_comparison_records_status_order_args_results_and_codes():
    case = runner.load_cases(CASES_PATH)[2]
    report = await runner.run_cases([case], decomposer=FixtureDecomposer(), metadata=META)
    actual = deepcopy(report['cases'][0]['execution'])
    actual['status'] = 'failed'
    actual['classification'] = 'mcp'
    actual['events'] = list(reversed(actual['events']))
    actual['events'][0]['arguments']['discount_pct'] = '7'
    actual['final_result']['discounted_amount'] = '11160'
    expected = case.expected.model_copy(update={'error_code': 'MISSING_INPUT'})
    comparison = runner.compare(expected, actual)
    assert comparison['matched'] is False
    assert {item['path'] for item in comparison['failures']} == {
        'status', 'classification', 'calls', 'final_result.discounted_amount', 'errors'}


async def test_pure_mcp_quote_expectation_detects_empty_quotes():
    class EmptyQuotes(FixtureToolCaller):
        async def __call__(self, server, tool, args):
            result = await super().__call__(server, tool, args)
            if tool == 'get_supplier':
                result['recent_quotes'] = []
            return result

    report = await runner.run_cases([runner.load_cases(CASES_PATH)[1]], decomposer=FixtureDecomposer(),
                                    metadata=META, caller_factory=EmptyQuotes)
    record = report['cases'][0]
    assert record['execution']['status'] == 'completed'
    assert record['comparison']['matched'] is False
    assert record['comparison']['failures'][0]['path'].endswith('recent_quotes')


def test_case_input_reuses_history_and_selection_contracts(tmp_path):
    original = json.loads(CASES_PATH.read_text())[:1]
    path = tmp_path / 'bad.json'
    bad = deepcopy(original)
    bad[0]['context'] = {'selected_supplier_ids': ['SUP-0001', 'SUP-0002']}
    path.write_text(json.dumps(bad))
    with pytest.raises(ValidationError):
        runner.load_cases(path)
    bad = deepcopy(original)
    bad[0]['history'] = [{'role': 'system', 'content': 'disallowed'}]
    path.write_text(json.dumps(bad))
    with pytest.raises(ValidationError):
        runner.load_cases(path)
    path.write_text(json.dumps(original + original))
    with pytest.raises(ValueError, match='unique'):
        runner.load_cases(path)


def linked_worktree(tmp_path):
    root = tmp_path / 'qa-worktree'
    root.mkdir()
    (root / '.git').write_text('gitdir: placeholder')
    return root


@pytest.mark.parametrize('branch', ['main', 'master', 'feature/current-product', '', 'qa/something-else'])
def test_workspace_rejects_all_other_branches(tmp_path, monkeypatch, branch):
    root = linked_worktree(tmp_path)
    monkeypatch.setattr(runner, '_git', lambda *args: branch)
    with pytest.raises(ValueError, match='restricted'):
        runner.check_workspace(root)


def test_workspace_rejects_primary_accepts_isolated_branch(tmp_path, monkeypatch):
    primary = tmp_path / 'primary'
    (primary / '.git').mkdir(parents=True)
    with pytest.raises(ValueError, match='linked worktree'):
        runner.check_workspace(primary)
    root = linked_worktree(tmp_path)
    monkeypatch.setattr(runner, '_git', lambda root, *args: {
        ('branch', '--show-current'): runner.QA_BRANCH,
        ('rev-parse', 'HEAD'): 'test-commit',
        ('status', '--porcelain'): '?? qa file',
    }[args])
    assert runner.check_workspace(root) == {
        'branch': runner.QA_BRANCH, 'commit': 'test-commit', 'worktree': str(root), 'dirty': True}


def test_output_guard_rejects_escape_symlinks_and_overwrite(tmp_path):
    root = linked_worktree(tmp_path)
    allowed = root / runner.RESULTS_DIRECTORY
    allowed.mkdir(parents=True)
    outside = tmp_path / 'outside.json'
    for target in [outside, allowed / '..' / 'escaped.json', allowed / 'report.txt']:
        with pytest.raises(ValueError):
            runner.checked_output_path(root, target)
    redirect = allowed / 'redirect.json'
    redirect.symlink_to(outside)
    with pytest.raises(ValueError):
        runner.checked_output_path(root, redirect)
    target = runner.checked_output_path(root, allowed / 'normal.json')
    runner.write_report({'test': True}, target)
    assert json.loads(target.read_text()) == {'test': True}
    with pytest.raises(ValueError, match='already exists'):
        runner.checked_output_path(root, target)
    with pytest.raises(FileExistsError):
        runner.write_report({'test': False}, target)
    assert json.loads(target.read_text()) == {'test': True}


def test_output_guard_rejects_symlinked_results_ancestor(tmp_path):
    root = linked_worktree(tmp_path)
    external = tmp_path / 'other'
    external.mkdir()
    (root / 'agent').symlink_to(external, target_is_directory=True)
    with pytest.raises(ValueError, match='symlink'):
        runner.checked_output_path(root, None)


def test_cli_blocks_main_before_module_load(tmp_path, monkeypatch, capsys):
    root = linked_worktree(tmp_path)
    monkeypatch.setattr(runner, 'REPOSITORY_ROOT', root)
    monkeypatch.setattr(runner, '_git', lambda *args: 'main')
    monkeypatch.setattr(runner, 'select_decomposer', no_network)
    assert runner.main(['--module', 'qa', '--factory', FIXTURE_FACTORY]) == 2
    assert 'restricted' in capsys.readouterr().out
    assert not (root / runner.RESULTS_DIRECTORY).exists()


def test_cli_fixture_report_and_output_rejection(tmp_path, monkeypatch, capsys):
    root = linked_worktree(tmp_path)
    monkeypatch.setattr(runner, 'REPOSITORY_ROOT', root)
    monkeypatch.setattr(runner, 'check_workspace', lambda root: {'branch': runner.QA_BRANCH})
    args = ['--module', 'qa', '--factory', FIXTURE_FACTORY, '--io', 'test-double', '--cases', str(CASES_PATH)]
    assert runner.main(args + ['--output', str(tmp_path / 'outside.json')]) == 2
    assert not (tmp_path / 'outside.json').exists()
    target = root / runner.RESULTS_DIRECTORY / 'run.json'
    assert runner.main(args + ['--output', str(target)]) == 0
    report = json.loads(target.read_text())
    assert report['summary']['matched'] == 8
    assert report['provenance']['branch'] == runner.QA_BRANCH
    assert 'performance was not evaluated' in capsys.readouterr().out


async def test_nonfinite_model_output_retained_as_text_without_aborting_report():
    from pydantic import BaseModel

    class InvalidModel(BaseModel):
        value: float

    class Invalid:
        async def decompose(self, *args, **kwargs):
            return InvalidModel(value=float('nan'))

    caller = FixtureToolCaller()
    report = await runner.run_cases([runner.load_cases(CASES_PATH)[0]], decomposer=Invalid(), metadata=META,
                                    caller_factory=lambda: caller)
    record = report['cases'][0]
    assert record['raw_output_encoding'] == 'nonstandard_json_text'
    assert record['raw_output'] == '{"value": NaN}'
    assert record['execution']['errors'][0]['code'] == 'OUTPUT_SERIALIZATION_ERROR'
    assert caller.calls == []
    json.dumps(report, allow_nan=False)


async def test_cyclic_python_output_records_failure_and_continues():
    class InvalidOnce(FixtureDecomposer):
        async def decompose(self, query, history=None, *, context=None):
            if query == INTERNAL_QUERY:
                raw = {}
                raw['cycle'] = raw
                return raw
            return await super().decompose(query, history, context=context)

    report = await runner.run_cases(runner.load_cases(CASES_PATH)[:2],
                                    decomposer=InvalidOnce(), metadata=META)
    assert report['cases'][0]['raw_output_encoding'] == 'unsupported_python_type'
    assert report['cases'][0]['execution']['events'] == []
    assert report['cases'][1]['comparison']['matched']
    json.dumps(report, allow_nan=False)
