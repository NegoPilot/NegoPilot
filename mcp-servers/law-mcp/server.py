"""하도급법·연동제 관련 법령을 로컬 파일에서 제공하는 MCP Server.

data/index/*.json 만 읽고, 실행 중에는 네트워크를 쓰지 않는다.
법령마다 여러 판(개정본)을 갖고 있어서, as_of 날짜에 시행 중인 판으로 답한다.

실행:
    uv run python server.py                 # stdio
    uv run python server.py --http 9103     # Streamable HTTP (http://localhost:9103/mcp)
"""

import argparse
import json
import math
import re
from collections import Counter
from datetime import date
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

INDEX_DIR = Path(__file__).resolve().parent / "data" / "index"

# 사람이 부르는 이름 → law_key
ALIASES = {
    "하도급법": "subcontract_act",
    "하도급거래공정화에관한법률": "subcontract_act",
    "하도급법시행령": "subcontract_decree",
    "상생협력법": "winwin_act",
    "대중소기업상생협력촉진에관한법률": "winwin_act",
    "상생협력법시행령": "winwin_decree",
    "중소기업기본법": "sme_act",
    "중소기업기본법시행령": "sme_decree",
}

mcp = FastMCP("LawServer", instructions=(
    "하도급법, 대ㆍ중소기업 상생협력법(납품대금 연동제), 중소기업기본법과 각 시행령을 "
    "조문 단위로 제공한다. 답변에는 반드시 citation 값을 그대로 인용한다. "
    "법령은 as_of 날짜에 시행 중인 판 기준이며, 시행 예정 개정이 있으면 upcoming 에 표시된다."
))


# ---------- 데이터 적재 ----------

def load_laws() -> dict[str, dict]:
    laws = {}
    for file in sorted(INDEX_DIR.glob("*.json")):
        law = json.loads(file.read_text(encoding="utf-8"))
        laws[law["law_key"]] = law
    return laws


LAWS = load_laws()


def resolve_law(law: str) -> dict:
    key = law.strip()
    if key not in LAWS:
        key = ALIASES.get(re.sub(r"[\sㆍ·]", "", key), key)
    if key not in LAWS:
        raise ToolError(f"알 수 없는 법령 '{law}'. 사용 가능: {', '.join(LAWS)} "
                        f"또는 별칭 {', '.join(ALIASES)}")
    return LAWS[key]


def parse_as_of(as_of: str | None) -> str:
    if not as_of:
        return date.today().isoformat()
    try:
        return date.fromisoformat(as_of).isoformat()
    except ValueError as exc:
        raise ToolError(f"as_of 는 YYYY-MM-DD 형식이어야 한다: '{as_of}'") from exc


def version_in_force(law: dict, as_of: str) -> dict:
    """as_of 이전에 시행된 판 중 가장 최근 판. 없으면 가장 오래된 판."""
    candidates = [v for v in law["versions"] if v["effective_date"] <= as_of]
    return candidates[-1] if candidates else law["versions"][0]


def upcoming_versions(law: dict, as_of: str) -> list[dict]:
    return [{"effective_date": v["effective_date"],
             "promulgation_date": v["promulgation_date"],
             "promulgation_no": v["promulgation_no"]}
            for v in law["versions"] if v["effective_date"] > as_of]


def normalize_article_no(article_no: str) -> str:
    s = re.sub(r"\s", "", article_no)
    m = re.fullmatch(r"제?(\d+)(?:조)?(?:의(\d+))?", s)
    if not m:
        raise ToolError(f"조문 번호 형식을 알 수 없다: '{article_no}' (예: 제4조, 16조의2)")
    return f"제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else "")


def citation(version: dict, article: dict, paragraph: str | None = None) -> str:
    para = paragraph if paragraph else ""
    return (f"「{version['law_name']}」 {article['no']}{para}"
            f"({article['title']}) [시행 {version['effective_date']}]")


# ---------- 검색 (한글 문자 bigram + BM25) ----------

# 현업 표현 → 법령 용어. 질문에 왼쪽 표현이 있으면 오른쪽 용어를 덧붙여 검색한다
GLOSSARY = {
    "1차 협력사": "원사업자",
    "1차협력사": "원사업자",
    "2차 협력사": "수급사업자",
    "2차협력사": "수급사업자",
    "협력사": "수급사업자",
    "하청": "수급사업자 하도급",
    "납품단가": "하도급대금 납품대금",
    "단가": "하도급대금 단가",
    "깎": "인하 감액",
    "인하": "인하 하도급대금의 결정",
    "전 품목": "일률적인 비율",
    "일괄": "일률적인 비율",
    "같은 비율": "일률적인 비율",
    "원자재": "주요 원재료 공급원가",
    "원재료": "주요 원재료 공급원가",
    "연동": "연동 조정요건 기준 지표",
    "계약서": "서면 발급",
    "대금 지급": "하도급대금의 지급 60일",
    "기술 자료": "기술자료 제공 요구",
}


def expand_query(query: str) -> str:
    extra = [term for key, term in GLOSSARY.items() if key in query]
    return query + (" " + " ".join(extra) if extra else "")


def tokenize(text: str) -> list[str]:
    tokens = re.findall(r"제\d+조(?:의\d+)?|\d+", text)
    compact = re.sub(r"[^0-9A-Za-z가-힣]", "", text)
    tokens += [compact[i:i + 2] for i in range(len(compact) - 1)]
    return tokens


class Bm25:
    def __init__(self, docs: list[list[str]], k1: float = 1.4, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tf = [Counter(d) for d in docs]
        self.len = [len(d) for d in docs]
        self.avg = sum(self.len) / max(len(docs), 1)
        df = Counter(t for d in self.tf for t in d)
        n = len(docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def scores(self, query: list[str]) -> list[float]:
        out = []
        for tf, dl in zip(self.tf, self.len):
            s = 0.0
            for t in query:
                if t in tf:
                    f = tf[t]
                    s += self.idf[t] * f * (self.k1 + 1) / (
                        f + self.k1 * (1 - self.b + self.b * dl / self.avg))
            out.append(s)
        return out


_search_cache: dict[tuple, tuple[list[dict], Bm25]] = {}


def search_units(law_keys: tuple[str, ...], as_of: str, include_addenda: bool):
    """항 단위 검색 문서 목록과 BM25 인덱스. 같은 조건이면 재사용한다."""
    cache_key = (law_keys, as_of, include_addenda)
    if cache_key in _search_cache:
        return _search_cache[cache_key]
    units = []
    for key in law_keys:
        law = LAWS[key]
        v = version_in_force(law, as_of)
        for art in v["articles"]:
            if art["deleted"]:
                continue
            for p in art["paragraphs"]:
                header = f"{art['no']} {art['title']}"
                units.append({"law_key": key, "version": v, "article": art,
                              "paragraph": p["no"], "text": p["text"],
                              "tokens": tokenize(header + " " + header + " " + p["text"])})
        if include_addenda:
            for ad in v["addenda"]:
                units.append({"law_key": key, "version": v, "article": None,
                              "addendum": ad["title"], "text": ad["text"],
                              "tokens": tokenize(ad["title"] + " " + ad["text"])})
    result = (units, Bm25([u["tokens"] for u in units]))
    _search_cache[cache_key] = result
    return result


# ---------- Tools ----------

@mcp.tool(annotations={"readOnlyHint": True})
def list_laws(as_of: str | None = None) -> list[dict]:
    """제공하는 법령 목록과, as_of(YYYY-MM-DD, 기본 오늘)에 시행 중인 판·시행 예정 판을 반환한다."""
    day = parse_as_of(as_of)
    out = []
    for key, law in LAWS.items():
        v = version_in_force(law, day)
        out.append({"law_key": key, "law_name": v["law_name"], "law_type": v["law_type"],
                    "ministry": v["ministry"], "in_force_effective_date": v["effective_date"],
                    "article_count": len(v["articles"]), "source_url": v["source_url"],
                    "upcoming": upcoming_versions(law, day)})
    return out


@mcp.tool(annotations={"readOnlyHint": True})
def get_article(law: str, article_no: str, as_of: str | None = None) -> dict:
    """특정 조문 전문을 항 단위로 반환한다.

    law: law_key(subcontract_act 등) 또는 별칭(하도급법, 상생협력법, 중소기업기본법 시행령 등)
    article_no: '제4조', '16조의2' 처럼 입력
    as_of: 기준일(YYYY-MM-DD). 생략하면 오늘 시행 중인 판
    """
    law_data = resolve_law(law)
    day = parse_as_of(as_of)
    no = normalize_article_no(article_no)
    v = version_in_force(law_data, day)
    article = next((a for a in v["articles"] if a["no"] == no), None)
    if article is None:
        nearby = [a["no"] for a in v["articles"]
                  if a["no"].split("조")[0] == no.split("조")[0]]
        raise ToolError(f"{v['law_name']}({v['effective_date']} 시행판)에 {no}가 없다. "
                        f"비슷한 조문: {nearby or '없음'}")

    # 시행 예정 판에서 이 조문이 바뀌는지 알려 준다
    changes = []
    for fv in (x for x in law_data["versions"] if x["effective_date"] > day):
        future = next((a for a in fv["articles"] if a["no"] == no), None)
        if future is None or future["text"] != article["text"]:
            changes.append({"effective_date": fv["effective_date"],
                            "promulgation_date": fv["promulgation_date"],
                            "change": "삭제" if future is None else "개정",
                            "text": future["text"] if future else None})

    return {"law_key": law_data["law_key"], "law_name": v["law_name"],
            "article_no": article["no"], "title": article["title"],
            "chapter": article["chapter"], "deleted": article["deleted"],
            "paragraphs": article["paragraphs"],
            "citation": citation(v, article),
            "effective_date": v["effective_date"], "as_of": day,
            "source_url": v["source_url"], "upcoming_changes": changes}


@mcp.tool(annotations={"readOnlyHint": True})
def search_regulation(query: str, laws: list[str] | None = None, as_of: str | None = None,
                      top_k: int = 5, include_addenda: bool = False) -> list[dict]:
    """질문과 관련된 조문을 항 단위로 찾아 인용 정보와 함께 반환한다.

    query: 자연어 질문이나 키워드 (예: '일률적 단가 인하', '연동 약정 예외').
           법령 용어(원사업자, 수급사업자, 하도급대금)로 쓰면 정확도가 높다.
           '1차 협력사', '단가' 같은 현업 표현은 법령 용어를 덧붙여 검색한다.
    laws: 검색할 법령(law_key 또는 별칭). 생략하면 전체
    as_of: 기준일(YYYY-MM-DD). 생략하면 오늘 시행 중인 판
    include_addenda: 부칙(시행일·경과조치)까지 검색할지
    """
    if not query.strip():
        raise ToolError("query 가 비어 있다")
    day = parse_as_of(as_of)
    keys = tuple(sorted({resolve_law(l)["law_key"] for l in laws})) if laws else tuple(LAWS)
    top_k = max(1, min(top_k, 20))
    units, bm25 = search_units(keys, day, include_addenda)
    scores = bm25.scores(tokenize(expand_query(query)))
    ranked = sorted(range(len(units)), key=lambda i: scores[i], reverse=True)

    results = []
    for i in ranked[:top_k]:
        if scores[i] <= 0:
            break
        u, v = units[i], units[i]["version"]
        if u["article"] is None:
            results.append({"law_key": u["law_key"], "law_name": v["law_name"],
                            "type": "부칙", "title": u["addendum"], "text": u["text"][:1500],
                            "citation": f"「{v['law_name']}」 {u['addendum']}",
                            "effective_date": v["effective_date"], "score": round(scores[i], 2)})
        else:
            a = u["article"]
            results.append({"law_key": u["law_key"], "law_name": v["law_name"],
                            "type": "조문", "article_no": a["no"], "title": a["title"],
                            "paragraph": u["paragraph"], "text": u["text"][:1500],
                            "citation": citation(v, a, u["paragraph"]),
                            "effective_date": v["effective_date"], "source_url": v["source_url"],
                            "score": round(scores[i], 2)})
    return results


# ---------- Resources ----------

@mcp.resource("law://catalog", mime_type="application/json")
def catalog() -> str:
    """제공 법령 목록 (오늘 기준 시행판)."""
    return json.dumps(list_laws(), ensure_ascii=False, indent=1)


@mcp.resource("law://{law_key}/{article_no}", mime_type="text/plain")
def article_resource(law_key: str, article_no: str) -> str:
    """조문 전문 텍스트 (오늘 기준 시행판). 예: law://subcontract_act/제4조"""
    a = get_article(law_key, article_no)
    body = "\n\n".join(p["text"] for p in a["paragraphs"])
    return f"{a['citation']}\n출처: {a['source_url']}\n\n{body}"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--http", type=int, metavar="PORT",
                        help="Streamable HTTP로 실행할 포트 (생략하면 stdio)")
    args = parser.parse_args()
    if args.http:
        mcp.run(transport="http", host="0.0.0.0", port=args.http)
    else:
        mcp.run()
