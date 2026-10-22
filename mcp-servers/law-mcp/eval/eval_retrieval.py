"""search_regulation 검색 방식 비교 평가 (BM25 · 벡터 · 하이브리드).

server.py 가 쓰는 항 단위 색인을 그대로 가져와, 같은 질문 세트(eval/retrieval_gold.json)로
검색 방식마다 정답 조문이 몇 위에 나오는지 잰다. 서버 코드는 바꾸지 않는다.

실행 (law-mcp 폴더에서):
    uv run --with numpy python eval/eval_retrieval.py          # BM25만
    uv run --with numpy python eval/eval_retrieval.py --dense bge-m3 openai-small
    uv run --with numpy python eval/eval_retrieval.py --decomposed eval/results/decomposed_gpt-4.1-mini.json
    uv run --with numpy python eval/eval_retrieval.py --retrievers bm25,hybrid-bge-m3 --min-hit-at-5 0.8

--decomposed 는 eval/decompose_gold.py 가 저장한 실제 분해 결과다. 라우터처럼 law_check 하위 질문을
question 으로, law 엔티티가 있으면 그 법령만 검색해 "원문 → 사람 분해 → 실제 분해" 를 비교하고
eval/results/decomposition_eval.json 에 저장한다.

--retrievers(환경 변수 RETRIEVERS)로 평가할 방식만 고를 수 있고, --min-hit-at-5(MIN_HIT_AT_5)를 주면
고른 방식 중 하나라도 "원문 질문 (전체) / 전체 법령" Hit@5 가 기준보다 낮을 때 종료 코드 1로 끝난다.
Docker 로 돌리려면 저장소 루트의 compose.retrieval-eval.yaml 을 쓴다.

임베딩은 --embed-python(기본 python3)으로 eval/embed.py 를 실행해 계산하고,
eval/.cache/ 에 저장해 두어 색인 문장이 같으면 다시 계산하지 않는다.
"""

import argparse
import hashlib
import json
import os
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

BASELINE = "bm25"
GATE_SET = "원문 질문 (전체) / 전체 법령"     # MIN_HIT_AT_5 를 견주는 표


def retriever_labels() -> dict[str, str]:
    """--retrievers 에 쓰는 이름 → 표에 찍히는 방식 이름."""
    labels = {"bm25": "BM25+확장 (현재)", "bm25-noexp": "BM25"}
    for e in EMBED_MODELS:
        labels[e] = f"벡터 {e}"
        labels[f"{e}-exp"] = f"벡터 {e}+확장"
        labels[f"hybrid-{e}"] = f"하이브리드 RRF(BM25+확장, {e})"
    return labels


def embedder_of(name: str) -> str | None:
    e = name.removeprefix("hybrid-").removesuffix("-exp")
    return e if e in EMBED_MODELS else None


def default_retrievers(dense: list[str]) -> list[str]:
    """--retrievers 를 안 주면 예전처럼 BM25 두 가지에 --dense 모델별 세 가지를 붙인다."""
    return ["bm25", "bm25-noexp"] + [n for e in dense for n in (e, f"{e}-exp", f"hybrid-{e}")]


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


def hit(rank: int | None, k: int = 5) -> bool:
    return rank is not None and rank <= k


def bootstrap_diff(a: list[int | None], b: list[int | None], k: int = 5, n: int = 10000) -> tuple:
    """Hit@k(b) - Hit@k(a) 의 95% 구간 (질문 단위 paired bootstrap)."""
    return bootstrap_hits([hit(r, k) for r in a], [hit(r, k) for r in b], n)


def bootstrap_hits(ha: list[float], hb: list[float], n: int = 10000) -> tuple:
    """질문별 적중(0~1) 평균 차이 mean(hb) - mean(ha) 의 95% 구간."""
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

    selected = args.retrievers or default_retrievers(args.dense)
    labels = retriever_labels()
    base_label = labels[BASELINE]
    embedders = list(dict.fromkeys(e for n in selected if (e := embedder_of(n))))
    doc_vecs = {e: cached_embed(e, "docs", [unit_text(u) for u in units], args.embed_python)
                for e in embedders}

    # 질문 세트: 사용자 원문 질문 전체, 그리고 분해 질문이 있는 fixture 부분
    # GLOSSARY 는 fixture 질문을 보며 만든 것이라, 새로 쓴 질문만 따로도 본다
    sets = {"원문 질문 (전체)": [(it, it["query"]) for it in gold["items"]],
            "새로 쓴 질문": [(it, it["query"]) for it in gold["items"] if it["source"] == "new"],
            "fixture 원문 질문": [(it, it["query"]) for it in gold["items"] if "decomposed" in it],
            "fixture 분해 질문": [(it, it["decomposed"]) for it in gold["items"] if "decomposed" in it]}

    report = {"as_of": as_of, "units": len(units), "retrievers": selected, "results": {}, "per_query": {}}
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

                bm = keep(rank_bm25(bm25, q, True))      # 하이브리드와 비교 기준에 늘 쓴다
                runs = {base_label: bm}
                for name in selected:
                    e = embedder_of(name)
                    if name == "bm25-noexp":
                        r = keep(rank_bm25(bm25, q, False))
                    elif name == e:
                        r = keep(rank_dense(doc_vecs[e], q_vecs[e][0][j]))
                    elif name == f"{e}-exp":
                        r = keep(rank_dense(doc_vecs[e], q_vecs[e][1][j]))
                    elif name == f"hybrid-{e}":
                        r = rank_rrf(bm, keep(rank_dense(doc_vecs[e], q_vecs[e][0][j])))
                    else:
                        continue                            # bm25 는 위에서 넣었다
                    runs[labels[name]] = r
                for name, r in runs.items():
                    methods.setdefault(name, []).append(first_hit(r, arts, g))
            label = f"{set_name} / {'법령 필터' if use_filter else '전체 법령'}"
            base = methods[base_label]
            if BASELINE not in selected:                 # 고르지 않았으면 표에서는 뺀다
                del methods[base_label]
            report["results"][label] = {m: summarize(r) for m, r in methods.items()}
            report["per_query"][label] = {m: dict(zip([it["id"] for it, _ in pairs], r))
                                          for m, r in methods.items()}
            report["results"][label]["_bootstrap_Hit@5_vs_BM25+확장"] = {
                m: bootstrap_diff(base, r) for m, r in methods.items() if m != base_label}
            print_table(label, len(pairs), report["results"][label])

    if args.decomposed:
        dec_out = HERE / "results" / "decomposition_eval.json"
        dec_out.write_text(json.dumps(run_decomposition(gold, args.decomposed, units, bm25),
                                      ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n분해 비교 질문별 순위: {dec_out.relative_to(HERE.parent)}")

    if args.min_hit_at_5 is not None:
        report["gate"] = gate(report["results"][GATE_SET], args.min_hit_at_5)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n질문별 순위: {out}")
    return report.get("gate", {}).get("passed", True)


def gate(rows: dict, minimum: float) -> dict:
    print(f"\n## 기준: {GATE_SET} Hit@5 >= {minimum:.3f}")
    failed = []
    for name, row in rows.items():
        if name.startswith("_"):
            continue
        ok = row["Hit@5"] >= minimum
        print(f"{'통과' if ok else '미달'}  {name:<44}{row['Hit@5']:>9.3f}")
        if not ok:
            failed.append(name)
    print("결과: " + ("모두 통과" if not failed else f"{len(failed)}개 방식 미달"))
    return {"set": GATE_SET, "min_hit_at_5": minimum, "failed": failed, "passed": not failed}


def parse_retrievers(value: str) -> list[str]:
    names = [n.strip() for n in value.split(",") if n.strip()]
    unknown = [n for n in names if n not in retriever_labels()]
    if unknown or not names:
        raise argparse.ArgumentTypeError(
            f"모르는 방식 {unknown or value!r}. 쓸 수 있는 이름: {', '.join(retriever_labels())}")
    return list(dict.fromkeys(names))


def env_float(name: str) -> float | None:
    value = os.getenv(name, "").strip()
    return float(value) if value else None


# ---------- 실제 분해 비교 ----------

def run_decomposition(gold: dict, path: Path, units: list[dict], bm25) -> dict:
    """원문 / 사람 분해 / 실제 분해 질문으로 BM25 검색 점수를 비교한다.

    실제 분해는 agent 라우터(_law_check)처럼 law_check 하위 질문마다 search_regulation(question, laws=[law])을
    부른 것으로 보고, 하위 질문 중 정답이 가장 높게 나온 순위를 그 질문의 순위로 쓴다.
    law_check 하위 질문이 없으면(다른 intent로 분해되면) 법령 검색을 하지 않으므로 놓친 것으로 센다.
    "+원문" 은 라우터가 question 뒤에 그 하위 질문의 원문(original_text)을 이어 붙여 검색한다고 가정한 경우다.
    분해를 여러 번 돌린 결과(runs)면 실행마다 지표를 내 평균하고, 최저·최고를 함께 남긴다.
    """
    dec = json.loads(path.read_text(encoding="utf-8"))
    runs = dec["runs"] if "runs" in dec else [dec["items"]]
    as_of = gold["as_of"]
    arts = [unit_article(u) for u in units]

    def real_rank(subs: list[dict], it: dict, expand: bool, with_original: bool = False) -> int | None:
        hits = []
        for s in subs:
            if s["intent"] != "law_check":
                continue
            law = s["entities"].get("law")
            keys = (server.resolve_law(law)["law_key"],) if law else tuple(server.LAWS)
            us, bm = server.search_units(keys, as_of, False)   # 서버처럼 법령 필터면 그 법령만으로 색인한다
            q = f"{s['question']} {s['original_text']}" if with_original else s["question"]
            hits.append(first_hit(rank_bm25(bm, q, expand), [unit_article(u) for u in us],
                                  set(it["gold"])))
        return min((h for h in hits if h), default=None)

    def orig_rank(it: dict, q: str, expand: bool) -> int | None:
        return first_hit(rank_bm25(bm25, q, expand), arts, set(it["gold"]))

    items = gold["items"]
    sets = {"전체": items,
            "개발용 (보면서 고치는 질문)": [it for it in items if it["split"] == "dev"],
            "확인용 (점수만 보는 질문)": [it for it in items if it["split"] == "test"],
            "새로 쓴 질문": [it for it in items if it["source"] == "new"],
            "보강 질문 (원문 BM25 약점)": [it for it in items if it["source"] == "new_hard"],
            "fixture (사람 분해 있음)": [it for it in items if "decomposed" in it]}
    real = {"실제 분해": f"실제 분해 ({dec.get('model')})",
            "실제 분해+원문": "실제 분해 + 하위 질문 원문 이어 붙이기"}
    out = {"backend": dec.get("backend"), "model": dec.get("model"), "repeat": len(runs),
           "results": {}, "per_query": {}}
    for expand in (True, False):
        tag = "BM25+확장 (현재)" if expand else "BM25"
        per = {it["id"]: {"원문": orig_rank(it, it["query"], expand),
                          "사람 분해": orig_rank(it, it["decomposed"], expand) if "decomposed" in it else None,
                          "실제 분해": [real_rank(r.get(it["id"], {}).get("sub_queries", []), it, expand)
                                    for r in runs],
                          "실제 분해+원문": [real_rank(r.get(it["id"], {}).get("sub_queries", []), it, expand, True)
                                       for r in runs]} for it in items}
        out["per_query"][tag] = per
        for set_name, its in sets.items():
            rows = {"원문": summarize([per[it["id"]]["원문"] for it in its])}
            if all("decomposed" in it for it in its):
                rows["사람 분해"] = summarize([per[it["id"]]["사람 분해"] for it in its])
            base = [hit(per[it["id"]]["원문"]) for it in its]
            boot = {}
            for key, name in real.items():
                by_run = [summarize([per[it["id"]][key][j] for it in its]) for j in range(len(runs))]
                rows[name] = {m: sum(r[m] for r in by_run) / len(by_run) for m in by_run[0]}
                rows[name]["Hit@5 최저"] = min(r["Hit@5"] for r in by_run)
                rows[name]["Hit@5 최고"] = max(r["Hit@5"] for r in by_run)
                # 질문마다 실행 중 맞힌 비율로 paired bootstrap
                boot[key] = bootstrap_hits(base, [sum(map(hit, per[it["id"]][key])) / len(runs) for it in its])
            rows["_bootstrap_Hit@5_vs_원문"] = boot
            label = f"{set_name} / {tag}"
            out["results"][label] = rows
            print_table(label, len(its), rows)

    per = out["per_query"]["BM25+확장 (현재)"]
    print("\n## 원문과 실제 분해가 갈린 질문 (BM25+확장, Hit@5 기준, 분해 실행별 순위)")
    for it in items:
        o, rs = per[it["id"]]["원문"], per[it["id"]]["실제 분해"]
        if any(hit(o) != hit(r) for r in rs):
            subs = [s["question"] if s["intent"] == "law_check" else f"[{s['intent']}]"
                    for s in runs[0].get(it["id"], {}).get("sub_queries", [])]
            print(f"  {it['id']:<28} 원문 {o or '-'}위 → 분해 {', '.join(str(r or '-') for r in rs)}위  "
                  f"{it['query']}\n  {'':<28} 분해(1회차): {' | '.join(subs)}")
    return out


def print_table(label: str, n: int, rows: dict):
    print(f"\n## {label} (n={n})")
    cols = [f"Hit@{k}" for k in KS] + ["MRR@10"]
    print(f"{'방식':<44}" + "".join(f"{c:>9}" for c in cols))
    for name, row in rows.items():
        if name.startswith("_"):
            continue
        print(f"{name:<44}" + "".join(f"{row[c]:>9.3f}" for c in cols))
        if "Hit@5 최저" in row:
            print(f"{'':<44}  Hit@5 실행별 {row['Hit@5 최저']:.3f} ~ {row['Hit@5 최고']:.3f}")
    base, diffs = next((k.removeprefix("_bootstrap_Hit@5_vs_"), v) for k, v in rows.items()
                       if k.startswith("_bootstrap"))
    for name, (lo, hi) in diffs.items():
        print(f"  Hit@5 차이 95% 구간 [{name} - {base}]: {lo:+.3f} ~ {hi:+.3f}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dense", nargs="*", default=[], choices=list(EMBED_MODELS),
                   help="함께 평가할 임베딩 모델")
    p.add_argument("--embed-python", default="python3",
                   help="embed.py 를 실행할 파이썬 (torch·transformers 또는 openai 필요)")
    p.add_argument("--decomposed", type=Path,
                   help="eval/decompose_gold.py 가 저장한 실제 분해 결과. 주면 원문/사람 분해/실제 분해 비교를 더한다")
    p.add_argument("--retrievers", type=parse_retrievers,
                   default=os.getenv("RETRIEVERS", "").strip() or None,   # 문자열 기본값에도 type 이 적용된다
                   help="쉼표로 구분한 평가 방식 (환경 변수 RETRIEVERS). 예: bm25,bm25-noexp,bge-m3,hybrid-bge-m3")
    p.add_argument("--min-hit-at-5", type=float, default=env_float("MIN_HIT_AT_5"),
                   help=f"{GATE_SET} Hit@5 기준. 고른 방식 중 하나라도 낮으면 종료 코드 1 (환경 변수 MIN_HIT_AT_5)")
    p.add_argument("--out", default=os.getenv("RESULTS_OUT") or str(HERE / "results" / "retrieval_eval.json"),
                   help="결과 JSON 경로 (환경 변수 RESULTS_OUT)")
    sys.exit(0 if run(p.parse_args()) else 1)
