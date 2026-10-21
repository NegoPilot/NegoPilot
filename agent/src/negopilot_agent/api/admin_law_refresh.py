"""Authenticated HTTP control client. Refresh is never exposed as an Agent tool."""
import asyncio
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Literal
from uuid import UUID

from fastapi import Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator


class RefreshJob(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    job_id: str
    status: Literal['RUNNING', 'SUCCEEDED', 'FAILED']
    started_at: str
    finished_at: str | None
    previous_dataset_id: str
    dataset_id: str | None
    error_code: Literal['REFRESH_FAILED', 'REFRESH_INTERRUPTED'] | None

    @model_validator(mode='after')
    def validate_state(self):
        UUID(self.job_id)
        if self.status == 'RUNNING' and any(x is not None for x in (self.finished_at, self.dataset_id, self.error_code)):
            raise ValueError('Invalid running job')
        if self.status != 'RUNNING' and not self.finished_at:
            raise ValueError('Missing completion time')
        if self.status == 'SUCCEEDED' and (not self.dataset_id or self.error_code):
            raise ValueError('Invalid success')
        if self.status == 'FAILED' and (not self.error_code or self.dataset_id):
            raise ValueError('Invalid failure')
        return self


class LatestJob(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    item: RefreshJob | None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class LawControlClient:
    def __init__(self, timeout):
        self.timeout = timeout
        self.base = os.getenv('LAW_MCP_URL', 'http://localhost:9103/mcp')
        self.key = os.getenv('LAW_ADMIN_API_KEY', '')

    async def __call__(self, method, suffix):
        parsed = urllib.parse.urlsplit(self.base)
        if (not self.key or parsed.scheme not in ('http', 'https') or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in ('', '/', '/mcp', '/mcp/')):
            raise ConnectionError('Law refresh unavailable')
        url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, '/admin/refresh-jobs' + suffix, '', ''))

        def request():
            req = urllib.request.Request(url, method=method, headers={'X-Law-Admin-Key': self.key})
            try:
                with urllib.request.build_opener(NoRedirect).open(req, timeout=self.timeout) as response:
                    return response.status, json.loads(response.read(1_000_000))
            except urllib.error.HTTPError as error:
                # Only the status is trusted. Never relay error bodies or headers.
                return error.code, None
        return await asyncio.to_thread(request)


def add_refresh_routes(app, settings, authenticate, error_response, control=None):
    control = control or LawControlClient(settings.request_timeout_seconds)
    dependencies = [Depends(authenticate)]

    async def run(request, suffix='', job_id=None):
        try:
            if request.query_params or await request.body():
                raise ValueError('Unexpected parameters')
            if job_id is not None and str(UUID(job_id)) != job_id:
                raise ValueError('Invalid job id')
        except ValueError:
            return error_response(400, None, 'VALIDATION_ERROR', 'Invalid law refresh request.')
        try:
            async with asyncio.timeout(settings.request_timeout_seconds):
                status, raw = await control(request.method, suffix)
            errors = {400: ('VALIDATION_ERROR', False), 404: ('LAW_NOT_FOUND', False),
                      409: ('REFRESH_BUSY', True), 503: ('UPSTREAM_UNAVAILABLE', True),
                      504: ('UPSTREAM_TIMEOUT', True)}
            if status in errors:
                code, retryable = errors[status]
                return error_response(status, None, code, 'Law refresh request could not be completed.', retryable)
            expected = 202 if request.method == 'POST' else 200
            if status != expected:
                raise ValueError('Unexpected status')
            model = LatestJob if request.method == 'GET' and not suffix else RefreshJob
            parsed = model.model_validate(raw)
            if job_id and parsed.job_id != job_id:
                raise ValueError('Wrong job')
            return JSONResponse({'schema_version': '1.0', 'data': parsed.model_dump()}, status_code=status)
        except (TimeoutError, urllib.error.URLError) as exc:
            if isinstance(exc, TimeoutError) or isinstance(getattr(exc, 'reason', None), TimeoutError):
                return error_response(504, None, 'UPSTREAM_TIMEOUT', 'Law service timed out.', True)
            return error_response(503, None, 'UPSTREAM_UNAVAILABLE', 'Law service unavailable.', True)
        except (ValueError, ValidationError, TypeError):
            return error_response(502, None, 'UPSTREAM_INVALID_RESPONSE', 'Invalid law service response.')
        except Exception:
            return error_response(503, None, 'UPSTREAM_UNAVAILABLE', 'Law service unavailable.', True)

    @app.post('/internal/v1/admin/laws/refresh-jobs', dependencies=dependencies)
    async def start(request: Request):
        return await run(request)

    @app.get('/internal/v1/admin/laws/refresh-jobs', dependencies=dependencies)
    async def latest(request: Request):
        return await run(request)

    @app.get('/internal/v1/admin/laws/refresh-jobs/{job_id}', dependencies=dependencies)
    async def get(job_id: str, request: Request):
        return await run(request, '/' + job_id, job_id)
