"""백엔드의 메일 요청을 모델 호출 없이 mail-mcp로 전달한다."""

import asyncio
import re
from typing import Any
from uuid import UUID, uuid4

import httpx2 as httpx
from fastapi import Depends, Query
from pydantic import BaseModel, ConfigDict

from negopilot_agent.routing.mcp_client import McpToolCaller

MAIL_ERRORS = {
    'INVALID_DOCUMENT', 'DOCUMENT_NOT_FINAL', 'OFFER_VIOLATION', 'HASH_MISMATCH',
    'SENDER_INCOMPLETE', 'INVALID_ATTACHMENT', 'INVALID_MESSAGE', 'RECIPIENT_MISSING',
    'CONFIRMATION_REQUIRED', 'CONFIRMATION_INVALID', 'CONFIRMATION_EXPIRED',
    'CONFIRMATION_MISMATCH', 'ALREADY_SENT', 'REQUESTER_REQUIRED', 'MAIL_SEND_FAILED',
    'DB_UNAVAILABLE', 'MAIL_NOT_CONFIGURED', 'INVALID_CLIENT_REF', 'CLIENT_REF_USED',
}


class MailPreviewBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: UUID
    # 문서의 기본값을 채우면 미리보기와 발송의 해시가 달라진다.
    document: dict[str, Any]
    attachments: list[dict[str, Any]]
    contact_id: int | None = None
    message: str | None = None


class MailSendBody(MailPreviewBody):
    confirmation_token: str
    requested_by: str
    contact_id: int
    client_ref: str


def upstream_exceptions(exc: BaseException):
    """MCP가 감싼 원인과 예외 그룹도 검사한다."""
    pending, seen = [exc], set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)
        cause = current.__cause__ or current.__context__
        if cause is not None:
            pending.append(cause)


def add_mail_routes(app, settings, authenticate, error_response, caller=None):
    async def forward(tool, args, request_id, sending=False):
        timeout = settings.mail_send_timeout_seconds if sending else settings.request_timeout_seconds
        try:
            async with asyncio.timeout(timeout):
                result = await (caller or McpToolCaller())('mail-mcp', tool, args)
            return {'request_id': str(request_id), 'result': result}
        except Exception as exc:
            errors = list(upstream_exceptions(exc))
            if any(isinstance(error, (TimeoutError, httpx.TimeoutException)) for error in errors):
                message = ('메일 발송 시간이 초과되어 결과 불명확 상태입니다. 발송 기록을 조회하세요.'
                           if sending else '메일 서버 응답 시간이 초과되었습니다.')
                return error_response(504, request_id, 'UPSTREAM_TIMEOUT', message, True)
            for error in errors:
                match = re.search(r'\[([A-Z_]+)\]\s*(.*)', str(error), re.DOTALL)
                if match and match[1] in MAIL_ERRORS:
                    return error_response(422, request_id, match[1], match[2])
            return error_response(503, request_id, 'UPSTREAM_UNAVAILABLE',
                                  '메일 서버에 연결할 수 없습니다.', True)

    @app.post('/internal/offer-mails/preview', dependencies=[Depends(authenticate)])
    async def preview(body: MailPreviewBody):
        return await forward('preview_offer_mail', body.model_dump(mode='json', exclude={'request_id'},
                                                                  exclude_unset=True), body.request_id)

    @app.post('/internal/offer-mails/send', dependencies=[Depends(authenticate)])
    async def send(body: MailSendBody):
        return await forward('send_offer_mail', body.model_dump(mode='json', exclude={'request_id'},
                                                               exclude_unset=True), body.request_id, sending=True)

    @app.get('/internal/offer-mails', dependencies=[Depends(authenticate)])
    async def list_sends(document_id: str = Query(min_length=1), client_ref: str | None = None):
        args = {'document_id': document_id}
        if client_ref is not None:
            args['client_ref'] = client_ref
        return await forward('list_offer_mail_sends', args, uuid4())
