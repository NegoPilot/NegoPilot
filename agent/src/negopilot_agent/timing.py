"""단계별 시간 측정. 그래프 한 번 실행 동안 노드·LLM 호출·MCP 도구 호출 시간을 모은다.

    from negopilot_agent import timing

    with timing.collect() as t:
        state = await build_graph().ainvoke({"query": "..."})
    print(t.report())

collect() 밖에서는 아무것도 기록하지 않으므로, 측정이 필요 없는 곳(테스트 등)에는 영향이 없다.
asyncio.gather로 병렬 실행한 호출도 같은 기록에 모인다 (contextvar를 복사해도 기록 객체는 하나다).
"""

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Span:
    kind: str          # node | llm | tool
    name: str          # 노드 이름, 모델 이름, "서버.도구"
    stage: str         # 이 호출이 일어난 노드 (node면 자기 이름)
    start: float       # collect() 시작 기준 초
    seconds: float
    meta: dict[str, Any] = field(default_factory=dict)   # llm: 토큰 수


@dataclass
class Timings:
    origin: float = field(default_factory=time.perf_counter)
    spans: list[Span] = field(default_factory=list)
    total: float = 0.0

    def summary(self) -> dict[str, Any]:
        """노드별 시간, LLM·도구 호출 수와 합계. 로그나 평가 결과에 남기는 형태."""
        out: dict[str, Any] = {"total": round(self.total, 3)}
        for s in self.spans:
            if s.kind == "node":
                out[s.name] = round(out.get(s.name, 0.0) + s.seconds, 3)
        for kind in ("llm", "tool"):
            spans = [s for s in self.spans if s.kind == kind]
            out[f"{kind}_calls"] = len(spans)
            out[f"{kind}_seconds"] = round(sum(s.seconds for s in spans), 3)
        for key in ("input_tokens", "output_tokens", "cached_tokens"):
            out[key] = sum(s.meta.get(key, 0) for s in self.spans if s.kind == "llm")
        return out

    def report(self) -> str:
        """사람이 읽는 표. 시작 시각 순서로 보여 줘서 순차·병렬 구간이 보이게 한다."""
        lines = [f"[시간] 전체 {self.total:.2f}초"]
        for s in sorted(self.spans, key=lambda s: s.start):
            indent = "  " if s.kind == "node" else "      "
            meta = " ".join(f"{k}={v}" for k, v in s.meta.items())
            lines.append(f"{indent}{s.start:6.2f}s +{s.seconds:5.2f}s  {s.kind:<4} {s.name} {meta}".rstrip())
        return "\n".join(lines)


_current: ContextVar[Timings | None] = ContextVar("negopilot_timings", default=None)
_stage: ContextVar[str] = ContextVar("negopilot_stage", default="")


@contextmanager
def collect() -> Iterator[Timings]:
    timings = Timings()
    token = _current.set(timings)
    try:
        yield timings
    finally:
        timings.total = time.perf_counter() - timings.origin
        _current.reset(token)


@contextmanager
def span(kind: str, name: str, meta: dict[str, Any] | None = None) -> Iterator[dict[str, Any]]:
    """with 블록 시간을 기록한다. 돌려준 dict에 값을 넣으면 meta로 남는다 (예: 토큰 수)."""
    timings = _current.get()
    meta = {} if meta is None else meta
    if timings is None:
        yield meta
        return
    stage_token = _stage.set(name) if kind == "node" else None
    start = time.perf_counter()
    try:
        yield meta
    finally:
        seconds = time.perf_counter() - start
        if stage_token is not None:
            _stage.reset(stage_token)
        timings.spans.append(Span(kind=kind, name=name, stage=name if kind == "node" else _stage.get(),
                                  start=start - timings.origin, seconds=seconds, meta=meta))


def timed_node(name: str, node):
    """LangGraph 노드 함수를 감싸 노드 시간을 기록한다."""
    async def run(state):
        with span("node", name):
            return await node(state)

    run.__name__ = getattr(node, "__name__", name)
    return run
