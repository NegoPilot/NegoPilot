"""모든 테스트 공통 설정."""

import pytest


@pytest.fixture(autouse=True)
def no_langsmith_tracing(monkeypatch):
    """개발자의 .env나 셸에 LangSmith 설정이 있어도 테스트는 외부로 추적을 보내지 않는다 (#154)."""
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.delenv("LANGCHAIN_API_KEY", raising=False)
