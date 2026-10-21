"""Generation 검증 세트(eval_data/generation_validation/)의 분해 결과를 실제 MCP 서버로 Routing해서 저장한다.

개발 세트의 입력은 shared/fixtures/routing(scripts/route_fixtures.py --save)이고,
검증 세트는 파일 하나에 질문·분해 결과(decomposition)·Routing 결과(routing)를 함께 둔다.
분해 결과는 사람이 정답으로 적은 것이라 Generation만의 실력을 잰다.

먼저 서버를 띄운다 (README 빠른 시작):
    cd mcp-servers/business-mcp && uv run python server.py --http 9102
    cd mcp-servers/law-mcp      && uv run python server.py --http 9103

    uv run python scripts/route_generation_set.py            # 전부 Routing해서 routing 필드에 저장
    uv run python scripts/route_generation_set.py gv03 gv07  # 이름이 이것으로 시작하는 것만
"""

import asyncio
import json
import sys
from pathlib import Path

from negopilot_shared import DecompositionResult

from negopilot_agent.routing import McpToolCaller, Router

SET_DIR = Path(__file__).resolve().parents[1] / "eval_data" / "generation_validation"


async def main(prefixes: list[str]) -> None:
    router = Router(McpToolCaller())
    for path in sorted(SET_DIR.glob("*.json")):
        if prefixes and not any(path.stem.startswith(p) for p in prefixes):
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        result = await router.route(DecompositionResult.model_validate(data["decomposition"]))
        data["routing"] = result.model_dump(mode="json")
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{path.stem}  status={result.status} " + " ".join(
            f"{o.id}:{o.status}({len(o.calls)} calls)" for o in result.outcomes))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
