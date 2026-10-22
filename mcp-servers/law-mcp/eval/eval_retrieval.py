"""search_regulation 검색 방식 비교 평가 (BM25 · 벡터 · 하이브리드).

server.py 가 쓰는 항 단위 색인을 그대로 가져와, 같은 질문 세트(eval/retrieval_gold.json)로
검색 방식마다 정답 조문이 몇 위에 나오는지 잰다. 서버 코드는 바꾸지 않는다.

실행 (law-mcp 폴더에서):
    uv run --with numpy python eval/eval_retrieval.py          # BM25만
    uv run --with numpy python eval/eval_retrieval.py --dense bge-m3 openai-small

임베딩은 --embed-python(기본 python3)으로 eval/embed.py 를 실행해 계산하고,
eval/.cache/ 에 저장해 두어 색인 문장이 같으면 다시 계산하지 않는다.
"""

import argparse
import hashlib
import json
import random
import subprocess
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import server  # noqa: E402

CACHE_DIR = HERE / ".cache"
KS = (1, 3, 5, 10)
RRF_K = 60


# ---------- 색인 ----------

def unit_text(u: dict) -> str:
    """임베딩할 문장. BM25 색인처럼 조 번호·제목을 앞에 붙인다."""
    v = u["version"]
    if u["article"] is None:
        return f"{v['law_name']} {u['addendum']}\n{u['text']}"
    a = u["article"]
    return f"{v['law_name']} {a['no']}({a['title']}) {u['paragraph'] or ''}\n{u['text']}"


def unit_article(u: dict) -> str:
    return f"{u['law_key']}:{u['article']['no']}" if u["article"] else f"{u['law_key']}:부칙"


# ---------- 임베딩 ----------

# 임베딩은 torch·transformers(또는 openai)가 있는 파이썬에서 eval/embed.py 로 따로 계산한다.
# law-mcp 가상환경에 무거운 패키지를 넣지 않기 위해서다
EMBED_MODELS = {"bge-m3": "BAAI/bge-m3", "openai-small": "text-embedding-3-small"}


def cached_embed(model: str, kind: str, texts: list[str], python: str) -> np.ndarray:
    digest = hashlib.sha256(("\n\x00".join(texts)).encode()).hexdigest()[:16]
    path = CACHE_DIR / f"{model}_{kind}_{digest}.npy"
    if not path.exists():
        CACHE_DIR.mkdir(exist_ok=True)
        src = path.with_suffix(".json")
        src.write_text(json.dumps(texts, ensure_ascii=False), encoding="utf-8")
        subprocess.run([python, str(HERE / "embed.py"), model, str(src), str(path)], check=True)
        src.unlink()
    return np.load(path)


# ---------- 검색 방식 ----------

def rank_bm25(bm25, query: str, expand: bool) -> list[int]:
    scores = bm25.scores(server.tokenize(server.expand_query(query) if expand else query))
    ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    return [i for i in ranked if scores[i] > 0]          # 서버처럼 점수 0 은 버린다


def rank_dense(doc_vecs: np.ndarray, q_vec: np.ndarray) -> list[int]:
    return list(np.argsort(-(doc_vecs @ q_vec)))


def rank_rrf(*rankings: list[int]) -> list[int]:
    score: dict[int, float] = {}
    for r in rankings:
        for pos, i in enumerate(r[:100]):
            score[i] = score.get(i, 0.0) + 1 / (RRF_K + pos + 1)
    return sorted(score, key=score.get, reverse=True)


# ---------- 지표 ----------

def first_hit(ranking: list[int], arts: list[str], gold: set[str]) -> int | None:
    """정답 조문의 항이 처음 나온 순위(1부터). 상위 10 안에 없으면 None."""
    for pos, i in enumerate(ranking[:10]):
        if arts[i] in gold:
            return pos + 1
    return None


def summarize(ranks: list[int | None]) -> dict:
    n = len(ranks)
    row = {f"Hit@{k}": sum(r is not None and r <= k for r in ranks) / n for k in KS}
    row["MRR@10"] = sum(1 / r for r in ranks if r) / n
    return row


def bootstrap_diff(a: list[int | None], b: list[int | None], k: int = 5, n: int = 10000) -> tuple:
    """Hit@k(b) - Hit@k(a) 의 95% 구간 (질문 단위 paired bootstrap)."""
    ha = [r is not None and r <= k for r in a]
    hb = [r is not None and r <= k for r in b]
    rnd = random.Random(0)
    m = len(ha)
    diffs = sorted(sum(hb[j] - ha[j] for j in idx) / m
                   for idx in ([rnd.randrange(m) for _ in range(m)] for _ in range(n)))
    return diffs[int(n * 0.025)], diffs[int(n * 0.975)]


# ---------- 실행 ----------

def run(args):
    gold = json.loads((HERE / "retrieval_gold.json").read_text(encoding="utf-8"))
    as_of = gold["as_of"]
    keys = tuple(server.LAWS)
    units, bm25 = server.search_units(keys, as_of, False)
    arts = [unit_article(u) for u in units]
    print(f"색인: 항 {len(units)}개, 질문 {len(gold['items'])}개, 기준일 {as_of}")

    embedders = args.dense
    doc_vecs = {e: cached_embed(e, "docs", [unit_text(u) for u in units], args.embed_python)
                for e in embedders}

    # 질문 세트: 사용자 원문 질문 전체, 그리고 분해 질문이 있는 fixture 부분
    # GLOSSARY 는 fixture 질문을 보며 만든 것이라, 새로 쓴 질문만 따로도 본다
    sets = {"원문 질문 (전체)": [(it, it["query"]) for it in gold["items"]],
            "새로 쓴 질문": [(it, it["query"]) for it in gold["items"] if it["source"] == "new"],
            "fixture 원문 질문": [(it, it["query"]) for it in gold["items"] if "decomposed" in it],
            "fixture 분해 질문": [(it, it["decomposed"]) for it in gold["items"] if "decomposed" in it]}

    report = {"as_of": as_of, "units": len(units), "results": {}, "per_query": {}}
    for set_name, pairs in sets.items():
        queries = [q for _, q in pairs]
        expanded = [server.expand_query(q) for q in queries]
        q_vecs = {e: (cached_embed(e, "q", queries, args.embed_python),
                      cached_embed(e, "qexp", expanded, args.embed_python)) for e in embedders}
        for use_filter in (False, True):
            methods: dict[str, list] = {}
            for j, (it, q) in enumerate(pairs):
                g = set(it["gold"])
                allowed = set(it["laws"]) if use_filter and it.get("laws") else None

                def keep(r):
                    return [i for i in r if allowed is None or units[i]["law_key"] in allowed]

                bm = keep(rank_bm25(bm25, q, True))
                runs = {"BM25+확장 (현재)": bm, "BM25": keep(rank_bm25(bm25, q, False))}
                for e in embedders:
                    dv, (qv, qxv) = doc_vecs[e], q_vecs[e]
                    dn = keep(rank_dense(dv, qv[j]))
                    runs[f"벡터 {e}"] = dn
                    runs[f"벡터 {e}+확장"] = keep(rank_dense(dv, qxv[j]))
                    runs[f"하이브리드 RRF(BM25+확장, {e})"] = rank_rrf(bm, dn)
                for name, r in runs.items():
                    methods.setdefault(name, []).append(first_hit(r, arts, g))
            label = f"{set_name} / {'법령 필터' if use_filter else '전체 법령'}"
            report["results"][label] = {m: summarize(r) for m, r in methods.items()}
            report["per_query"][label] = {m: dict(zip([it["id"] for it, _ in pairs], r))
                                          for m, r in methods.items()}
            base = methods["BM25+확장 (현재)"]
            report["results"][label]["_bootstrap_Hit@5_vs_BM25+확장"] = {
                m: bootstrap_diff(base, r) for m, r in methods.items() if m != "BM25+확장 (현재)"}
            print_table(label, len(pairs), report["results"][label])

    out = HERE / "results" / "retrieval_eval.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n질문별 순위: {out.relative_to(HERE.parent)}")


def print_table(label: str, n: int, rows: dict):
    print(f"\n## {label} (n={n})")
    cols = [f"Hit@{k}" for k in KS] + ["MRR@10"]
    print(f"{'방식':<44}" + "".join(f"{c:>9}" for c in cols))
    for name, row in rows.items():
        if name.startswith("_"):
            continue
        print(f"{name:<44}" + "".join(f"{row[c]:>9.3f}" for c in cols))
    for name, (lo, hi) in rows["_bootstrap_Hit@5_vs_BM25+확장"].items():
        print(f"  Hit@5 차이 95% 구간 [{name} - 현재]: {lo:+.3f} ~ {hi:+.3f}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dense", nargs="*", default=[], choices=list(EMBED_MODELS),
                   help="함께 평가할 임베딩 모델")
    p.add_argument("--embed-python", default="python3",
                   help="embed.py 를 실행할 파이썬 (torch·transformers 또는 openai 필요)")
    run(p.parse_args())
