"""실제 MCP 서버를 Streamable HTTP로 부르는 ToolCaller.

서버 주소는 환경변수로 바꿀 수 있다 (기본: README 빠른 시작의 포트).
    BUSINESS_MCP_URL=http://localhost:9102/mcp
    LAW_MCP_URL=http://localhost:9103/mcp
"""

import os
from typing import Any

DEFAULT_URLS = {
    "business-mcp": ("BUSINESS_MCP_URL", "http://localhost:9102/mcp"),
    "law-mcp": ("LAW_MCP_URL", "http://localhost:9103/mcp"),
}


class McpToolCaller:
    def __init__(self, urls: dict[str, str] | None = None):
        self.urls = urls or {name: os.getenv(env, url) for name, (env, url) in DEFAULT_URLS.items()}

    async def __call__(self, server: str, tool: str, args: dict[str, Any]) -> Any:
        from fastmcp import Client   # 테스트(가짜 caller)에서는 fastmcp 없이도 돌도록 늦게 import

        if server not in self.urls:
            raise KeyError(f"MCP 서버 '{server}'의 주소가 없다")
        async with Client(self.urls[server]) as client:
            result = await client.call_tool(tool, args)   # ToolError면 예외 → Router가 error로 기록
        return result.data if result.data is not None else result.structured_content
