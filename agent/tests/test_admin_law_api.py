"""Exercise administrator reads without models or a network MCP service."""
import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from negopilot_agent.api.app import create_app
from negopilot_agent.api.settings import ApiSettings


@pytest.fixture(scope="module")
def law_server():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "mcp-servers/law-mcp"))
    path = Path(__file__).resolve().parents[2] / "mcp-servers/law-mcp/server.py"
    spec = importlib.util.spec_from_file_location("admin_law_test_server", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def client(law_server):
    async def caller(server, tool, args):
        assert server == "law-mcp"
        assert tool.startswith("admin_law_")
        return getattr(law_server, tool)(**args)
    return TestClient(create_app(ApiSettings(mode="stub", internal_api_key="test-only-key"), admin_law_caller=caller))


HEADERS = {"X-Internal-Api-Key": "test-only-key"}
ROOT = "/internal/admin/laws"


def test_reads_loaded_catalog_exact_versions_and_articles(client, law_server):
    catalog = client.get(ROOT, headers=HEADERS).json()["data"]
    assert catalog["law_count"] == len(law_server.LAWS)
    assert catalog["collected_at"] is None
    assert catalog["dataset_id"] == law_server.DATASET_ID
    key = catalog["items"][0]["law_key"]
    versions = client.get(f"{ROOT}/{key}/versions", headers=HEADERS).json()["data"]
    assert len(versions["items"]) == len(law_server.LAWS[key]["versions"])
    for version in versions["items"]:
        base = f"{ROOT}/{key}/versions/{version['version_id']}/articles"
        page = client.get(base + "?offset=0&limit=2", headers=HEADERS).json()["data"]
        assert page["total"] == version["article_count"]
        assert len(page["items"]) <= 2
        detail = client.get(base + "/" + page["items"][0]["no"], headers=HEADERS)
        assert detail.status_code == 200, detail.text
        body = detail.json()["data"]
        assert body["effective_date"] == version["effective_date"]
        assert body["source_url"] == version["source_url"]
        assert "raw_file" not in body
        assert body["article"]["paragraphs"]


def test_requires_internal_key(client):
    assert client.get(ROOT).status_code == 401
    assert client.get(ROOT, headers={"X-Internal-Api-Key": "wrong"}).status_code == 401


@pytest.mark.parametrize("suffix", ["?unknown=1", "?limit=1", "/BAD/versions", "/x/versions/not-a-version/articles"])
def test_invalid_parameters(client, suffix):
    assert client.get(ROOT+suffix, headers=HEADERS).status_code == 400


def test_unknown_is_404_not_oldest_version_fallback(client):
    assert client.get(ROOT+"/unknown/versions", headers=HEADERS).status_code == 404
    path = ROOT+"/subcontract_act/versions/1900-01-01_" + "a"*40 + "/articles"
    assert client.get(path, headers=HEADERS).status_code == 404


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "offset=-1", "limit=2&limit=3", "limit=abc"])
def test_page_limits(client, law_server, query):
    version = law_server.admin_version_id(law_server.LAWS["subcontract_act"]["versions"][0])
    assert client.get(f"{ROOT}/subcontract_act/versions/{version}/articles?{query}", headers=HEADERS).status_code == 400


@pytest.mark.parametrize("mode,status,code", [("bad",502,"UPSTREAM_INVALID_RESPONSE"),
                                             ("timeout",504,"UPSTREAM_TIMEOUT"),
                                             ("down",503,"UPSTREAM_UNAVAILABLE")])
def test_sanitizes_failures(mode,status,code):
    async def caller(*args):
        if mode == "bad": return {"password": "do-not-expose"}
        if mode == "timeout": raise TimeoutError("do-not-expose")
        raise RuntimeError("do-not-expose")
    client = TestClient(create_app(ApiSettings(mode="live",internal_api_key="test-only-key"),admin_law_caller=caller))
    result = client.get(ROOT,headers=HEADERS)
    assert result.status_code == status
    assert result.json()["error"]["code"] == code
    assert "do-not-expose" not in result.text


def test_write_not_supported(client):
    assert client.post(ROOT,headers=HEADERS,json={}).status_code == 405
