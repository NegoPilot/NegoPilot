from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from negopilot_agent.api.app import create_app
from negopilot_agent.api.settings import ApiSettings

HEADERS = {'X-Internal-Api-Key': 'test-key'}
ROOT = '/internal/v1/admin/laws/refresh-jobs'
JOB = {'job_id': str(uuid4()), 'status': 'RUNNING', 'started_at': '2026-10-21T00:00:00Z',
       'finished_at': None, 'previous_dataset_id': 'a' * 64, 'dataset_id': None, 'error_code': None}


def client(control):
    return TestClient(create_app(ApiSettings(mode='stub', internal_api_key='test-key'), law_refresh_control=control))


def test_start_latest_and_status():
    async def control(method, suffix):
        if method == 'POST':
            return 202, JOB
        return 200, JOB if suffix else {'item': JOB}
    api = client(control)
    assert api.post(ROOT, headers=HEADERS).status_code == 202
    assert api.get(ROOT, headers=HEADERS).json()['data']['item'] == JOB
    assert api.get(ROOT + '/' + JOB['job_id'], headers=HEADERS).json()['data'] == JOB


@pytest.mark.parametrize('method,path,body', [('POST', ROOT, '{}'), ('GET', ROOT + '?law_key=x', None),
                                           ('GET', ROOT + '/invalid', None)])
def test_invalid_requests_never_call_control(method, path, body):
    async def control(*args):
        pytest.fail('Must reject before control call')
    assert client(control).request(method, path, content=body, headers=HEADERS).status_code == 400


def test_internal_authentication_required():
    async def control(*args):
        pytest.fail('Must authenticate first')
    assert client(control).post(ROOT).status_code == 401


@pytest.mark.parametrize('status,expected', [(400, 400), (404, 404), (409, 409), (503, 503), (504, 504), (401, 502), (302, 502)])
def test_upstream_errors_are_safe(status, expected):
    async def control(*args):
        return status, {'secret': 'never disclose'}
    response = client(control).post(ROOT, headers=HEADERS)
    assert response.status_code == expected
    assert 'never disclose' not in response.text


def test_wrong_job_and_inconsistent_success_rejected():
    async def control(*args):
        return 200, JOB
    assert client(control).get(ROOT + '/' + str(uuid4()), headers=HEADERS).status_code == 502
    async def bad(*args):
        return 202, dict(JOB, status='SUCCEEDED')
    assert client(bad).post(ROOT, headers=HEADERS).status_code == 502
