"""shared/fixtures의 분해 결과를 실제 MCP 서버로 Routing해서 결과를 출력한다.

먼저 서버를 띄운다 (README 빠른 시작):
    cd mcp-servers/business-mcp && uv run python server.py --http 9102
    cd mcp-servers/law-mcp      && uv run python server.py --http 9103

    uv run python scripts/route_fixtures.py                 # fixtures 전부
    uv run python scripts/route_fixtures.py 03 08           # 이름이 이 숫자로 시작하는 것만
    uv run python scripts/route_fixtures.py --save          # 결과를 shared/fixtures/routing/에 저장 (Generation 입력)
"""

import asyncio
import json
import sys
from pathlib import Path

from negopilot_shared import DecompositionResult

from negopilot_agent.routing import McpToolCaller, Router

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "shared" / "fixtures" / "decomposition"
ROUTING_DIR = FIXTURE_DIR.parent / "routing"


def short(value, limit: int = 160) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + "…"


async def main(prefixes: list[str], save: bool) -> None:
    router = Router(McpToolCaller())
    for path in sorted(FIXTURE_DIR.glob("*.json")):
        if prefixes and not any(path.stem.startswith(p) for p in prefixes):
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        result = await router.route(DecompositionResult.model_validate(data["expected"]))
        if save:
            ROUTING_DIR.mkdir(exist_ok=True)
            out = {"query": data["query"], "decomposition": f"decomposition/{path.name}",
                   "routing": result.model_dump(mode="json")}
            (ROUTING_DIR / path.name).write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n",
                                                 encoding="utf-8")
        print(f"\n## {path.stem}  {data['query']}")
        print(f"status={result.status} halted={result.halted} {result.halt_reason or ''}")
        for o in result.outcomes:
            print(f"  {o.id} {o.intent}: {o.status} {o.message or ''}")
            for c in o.calls:
                print(f"    {c.server}.{c.tool}({short(c.args, 80)}) → {c.error or short(c.result)}")


if __name__ == "__main__":
    args = sys.argv[1:]
    asyncio.run(main([a for a in args if a != "--save"], "--save" in args))
