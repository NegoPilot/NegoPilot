"""가짜 MCP로 메일 전달 계약과 발송 재시도 금지를 확인한다."""

import asyncio
from uuid import UUID, uuid4

import httpx2 as httpx
import pytest
from fastmcp.exceptions import ToolError

from negopilot_agent.api.app import create_app
from negopilot_agent.api.mail_api import MAIL_ERRORS
from negopilot_agent.api.settings import ApiSettings
from negopilot_agent.routing.mcp_client import McpToolCaller

KEY = {'X-Internal-Api-Key': 'mail-test'}


class MailCaller:
    def __init__(self, result=None, error=None, delay=0):
        self.result = result if result is not None else {'confirmation_token': 'server-token'}
        self.error, self.delay, self.calls = error, delay, []

    async def __call__(self, server, tool, args):
        self.calls.append((server, tool, args))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.result


def payload(send=False):
    value = {'request_id': str(uuid4()), 'document': {'document_id': 'd1', 'version': '2',
             'content_hash': 'untouched'}, 'attachments': [{'filename': 'offer.pdf',
             'content_type': 'application/pdf', 'data_base64': 'cGRm'}]}
    if send:
        value.update(confirmation_token='token', requested_by='member-1', contact_id=7, client_ref='send-1')
    return value


async def request(caller, path, body=None, *, headers=KEY, timeout=30):
    app = create_app(ApiSettings(internal_api_key='mail-test', mail_send_timeout_seconds=timeout),
                     mail_caller=caller)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        if body is None:
            return await client.get(path, headers=headers)
        return await client.post(path, json=body, headers=headers)


@pytest.mark.parametrize('send', [False, True])
async def test_forward_preserves_payload_and_tool_result(send):
    caller = MailCaller()
    body = payload(send) | {'contact_id': 7, 'message': '담당자 문구'}
    name = 'send' if send else 'preview'
    response = await request(caller, f'/internal/offer-mails/{name}', body)
    assert response.status_code == 200
    assert response.json() == {'request_id': body['request_id'], 'result': caller.result}
    assert caller.calls == [('mail-mcp', f'{name}_offer_mail',
                             {k: v for k, v in body.items() if k != 'request_id'})]


@pytest.mark.parametrize('suffix,expected', [('', {'document_id': 'd1'}),
    ('&client_ref=send-1', {'document_id': 'd1', 'client_ref': 'send-1'})])
async def test_list_filter(suffix, expected):
    caller = MailCaller(result=[{'send_id': 's1', 'client_ref': 'send-1'}])
    response = await request(caller, '/internal/offer-mails?document_id=d1' + suffix)
    assert response.status_code == 200
    assert UUID(response.json()['request_id'])
    assert response.json()['result'] == caller.result
    assert caller.calls == [('mail-mcp', 'list_offer_mail_sends', expected)]


@pytest.mark.parametrize('code', sorted(MAIL_ERRORS))
async def test_tool_errors_return_422(code):
    caller = MailCaller(error=ToolError(f'[{code}] 기존 send_id=s1'))
    response = await request(caller, '/internal/offer-mails/send', payload(True))
    assert response.status_code == 422
    assert response.json()['error'] == {'code': code, 'message': '기존 send_id=s1', 'retryable': False}
    assert len(caller.calls) == 1


async def test_connection_failure_is_sanitized():
    caller = MailCaller(error=ConnectionError('password=secret'))
    response = await request(caller, '/internal/offer-mails/preview', payload())
    assert response.status_code == 503
    assert response.json()['error']['code'] == 'UPSTREAM_UNAVAILABLE'
    assert 'secret' not in response.text


async def test_send_timeout_is_unknown_and_never_retried():
    caller = MailCaller(delay=1)
    response = await request(caller, '/internal/offer-mails/send', payload(True), timeout=0.01)
    assert response.status_code == 504
    assert response.json()['error']['code'] == 'UPSTREAM_TIMEOUT'
    assert response.json()['error']['retryable'] is True
    assert '결과 불명확' in response.json()['error']['message']
    assert len(caller.calls) == 1


@pytest.mark.parametrize('grouped', [False, True])
async def test_mcp_wrapped_timeout_is_504_without_retry(grouped):
    timeout = httpx.ReadTimeout('private upstream address')
    error = RuntimeError('Client failed to connect')
    error.__cause__ = ExceptionGroup('transport errors', [timeout]) if grouped else timeout
    caller = MailCaller(error=error)
    response = await request(caller, '/internal/offer-mails/send', payload(True))
    assert response.status_code == 504
    assert response.json()['error']['code'] == 'UPSTREAM_TIMEOUT'
    assert '결과 불명확' in response.json()['error']['message']
    assert 'private' not in response.text
    assert len(caller.calls) == 1


@pytest.mark.parametrize('path,body', [('/preview', payload()), ('/send', payload(True)),
                                      ('?document_id=d1', None)])
async def test_authentication_required(path, body):
    caller = MailCaller()
    response = await request(caller, '/internal/offer-mails' + path, body, headers={})
    assert response.status_code == 401
    assert caller.calls == []


@pytest.mark.parametrize('field', ['contact_id', 'client_ref'])
async def test_send_requires_recovery_identifiers(field):
    caller = MailCaller()
    body = payload(True)
    body.pop(field)
    assert (await request(caller, '/internal/offer-mails/send', body)).status_code == 400
    assert caller.calls == []


def test_mail_url_and_send_timeout_settings(monkeypatch):
    monkeypatch.setenv('MAIL_MCP_URL', 'http://mail.invalid/mcp')
    monkeypatch.setenv('MAIL_SEND_TIMEOUT_SECONDS', '12')
    assert McpToolCaller().urls['mail-mcp'] == 'http://mail.invalid/mcp'
    assert ApiSettings.from_env().mail_send_timeout_seconds == 12
    assert ApiSettings().mail_send_timeout_seconds == 30
    for value in [0, -1, 35, 60, float('nan'), float('inf')]:
        with pytest.raises(ValueError):
            ApiSettings(mail_send_timeout_seconds=value)
