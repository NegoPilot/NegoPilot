"""질문 하나를 Agent 전체(분해 → Routing → 생성)로 돌려 단계별 결과와 답변을 출력한다.

먼저 MCP 서버를 띄운다 (README 빠른 시작):
    cd mcp-servers/business-mcp && uv run python server.py --http 9102
    cd mcp-servers/law-mcp      && uv run python server.py --http 9103

    uv run python scripts/ask.py "RFQ-2026-001 협력사 추천해줘"
    uv run python scripts/ask.py --backend ollama "하도급법 제4조 보여줘"   # 분해·생성 모두 로컬 모델 (비용 없음)
    uv run python scripts/ask.py --json "..."                               # 상태 전체를 JSON으로

Claude 백엔드는 ANTHROPIC_API_KEY가 필요하고 비용이 든다. agent/.env가 있으면 읽는다.
"""

import argparse
import asyncio
import json
import os
from pathlib import Path


def load_env() -> None:
    env = Path(__file__).resolve().parents[1] / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and not line.lstrip().startswith("#") and value.strip():
                os.environ.setdefault(key.strip(), value.strip().strip("\"").strip("'"))


async def main(query: str, as_json: bool) -> None:
    from negopilot_agent.graph import build_graph   # 환경 변수를 읽은 뒤 import (설정 기본값이 env를 본다)

    state = await build_graph().ainvoke({"query": query})
    if as_json:
        print(json.dumps({k: v.model_dump(mode="json") if hasattr(v, "model_dump") else v for k, v in state.items()},
                         ensure_ascii=False, indent=2))
        return

    d = state["decomposition"]
    print(f"[분해] status={d.status}")
    for sq in d.sub_queries:
        print(f"  {sq.id} {sq.intent} {sq.entities.model_dump(exclude_none=True)} depends_on={sq.depends_on}")
    if routing := state.get("routing"):
        print(f"[Routing] status={routing.status} halted={routing.halted}")
        for o in routing.outcomes:
            tools = ", ".join(c.tool for c in o.calls)
            print(f"  {o.id} {o.status} {tools} {o.message or ''}")
    answer = state["answer"]
    print(f"[답변] kind={answer.kind} grounded={answer.grounded}\n")
    print(answer.text)
    for c in answer.citations:
        print(f"  [{c.n}] {c.label}")
    for option in answer.options:
        print(f"  - {option}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("query")
    parser.add_argument("--backend", choices=["anthropic", "ollama"], help="분해·생성 백엔드를 한 번에 지정")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    load_env()
    if args.backend:
        os.environ["NEGOPILOT_DECOMPOSER_BACKEND"] = os.environ["NEGOPILOT_GENERATOR_BACKEND"] = args.backend
    asyncio.run(main(args.query, args.json))
