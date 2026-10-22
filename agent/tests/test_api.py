"""App-level HTTP behavior shared by every internal route. No model or MCP server is contacted."""

import socket

import httpx2 as httpx
import pytest

from negopilot_agent.api.app import create_app
from negopilot_agent.api.settings import ApiSettings

API_KEY = "test-only-internal-key"
HEADERS = {"X-Internal-Api-Key": API_KEY}
REMOVED_V1_ROUTES = [
    ("POST", "/internal/v1/query-executions"),
    ("GET", "/internal/v1/business/catalog"),
    ("GET", "/internal/v1/suppliers"),
    ("GET", "/internal/v1/suppliers/SUP-0034"),
    ("POST", "/internal/v1/chat/responses"),
]


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("HTTP contract tests must not contact external services")

    monkeypatch.setattr(socket.socket, "connect", fail)
    monkeypatch.setattr(socket.socket, "connect_ex", fail)


def settings(**overrides):
    return ApiSettings(**({"mode": "stub", "internal_api_key": API_KEY,
                           "request_timeout_seconds": 30.0} | overrides))


async def send(app, method, path, **kwargs):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


@pytest.mark.parametrize("mode", ["live", "stub"])
async def test_health_is_available_without_key_or_upstream(mode):
    response = await send(create_app(settings=settings(mode=mode, internal_api_key="")), "GET", "/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_malformed_json_does_not_echo_request_or_stack(caplog):
    response = await send(create_app(settings=settings()), "POST", "/internal/v2/query-executions",
                          content='{"query": "private-body-marker"',
                          headers=HEADERS | {"Content-Type": "application/json"})
    assert response.status_code == 400
    assert response.json() == {"request_id": None, "error": {
        "code": "VALIDATION_ERROR", "message": "Invalid request.", "retryable": False}}
    assert "private-body-marker" not in response.text + caplog.text


@pytest.mark.parametrize("mode", ["live", "stub"])
@pytest.mark.parametrize(("method", "path"), REMOVED_V1_ROUTES)
async def test_removed_v1_business_routes_are_not_registered(mode, method, path):
    response = await send(create_app(settings=settings(mode=mode)), method, path, headers=HEADERS)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_only_admin_law_routes_remain_under_internal_v1():
    # The OpenAPI path list also covers routes registered through included routers.
    paths = set(create_app(settings=settings()).openapi()["paths"])
    v1 = {path for path in paths if path.startswith("/internal/v1/")}
    assert v1 and all(path.startswith("/internal/v1/admin/laws") for path in v1)
    assert {"/internal/v2/query-executions", "/internal/v2/suppliers"} <= paths
