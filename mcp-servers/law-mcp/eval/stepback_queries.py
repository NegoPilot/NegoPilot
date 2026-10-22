"""실제 분해 결과의 law_check 하위 질문마다 step-back 검색어를 만들어 저장한다 (검색어 확장 실험, #191).

step-back 검색어: 구체적인 상황을 법령 조문이 쓰는 일반적인 행위·의무 유형으로 한 단계 올려 쓴 문장.
예) "협력사들끼리 모임을 만들겠다는데 우리가 도와줘야 해?" → 위탁기업이 수탁기업의 협의회 구성·운영을 지원하는 것
eval_retrieval.py --decomposed ... --stepback ... 로 넘기면 질문·원문·step-back 조합별 검색 점수를 비교한다.

agent 가상환경에서 실행한다 (gpt-4.1-mini, 하위 질문 수백 개 기준 몇 센트):
    cd agent && uv run python ../mcp-servers/law-mcp/eval/stepback_queries.py

같은 (question, original_text) 는 한 번만 만든다. 결과는 eval/results/stepback_<모델>.json.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
AGENT = ROOT / "agent"
sys.path[:0] = [str(AGENT / "scripts")]

import openai  # noqa: E402
from eval_decomposition import load_dotenv  # noqa: E402

# 평가 질문의 정답을 보고 쓴 프롬프트가 아니다. 예시도 평가 세트에 없는 질문만 쓴다
SYSTEM = """너는 한국 법령 검색어를 만드는 도우미다. 사용자 질문과, 그것을 법률 용어로 바꾼 문장을 받는다.
이 질문에 답하는 조문을 찾기 위해, 구체적인 상황을 법령 조문이 쓰는 일반적인 행위·의무·절차 유형으로 한 단계 올려
검색어 한 문장을 써라.

규칙:
- 조문 본문에 나올 법한 말로 쓴다. 상황에 맞는 법의 용어를 쓴다:
  하도급법(원사업자, 수급사업자, 하도급대금, 위탁), 상생협력법(위탁기업, 수탁기업, 납품대금, 상생결제),
  중소기업기본법(중소기업자, 소기업, 규모 기준). 어느 법인지 모르면 해당할 만한 용어를 함께 쓴다.
- 구체적 사물·금액·일수는 그 상위 범주로 바꾼다 (예: 40일 → 지급 기한).
- 조문 번호나 법 이름을 지어내지 않는다. 검색어만 한 문장으로 출력한다.

예)
질문: 우리 회사 하도급 결제 조건을 외부에 공개해야 해?
검색어: 원사업자의 하도급대금 결제조건 등에 관한 공시 의무
질문: 협력사들끼리 모임을 만들겠다는데 우리가 도와줘야 해?
검색어: 위탁기업이 수탁기업의 협의회 구성·운영을 지원하는 것"""


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--decomposed", type=Path, default=HERE / "results" / "decomposed_gpt-4.1-mini.json")
    p.add_argument("--model", default="gpt-4.1-mini")
    p.add_argument("--concurrency", type=int, default=10)
    args = p.parse_args()
    load_dotenv(AGENT / ".env")
    client = openai.AsyncOpenAI()

    dec = json.loads(args.decomposed.read_text(encoding="utf-8"))
    runs = dec["runs"] if "runs" in dec else [dec["items"]]
    pairs = sorted({(s["question"], s["original_text"]) for run in runs for r in run.values()
                    for s in r.get("sub_queries", []) if s["intent"] == "law_check"})
    print(f"모델 {args.model} · law_check 하위 질문 {len(pairs)}개")
    sem = asyncio.Semaphore(args.concurrency)

    async def one(question: str, original: str) -> tuple[str, str]:
        async with sem:
            r = await client.chat.completions.create(
                model=args.model, temperature=0, max_completion_tokens=200,
                messages=[{"role": "system", "content": SYSTEM},
                          {"role": "user", "content": f"질문: {original}\n법률 용어 문장: {question}\n검색어:"}])
        text = (r.choices[0].message.content or "").strip().removeprefix("검색어:").strip()
        print(f"  {original[:30]:<32} → {text}", flush=True)
        return f"{question}\n{original}", text

    out = dict(await asyncio.gather(*(one(q, o) for q, o in pairs)))
    path = HERE / "results" / f"stepback_{args.model}.json"
    path.write_text(json.dumps({"model": args.model, "queries": out}, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    print(f"저장: {path.relative_to(ROOT)}")


if __name__ == "__main__":
    asyncio.run(main())
