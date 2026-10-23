"""실제 MCP 서버를 Streamable HTTP로 부르는 ToolCaller.

서버 주소는 환경변수로 바꿀 수 있다 (기본: README 빠른 시작의 포트).
    BUSINESS_MCP_URL=http://localhost:9102/mcp
    LAW_MCP_URL=http://localhost:9103/mcp

연결 재사용: Router는 route() 한 번을 session() 블록 안에서 실행한다. 블록 안에서는 서버마다 처음 부를 때
연결(MCP initialize)을 한 번 열고, 같은 서버의 다음 호출은 그 연결을 같이 쓴다. 블록이 끝나면 닫는다.
연결 하나를 여는 데 호출 한 번보다 훨씬 오래 걸려서(로컬 측정 약 88ms vs 9ms), law_check처럼
조문 조회가 여러 번 붙는 질문에서 차이가 난다. 요청이 끝나면 닫으므로 서버를 다시 띄워도 다음 요청은 새로 연결한다.
session() 밖에서 부르면 전처럼 호출마다 연결한다.
"""

import logging
import os
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_URLS = {
    "business-mcp": ("BUSINESS_MCP_URL", "http://localhost:9102/mcp"),
    "law-mcp": ("LAW_MCP_URL", "http://localhost:9103/mcp"),
    "mail-mcp": ("MAIL_MCP_URL", "http://localhost:9104/mcp"),
}


class McpToolCaller:
    def __init__(self, urls: dict[str, str] | None = None):
        self.urls = urls or {name: os.getenv(env, url) for name, (env, url) in DEFAULT_URLS.items()}
        self._stack: AsyncExitStack | None = None
        self._clients: dict[str, Any] = {}     # session() 안에서 서버별로 열어 둔 fastmcp Client

    @asynccontextmanager
    async def session(self) -> AsyncIterator[None]:
        """이 블록 안의 호출은 서버별 연결 하나를 같이 쓴다. 이미 블록 안이면 바깥 블록의 연결을 쓴다."""
        if self._stack is not None:
            yield
            return
        stack = AsyncExitStack()
        self._stack = stack
        try:
            yield
        finally:
            self._stack, self._clients = None, {}
            try:
                await stack.aclose()
            except Exception as e:  # noqa: BLE001  닫다가 난 오류가 이미 받은 결과를 버리게 하지 않는다
                log.warning("MCP 연결 닫기 실패: %s", type(e).__name__)

    async def __call__(self, server: str, tool: str, args: dict[str, Any]) -> Any:
        from fastmcp import Client   # 테스트(가짜 caller)에서는 fastmcp 없이도 돌도록 늦게 import

        if server not in self.urls:
            raise KeyError(f"MCP 서버 '{server}'의 주소가 없다")
        if self._stack is None:
            async with Client(self.urls[server]) as client:
                return self._data(await client.call_tool(tool, args))   # ToolError면 예외 → Router가 error로 기록

        client = self._clients.get(server)
        if client is None:
            # 사전에 먼저 넣어 같은 묶음의 병렬 호출이 연결을 하나만 만들게 한다.
            # Client는 async with를 겹쳐 쓸 수 있어서, 연결 중에 들어온 호출은 그 연결이 열리기를 기다린다
            client = self._clients[server] = Client(self.urls[server])
            try:
                await self._stack.enter_async_context(client)
            except BaseException:
                self._clients.pop(server, None)   # 연결 실패면 다음 호출이 다시 연결해 본다
                raise
        async with client:
            return self._data(await client.call_tool(tool, args))

    @staticmethod
    def _data(result) -> Any:
        return result.data if result.data is not None else result.structured_content
