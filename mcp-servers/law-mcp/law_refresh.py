"""Administrator-only refresh of the six existing datasets.

The model has no refresh tool. A single law server process owns a runtime directory.
A validated dataset is committed as one file before switching the in-memory snapshot.
"""
import copy
import fcntl
import hashlib
import importlib.util
import ipaddress
import json
import os
import re
import tempfile
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4


def now():
    return datetime.now(timezone.utc).isoformat()


def dataset_id(laws):
    return hashlib.sha256(json.dumps(laws, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def atomic_bytes(path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('wb') as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def atomic_json(path, value):
    atomic_bytes(path, json.dumps(value, ensure_ascii=False).encode('utf-8'))


def text(value, *, nonblank=False, nullable=False):
    if nullable and value is None:
        return
    if not isinstance(value, str) or (nonblank and not value.strip()):
        raise ValueError('Invalid text')


def iso_date(value):
    text(value)
    if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
        raise ValueError('Invalid date')
    date.fromisoformat(value)


def instant(value):
    text(value)
    if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?(?:Z|[+-][0-9]{2}:[0-9]{2})', value):
        raise ValueError('Invalid timestamp')
    if datetime.fromisoformat(value).utcoffset() is None:
        raise ValueError('Missing timestamp offset')


def source_url(value):
    text(value, nonblank=True)
    if re.search(r'[\s\x00-\x1f\x7f<>"{}|\\^`]', value) or re.search(r'%(?![0-9a-fA-F]{2})', value):
        raise ValueError('Invalid source URL')
    url = urlsplit(value)
    if url.scheme not in ('http', 'https') or not url.hostname or url.username is not None or url.password is not None:
        raise ValueError('Invalid source URL')
    # Match the Java URI consumer's ASCII host contract. No URL is fetched here.
    host = url.hostname
    if ':' in host:
        ipaddress.IPv6Address(host)
    elif re.fullmatch(r'[0-9.]+', host):
        ipaddress.IPv4Address(host)
    elif len(host) > 253 or any(not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label)
                               for label in host.removesuffix('.').split('.')):
        raise ValueError('Invalid source host')
    if url.port is not None and not 0 < url.port <= 65535:
        raise ValueError('Invalid source port')


def validate_dataset(laws, expected):
    if not isinstance(laws, dict) or set(laws) != set(expected):
        raise ValueError('Incomplete dataset')
    for key, law in laws.items():
        if not isinstance(law, dict) or law.get('law_key') != key or not isinstance(law.get('versions'), list) or not law['versions']:
            raise ValueError('Invalid law')
        if not isinstance(key, str) or not re.fullmatch('[a-z_]{1,80}', key):
            raise ValueError('Invalid law key')
        text(law.get('law_name'), nonblank=True)
        seen = set()
        for v in law['versions']:
            if not isinstance(v, dict):
                raise ValueError('Invalid version')
            iso_date(v.get('effective_date'))
            iso_date(v.get('promulgation_date'))
            for field in ('law_name', 'law_type', 'promulgation_no', 'raw_file'):
                text(v.get(field), nonblank=True)
            if not isinstance(v.get('ministry'), list):
                raise ValueError('Invalid ministry')
            for ministry in v['ministry']:
                text(ministry, nonblank=True)
            source_url(v.get('source_url'))
            if not isinstance(v.get('commit'), str) or not re.fullmatch('[a-f0-9]{40}', v['commit']):
                raise ValueError('Missing provenance')
            identity = (v['effective_date'], v['commit'])
            if identity in seen or not isinstance(v.get('articles'), list) or not v['articles']:
                raise ValueError('Invalid version')
            seen.add(identity)
            numbers = set()
            for a in v['articles']:
                if not isinstance(a, dict) or set(a) != {'no', 'title', 'chapter', 'section', 'deleted', 'paragraphs', 'text'}:
                    raise ValueError('Invalid article fields')
                if not isinstance(a['no'], str) or not re.fullmatch(r'제[0-9]+조(?:의[0-9]+)?', a['no']) or a['no'] in numbers:
                    raise ValueError('Invalid article')
                numbers.add(a['no'])
                text(a['title'])
                text(a['text'])
                text(a['chapter'], nullable=True)
                text(a['section'], nullable=True)
                if not isinstance(a['deleted'], bool) or not isinstance(a['paragraphs'], list):
                    raise ValueError('Invalid article types')
                for p in a['paragraphs']:
                    if not isinstance(p, dict) or set(p) != {'no', 'text'}:
                        raise ValueError('Invalid paragraph fields')
                    text(p['no'], nullable=True)
                    text(p['text'])
            # Preserve empty numbered source headings without inventing text.
            if not any(a['text'].strip() for a in v['articles']):
                raise ValueError('Empty version')
            if not isinstance(v.get('addenda'), list):
                raise ValueError('Invalid addenda')
            for addendum in v['addenda']:
                if not isinstance(addendum, dict) or set(addendum) != {'title', 'text'}:
                    raise ValueError('Invalid addendum fields')
                text(addendum['title'])
                text(addendum['text'])


def collect_laws(staging):
    """Use the existing collector/parser with isolated paths. Fail the whole job on error."""
    scripts = Path(__file__).resolve().parent / 'scripts'
    modules = []
    for name in ('fetch_laws', 'build_index'):
        spec = importlib.util.spec_from_file_location(name, scripts / (name + '.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.ROOT = staging
        module.RAW_DIR = staging / 'data' / 'raw'
        modules.append(module)
    fetch, build = modules
    result = {}
    for target in fetch.TARGETS:
        versions = fetch.fetch_target(target, 6)
        if not versions:
            raise ValueError('No versions collected')
        result[target['law_key']] = build.build_law(target['law_key'], {'versions': versions})
    return result


class RefreshBusy(Exception):
    pass


class LawRefreshManager:
    def __init__(self, root, initial, activate, collector=collect_laws):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.file_lock = (self.root / 'owner.lock').open('a')
        # Prevent a second worker from refreshing a dataset while the first serves stale memory.
        fcntl.flock(self.file_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.lock = threading.RLock()
        self.collector, self.activate = collector, activate
        self.laws = copy.deepcopy(initial)
        self.collected_at = None
        self.jobs = {}
        self.worker = None
        self.active_path = self.root / 'active.json'
        if self.active_path.exists():
            saved = json.loads(self.active_path.read_text())
            validate_dataset(saved['laws'], initial)
            if saved['dataset_id'] != dataset_id(saved['laws']):
                raise ValueError('Corrupted active dataset')
            instant(saved['collected_at'])
            self.laws = saved['laws']
            self.collected_at = saved['collected_at']
            activate(self.laws, saved['collected_at'])
        for path in self.root.glob('job-*.json'):
            job = json.loads(path.read_text())
            if job['status'] == 'RUNNING':
                # An active file carries the job id to recover a crash after commit.
                saved = json.loads(self.active_path.read_text()) if self.active_path.exists() else {}
                if saved.get('job_id') == job['job_id']:
                    job.update(status='SUCCEEDED', dataset_id=saved['dataset_id'], error_code=None, finished_at=now())
                else:
                    job.update(status='FAILED', error_code='REFRESH_INTERRUPTED', finished_at=now())
                atomic_json(path, job)
            self.jobs[job['job_id']] = job

    def start(self):
        with self.lock:
            if self.worker and self.worker.is_alive():
                raise RefreshBusy()
            job = {'job_id': str(uuid4()), 'status': 'RUNNING', 'started_at': now(), 'finished_at': None,
                   'previous_dataset_id': dataset_id(self.laws), 'dataset_id': None, 'error_code': None}
            atomic_json(self.root / ('job-' + job['job_id'] + '.json'), job)
            self.jobs[job['job_id']] = job
            self.worker = threading.Thread(target=self._run, args=(job['job_id'],), daemon=True)
            self.worker.start()
            return dict(job)

    def get(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            return dict(job) if job else None

    def latest(self):
        with self.lock:
            return dict(max(self.jobs.values(), key=lambda j: j['started_at'])) if self.jobs else None

    def _run(self, job_id):
        committed = False
        try:
            with tempfile.TemporaryDirectory(prefix='staging-', dir=self.root) as staging:
                fetched = self.collector(Path(staging))
                validate_dataset(fetched, self.laws)
                merged = copy.deepcopy(self.laws)
                for key, law in fetched.items():
                    # Retain historical versions beyond the collector's latest six commits.
                    versions = {(v['effective_date'], v['commit']): v for v in merged[key]['versions']}
                    versions.update({(v['effective_date'], v['commit']): v for v in law['versions']})
                    merged[key] = dict(law, versions=sorted(versions.values(), key=lambda v: (v['effective_date'], v['promulgation_date'])))
                validate_dataset(merged, self.laws)
            # Staging cleanup is part of preparation. It must finish before publication.
            collected_at = now()
            instant(collected_at)
            identity = dataset_id(merged)
            with self.lock:
                previous_file = self.active_path.read_bytes() if self.active_path.exists() else None
                atomic_json(self.active_path, {'job_id': job_id, 'dataset_id': identity,
                                              'collected_at': collected_at, 'laws': merged})
                try:
                    self.activate(merged, collected_at)
                except Exception:
                    # A callback may have swapped memory before raising. Restore both sides.
                    if previous_file is None:
                        self.active_path.unlink()
                    else:
                        atomic_bytes(self.active_path, previous_file)
                    self.activate(self.laws, self.collected_at)
                    raise
                self.laws, self.collected_at = merged, collected_at
                self.jobs[job_id].update(status='SUCCEEDED', dataset_id=identity, finished_at=now())
                committed = True
        except Exception:
            # No upstream response body or credentials are included in the job error.
            with self.lock:
                if not committed:
                    self.jobs[job_id].update(status='FAILED', error_code='REFRESH_FAILED', finished_at=now())
        finally:
            with self.lock:
                try:
                    atomic_json(self.root / ('job-' + job_id + '.json'), self.jobs[job_id])
                except OSError:
                    # Keep the committed result. Startup recovers it using active.json's job_id.
                    pass
