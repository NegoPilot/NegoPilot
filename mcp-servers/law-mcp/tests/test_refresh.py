import copy
import json
import threading
from contextlib import contextmanager

import pytest
from starlette.testclient import TestClient

import server
import law_refresh
from law_refresh import LawRefreshManager, RefreshBusy, dataset_id, validate_dataset


def manager(tmp_path, collector):
    snapshots = []
    return LawRefreshManager(tmp_path, server.LAWS, lambda laws, at: snapshots.append((laws, at)), collector), snapshots


def test_success_retains_history_and_survives_restart(tmp_path):
    old = copy.deepcopy(server.LAWS)
    new = copy.deepcopy(old)
    law = next(iter(new.values()))
    v = copy.deepcopy(law['versions'][-1])
    v['effective_date'] = '2099-01-01'
    v['commit'] = 'a' * 40
    law['versions'] = [v]
    refresh, snapshots = manager(tmp_path, lambda _: new)
    job = refresh.start()
    refresh.worker.join(5)
    done = refresh.get(job['job_id'])
    assert done['status'] == 'SUCCEEDED'
    key = law['law_key']
    assert len(snapshots[0][0][key]['versions']) == len(old[key]['versions']) + 1
    refresh.file_lock.close()
    restored, again = manager(tmp_path, lambda _: pytest.fail('No fetch on restart'))
    assert restored.latest() == done
    assert dataset_id(again[0][0]) == done['dataset_id']
    restored.file_lock.close()


@pytest.mark.parametrize('failure', ['partial', 'invalid', 'network'])
def test_failure_keeps_active_data_and_reports_safe_error(tmp_path, failure):
    old = copy.deepcopy(server.LAWS)
    def collector(_):
        if failure == 'network':
            raise OSError('credential must not appear')
        result = copy.deepcopy(old)
        if failure == 'partial':
            result.pop(next(iter(result)))
        else:
            next(iter(result.values()))['versions'][0]['articles'] = []
        return result
    refresh, snapshots = manager(tmp_path, collector)
    job = refresh.start()
    refresh.worker.join(5)
    done = refresh.get(job['job_id'])
    assert done['status'] == 'FAILED' and done['error_code'] == 'REFRESH_FAILED'
    assert done['dataset_id'] is None
    assert not snapshots and refresh.laws == old
    assert not (tmp_path / 'active.json').exists()
    assert 'credential' not in json.dumps(done)
    refresh.file_lock.close()


def test_single_inflight_job_and_runtime_owner(tmp_path):
    gate = threading.Event()
    def collector(_):
        gate.wait(3)
        return copy.deepcopy(server.LAWS)
    refresh, _ = manager(tmp_path, collector)
    refresh.start()
    with pytest.raises(RefreshBusy):
        refresh.start()
    with pytest.raises(BlockingIOError):
        manager(tmp_path, collector)
    gate.set()
    refresh.worker.join(5)
    refresh.file_lock.close()


def test_activation_clears_cached_search_and_updates_catalog():
    old = server.LAWS
    try:
        server.search_units(tuple(old), '2026-10-21', False)
        assert server._search_cache
        new = copy.deepcopy(old)
        next(iter(new.values()))['law_name'] = 'updated name'
        server.activate_dataset(new, '2026-10-21T00:00:00Z')
        assert not server._search_cache
        assert server.admin_law_catalog()['collected_at'] == '2026-10-21T00:00:00Z'
        assert server.admin_law_catalog()['dataset_id'] == dataset_id(new)
    finally:
        server.activate_dataset(old, None)


def test_refresh_http_requires_key_and_does_not_accept_custom_law(tmp_path, monkeypatch):
    refresh, _ = manager(tmp_path, lambda _: copy.deepcopy(server.LAWS))
    monkeypatch.setattr(server, 'REFRESH_MANAGER', refresh)
    monkeypatch.setattr(server, 'LAW_ADMIN_API_KEY', 'admin-key')
    headers = {'X-Law-Admin-Key': 'admin-key'}
    with TestClient(server.mcp.http_app()) as api:
        assert api.post('/admin/refresh-jobs').status_code == 401
        assert api.post('/admin/refresh-jobs', headers=headers, json={'url': 'https://untrusted'}).status_code == 400
        assert api.get('/admin/refresh-jobs', headers=headers).json() == {'item': None}
        response = api.post('/admin/refresh-jobs', headers=headers)
        assert response.status_code == 202
        refresh.worker.join(5)
        status = api.get('/admin/refresh-jobs/' + response.json()['job_id'], headers=headers)
        assert status.json()['status'] == 'SUCCEEDED'
        assert api.get('/admin/refresh-jobs/missing', headers=headers).status_code == 404
    refresh.file_lock.close()


def test_restart_marks_interrupted_jobs_failed(tmp_path):
    job = {'job_id': 'interrupted', 'status': 'RUNNING', 'started_at': '2026-10-20T00:00:00Z',
           'finished_at': None, 'previous_dataset_id': 'x', 'dataset_id': None, 'error_code': None}
    (tmp_path / 'job-interrupted.json').write_text(json.dumps(job))
    refresh, _ = manager(tmp_path, lambda _: None)
    assert refresh.latest()['error_code'] == 'REFRESH_INTERRUPTED'
    refresh.file_lock.close()


def complete(refresh):
    job = refresh.start()
    refresh.worker.join(5)
    assert not refresh.worker.is_alive()
    return refresh.get(job['job_id'])


def changed_laws():
    laws = copy.deepcopy(server.LAWS)
    next(iter(laws.values()))['law_name'] = 'Updated law name'
    return laws


def test_staging_cleanup_failure_preserves_previous_disk_and_memory(tmp_path, monkeypatch):
    refresh, snapshots = manager(tmp_path, lambda _: copy.deepcopy(server.LAWS))
    try:
        assert complete(refresh)['status'] == 'SUCCEEDED'
        previous_file = refresh.active_path.read_bytes()
        previous_laws = copy.deepcopy(refresh.laws)
        previous_time = refresh.collected_at
        snapshots.clear()
        refresh.collector = lambda _: changed_laws()
        temporary_directory = law_refresh.tempfile.TemporaryDirectory

        @contextmanager
        def cleanup_failure(*args, **kwargs):
            with temporary_directory(*args, **kwargs) as staging:
                yield staging
            raise OSError('secret cleanup detail')

        monkeypatch.setattr(law_refresh.tempfile, 'TemporaryDirectory', cleanup_failure)
        result = complete(refresh)
        assert result['status'] == 'FAILED' and result['dataset_id'] is None
        assert refresh.active_path.read_bytes() == previous_file
        assert refresh.laws == previous_laws and refresh.collected_at == previous_time
        assert snapshots == []
        assert 'secret' not in json.dumps(result)
    finally:
        refresh.file_lock.close()


@pytest.mark.parametrize('previous_file_exists', [False, True])
@pytest.mark.parametrize('fail_after_memory_swap', [False, True])
def test_activation_failure_restores_exact_file_and_served_snapshot(tmp_path, previous_file_exists, fail_after_memory_swap):
    view = {'laws': copy.deepcopy(server.LAWS), 'collected_at': None}
    fail = False

    def activate(laws, collected_at):
        nonlocal fail
        failing = fail
        fail = False
        if not failing or fail_after_memory_swap:
            view.update(laws=laws, collected_at=collected_at)
        if failing:
            raise RuntimeError('private callback error')

    refresh = LawRefreshManager(tmp_path, server.LAWS, activate, lambda _: copy.deepcopy(server.LAWS))
    try:
        if previous_file_exists:
            assert complete(refresh)['status'] == 'SUCCEEDED'
        before = refresh.active_path.read_bytes() if previous_file_exists else None
        old_laws = copy.deepcopy(view['laws'])
        old_time = view['collected_at']
        refresh.collector = lambda _: changed_laws()
        fail = True
        result = complete(refresh)
        assert result['status'] == 'FAILED' and result['dataset_id'] is None
        assert view == {'laws': old_laws, 'collected_at': old_time}
        assert refresh.laws == old_laws and refresh.collected_at == old_time
        assert (refresh.active_path.read_bytes() if refresh.active_path.exists() else None) == before
        assert 'private' not in json.dumps(result)
        # A failed callback does not prevent the next successful refresh.
        assert complete(refresh)['status'] == 'SUCCEEDED'
    finally:
        refresh.file_lock.close()


@pytest.mark.parametrize('write_before_failure', [False, True])
def test_success_job_persistence_failure_does_not_reverse_commit(tmp_path, monkeypatch, write_before_failure):
    refresh, snapshots = manager(tmp_path, lambda _: changed_laws())
    original = law_refresh.atomic_json

    def fail_success_record(path, value):
        if path.name.startswith('job-') and value['status'] == 'SUCCEEDED':
            if write_before_failure:
                original(path, value)
            raise OSError('private disk error')
        original(path, value)

    monkeypatch.setattr(law_refresh, 'atomic_json', fail_success_record)
    try:
        result = complete(refresh)
        assert result['status'] == 'SUCCEEDED' and result['error_code'] is None
        active = json.loads(refresh.active_path.read_text())
        assert active['job_id'] == result['job_id']
        assert active['dataset_id'] == result['dataset_id'] == dataset_id(snapshots[-1][0])
        assert 'private' not in json.dumps(result)
    finally:
        refresh.file_lock.close()
    monkeypatch.setattr(law_refresh, 'atomic_json', original)
    restored, snapshots = manager(tmp_path, lambda _: pytest.fail('Restart must not collect'))
    try:
        assert restored.latest()['status'] == 'SUCCEEDED'
        assert restored.latest()['dataset_id'] == result['dataset_id']
        assert dataset_id(snapshots[-1][0]) == result['dataset_id']
    finally:
        restored.file_lock.close()


def test_active_file_write_failure_keeps_old_dataset(tmp_path, monkeypatch):
    refresh, snapshots = manager(tmp_path, lambda _: copy.deepcopy(server.LAWS))
    try:
        assert complete(refresh)['status'] == 'SUCCEEDED'
        before = refresh.active_path.read_bytes()
        old_laws = copy.deepcopy(refresh.laws)
        snapshots.clear()
        refresh.collector = lambda _: changed_laws()
        original = law_refresh.atomic_json

        def write_failure(path, value):
            if path == refresh.active_path:
                raise OSError('private disk error')
            original(path, value)

        monkeypatch.setattr(law_refresh, 'atomic_json', write_failure)
        result = complete(refresh)
        assert result['status'] == 'FAILED'
        assert refresh.active_path.read_bytes() == before
        assert refresh.laws == old_laws and snapshots == []
    finally:
        refresh.file_lock.close()


@pytest.mark.parametrize('scope,field,value', [
    ('law', 'law_name', ' '),
    ('version', 'promulgation_no', ''),
    ('version', 'promulgation_no', 123),
    ('version', 'law_name', None),
    ('version', 'law_type', None),
    ('version', 'ministry', 'Ministry'),
    ('version', 'ministry', [None]),
    ('version', 'effective_date', '20260101'),
    ('version', 'promulgation_date', '2026-02-30'),
    ('version', 'source_url', 'https://'),
    ('version', 'source_url', 'https://user:secret@example.com/law'),
    ('version', 'source_url', 'https://bad..host/law'),
    ('version', 'source_url', 'https://example.com:invalid/law'),
    ('version', 'source_url', 'https://example.com/a b'),
    ('version', 'source_url', 'file:///tmp/private'),
    ('version', 'raw_file', 1),
    ('article', 'title', None),
    ('article', 'chapter', []),
    ('article', 'section', False),
    ('article', 'deleted', 1),
    ('article', 'text', None),
    ('article', 'paragraphs', {}),
    ('article', 'unexpected', 'extra'),
    ('paragraph', 'no', 1),
    ('paragraph', 'text', None),
    ('paragraph', 'unexpected', 'extra'),
    ('version', 'addenda', [{'title': 3, 'text': ''}]),
])
def test_consumer_incompatible_metadata_rejected_before_commit(tmp_path, scope, field, value):
    invalid = copy.deepcopy(server.LAWS)
    law = next(iter(invalid.values()))
    version = law['versions'][0]
    article = version['articles'][0]
    targets = {'law': law, 'version': version, 'article': article, 'paragraph': article['paragraphs'][0]}
    targets[scope][field] = value
    refresh, snapshots = manager(tmp_path, lambda _: invalid)
    try:
        result = complete(refresh)
        assert result['status'] == 'FAILED' and result['error_code'] == 'REFRESH_FAILED'
        assert refresh.laws == server.LAWS and not snapshots
        assert not refresh.active_path.exists()
    finally:
        refresh.file_lock.close()


def test_empty_source_heading_allowed_but_empty_whole_version_rejected():
    laws = copy.deepcopy(server.LAWS)
    version = next(iter(laws.values()))['versions'][0]
    article = version['articles'][0]
    article.update(title='', text='', paragraphs=[{'no': None, 'text': ''}])
    validate_dataset(laws, server.LAWS)
    for item in version['articles']:
        item['text'] = ''
    with pytest.raises(ValueError, match='Empty version'):
        validate_dataset(laws, server.LAWS)


@pytest.mark.parametrize('collected_at', ['not-a-time', '2026-10-21T12:00:00', '2026-10-21 12:00:00Z'])
def test_restart_rejects_timestamp_incompatible_with_catalog(tmp_path, collected_at):
    saved = {'job_id': 'saved', 'dataset_id': dataset_id(server.LAWS), 'collected_at': collected_at, 'laws': server.LAWS}
    (tmp_path / 'active.json').write_text(json.dumps(saved))
    with pytest.raises(ValueError):
        manager(tmp_path, lambda _: pytest.fail('Restart must not collect'))
