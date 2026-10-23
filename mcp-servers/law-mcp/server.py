"""하도급법·연동제 관련 법령을 로컬 파일에서 제공하는 MCP Server.

기본 법령은 data/index/*.json에서 읽는다. 관리자 최신화 요청만 외부 수집을 실행한다.
법령마다 여러 판(개정본)을 갖고 있어서, as_of 날짜에 시행 중인 판으로 답한다.

실행:
    uv run python server.py                 # stdio
    uv run python server.py --http 9103     # Streamable HTTP (http://localhost:9103/mcp)
"""

import argparse
import hashlib
import json
import math
import os
import secrets
import threading
from functools import wraps
import re
from collections import Counter, OrderedDict
from datetime import date, datetime, timezone
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from starlette.responses import JSONResponse

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


DATASET_LOCK = threading.RLock()


def dataset_read(fn):
    @wraps(fn)
    def read(*args, **kwargs):
        with DATASET_LOCK:
            return fn(*args, **kwargs)
    return read


LAWS = load_laws()
DATASET_COLLECTED_AT = None
DATASET_LOADED_AT = datetime.now(timezone.utc).isoformat()
DATASET_ID = hashlib.sha256(json.dumps(LAWS, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def admin_version_id(version: dict) -> str:
    return version["effective_date"] + "_" + version["commit"]


def admin_version_summary(version: dict) -> dict:
    return {"version_id": admin_version_id(version), "effective_date": version["effective_date"],
            "promulgation_date": version["promulgation_date"], "promulgation_no": version["promulgation_no"],
            "source_url": version["source_url"], "source_commit": version["commit"],
            "article_count": len(version["articles"])}


def admin_exact_version(law_key: str, version_id: str) -> dict | None:
    law = LAWS.get(law_key)
    return next((v for v in law["versions"] if admin_version_id(v) == version_id), None) if law else None


@mcp.tool(annotations={"readOnlyHint": True})
@dataset_read
def admin_law_catalog() -> dict:
    """관리자 화면용. 이 서버가 지금 불러온 법령 데이터의 목록과 건수를 반환한다. 수집·수정은 하지 않는다."""
    items = [{"law_key": key, "law_name": law["law_name"], "version_count": len(law["versions"]),
              "article_count": sum(len(v["articles"]) for v in law["versions"])}
             for key, law in sorted(LAWS.items())]
    return {"dataset_id": DATASET_ID, "loaded_at": DATASET_LOADED_AT, "collected_at": DATASET_COLLECTED_AT,
            "law_count": len(items), "version_count": sum(x["version_count"] for x in items),
            "article_count": sum(x["article_count"] for x in items), "items": items}


@mcp.tool(annotations={"readOnlyHint": True})
@dataset_read
def admin_law_versions(law_key: str) -> dict | None:
    """관리자 화면용. 법령 하나가 가진 모든 판(최신 판부터)을 반환한다. 기준일로 판을 고르지 않는다.

    law_key: subcontract_act 처럼 정확한 키 (별칭은 받지 않는다). 없으면 null
    """
    law = LAWS.get(law_key)
    if law is None:
        return None
    return {"law_key": law_key, "law_name": law["law_name"],
            "items": [admin_version_summary(v) for v in reversed(law["versions"])]}


@mcp.tool(annotations={"readOnlyHint": True})
@dataset_read
def admin_law_articles(law_key: str, version_id: str, offset: int = 0, limit: int = 50) -> dict | None:
    """관리자 화면용. 지정한 판의 조문 목록(번호, 제목, 삭제 여부)을 페이지 단위로 반환한다.

    version_id: admin_law_versions의 version_id. 판이 없으면 null
    offset: 0 이상, limit: 1~100
    """
    if offset < 0 or not 1 <= limit <= 100:
        raise ToolError("offset 은 0 이상, limit 은 1~100 이어야 한다")
    version = admin_exact_version(law_key, version_id)
    if version is None:
        return None
    articles = version["articles"]
    return {"law_key": law_key, **admin_version_summary(version), "total": len(articles),
            "offset": offset, "limit": limit,
            "items": [{k: a[k] for k in ("no", "title", "deleted")} for a in articles[offset:offset+limit]]}


@mcp.tool(annotations={"readOnlyHint": True})
@dataset_read
def admin_law_article(law_key: str, version_id: str, article_no: str) -> dict | None:
    """관리자 화면용. 지정한 판의 조문 하나를 전문과 인용 문구로 반환한다. 판이나 조문이 없으면 null.

    article_no: '제4조', '16조의2' 처럼 입력. 데이터를 바꾸거나 갱신하지 않는다
    """
    version = admin_exact_version(law_key, version_id)
    if version is None:
        return None
    no = normalize_article_no(article_no)
    article = next((a for a in version["articles"] if a["no"] == no), None)
    if article is None:
        return None
    return {"law_key": law_key, "law_name": version["law_name"], **admin_version_summary(version),
            "article": article, "citation": citation(version, article)}


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
    # 낮추는 시점(정할 때/정한 뒤)에 따라 제4조와 제11조로 갈려 한쪽으로 몰지 않는다 (#168)
    "깎": "인하 감액",
    "낮추": "인하 감액",
    "낮춰": "인하 감액",
    "인하": "인하 감액 하도급대금의 결정",
    "돌려보내": "반품",
    "되돌려": "반품",
    "철회": "위탁취소",
    "전 품목": "일률적인 비율",
    "일괄": "일률적인 비율",
    "같은 비율": "일률적인 비율",
    "원자재": "주요 원재료 공급원가",
    "원재료": "주요 원재료 공급원가",
    "연동": "연동 조정요건 기준 지표",
    "계약서": "서면 발급",
    # 기한 숫자를 덧붙이면 '지급 후' 같은 말에도 제13조로 몰려 다른 조문을 밀어낸다 (#168)
    "대금 지급": "하도급대금의 지급",
    "기술 자료": "기술자료 제공 요구",
    "도면": "기술자료",
    "노하우": "기술자료",
    "협찬": "경제적 이익",
}



def expand_query(query: str) -> str:
    """현업 표현을 찾아 법령 용어를 덧붙인다.

    긴 표현부터 찾고, 이미 찾은 글자는 짧은 표현이 다시 쓰지 않는다.
    예: '1차 협력사'는 원사업자만 붙고, 그 안의 '협력사'로 수급사업자가 함께 붙지 않는다.
    """
    used = [False] * len(query)
    found: list[tuple[int, str]] = []          # (질문 안 위치, 덧붙일 용어)
    for key in sorted(GLOSSARY, key=len, reverse=True):
        start = query.find(key)
        while start >= 0:
            end = start + len(key)
            if not any(used[start:end]):
                used[start:end] = [True] * len(key)
                found.append((start, GLOSSARY[key]))
            start = query.find(key, end)
    extra = list(dict.fromkeys(term for _, term in sorted(found)))   # 질문에 나온 순서, 같은 용어는 한 번
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


# 법령 조합·시행판마다 색인 하나. 오래 안 쓴 색인부터 버린다 (LRU)
SEARCH_CACHE_SIZE = 32
_search_cache: OrderedDict[tuple, tuple[list[dict], Bm25]] = OrderedDict()


# 정의 조항: '이 법에서 "주요 원재료"란 …을 말한다'. 시행령의 '대통령령으로 정하는 …'은 정의가 아니라 위임이라 뺀다
DEFINED_TERM = re.compile(r'"([^"]{2,30})"(?:이)?란')
DEFINITION_ARTICLE = "제2조"
MAX_DEFINITIONS = 2
# 원사업자·중소기업처럼 그 법령의 많은 항에 나오는 용어는 정의를 붙여도 도움이 안 되고 결과만 늘린다.
# 비율은 그 법령의 항 수 기준이라 법령 필터와 관계없이 같은 용어가 붙는다
COMMON_TERM_SHARE = 0.05


def definition_text(text: str, term: str) -> str:
    """정의 항에서 그 용어를 정의한 부분만 꺼낸다.

    상생협력법 제2조처럼 한 항에 호(1. 2. …)로 여러 용어를 정의하면 그 호와 딸린 목만 남긴다.
    한 항에 용어 하나면 항 전체를 쓴다.
    """
    if len(DEFINED_TERM.findall(text)) <= 1:
        return text
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if f'"{term}"' in line)
    indent = len(lines[start]) - len(lines[start].lstrip())
    end = start + 1
    while end < len(lines) and lines[end].strip() and len(lines[end]) - len(lines[end].lstrip()) > indent:
        end += 1
    return "\n".join(line.strip() for line in lines[start:end])


def definition_units(units: list[dict]) -> dict[tuple[str, str], tuple[int, str]]:
    """(law_key, 용어) → (그 용어를 정의한 항의 색인 위치, 정의 부분). 흔한 용어는 뺀다."""
    out = {}
    law_size = {k: sum(1 for v in units if v["law_key"] == k) for k in {u["law_key"] for u in units}}
    for i, u in enumerate(units):
        if u["article"] is None or u["article"]["no"] != DEFINITION_ARTICLE or u["law_key"].endswith("_decree"):
            continue
        for term in DEFINED_TERM.findall(u["text"]):
            if term.startswith("대통령령"):
                continue
            df = sum(1 for v in units if v["law_key"] == u["law_key"] and term in v["text"])
            if df <= COMMON_TERM_SHARE * law_size[u["law_key"]]:
                out.setdefault((u["law_key"], term), (i, definition_text(u["text"], term)))
    return out


def search_units(law_keys: tuple[str, ...], as_of: str, include_addenda: bool):
    """항 단위 검색 문서 목록과 BM25 인덱스. 같은 조건이면 재사용한다.

    캐시 키는 기준일이 아니라 그날 시행 중인 판이다. 기준일이 달라도 시행판이 같으면 같은 색인을 쓴다.
    """
    versions = {key: version_in_force(LAWS[key], as_of) for key in law_keys}
    cache_key = (law_keys, tuple(admin_version_id(v) for v in versions.values()), include_addenda)
    if cache_key in _search_cache:
        _search_cache.move_to_end(cache_key)
        return _search_cache[cache_key]
    units = []
    for key in law_keys:
        v = versions[key]
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
    result = (units, Bm25([u["tokens"] for u in units]), definition_units(units))
    _search_cache[cache_key] = result
    while len(_search_cache) > SEARCH_CACHE_SIZE:
        _search_cache.popitem(last=False)
    return result


# ---------- Tools ----------

@mcp.tool(annotations={"readOnlyHint": True})
@dataset_read
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
@dataset_read
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
@dataset_read
def search_regulation(query: str, laws: list[str] | None = None, as_of: str | None = None,
                      top_k: int = 5, include_addenda: bool = False) -> list[dict]:
    """질문과 관련된 조문을 항 단위로 찾아 인용 정보와 함께 반환한다.

    query: 자연어 질문이나 키워드 (예: '일률적 단가 인하', '연동 약정 예외').
           법령 용어(원사업자, 수급사업자, 하도급대금)로 쓰면 정확도가 높다.
           '1차 협력사', '단가' 같은 현업 표현은 법령 용어를 덧붙여 검색한다.
    laws: 검색할 법령(law_key 또는 별칭). 생략하면 전체
    as_of: 기준일(YYYY-MM-DD). 생략하면 오늘 시행 중인 판
    include_addenda: 부칙(시행일·경과조치)까지 검색할지
    결과 조문에 정의된 용어(예: 주요 원재료)가 나오면 그 용어의 정의 항(제2조)을 top_k 뒤에 최대 2개 덧붙인다.
    덧붙인 항은 type이 "정의"다. 의무 조문만으로는 "10% 이상" 같은 기준이 빠지기 때문이다 (#167).
    """
    if not query.strip():
        raise ToolError("query 가 비어 있다")
    day = parse_as_of(as_of)
    keys = tuple(sorted({resolve_law(l)["law_key"] for l in laws})) if laws else tuple(LAWS)
    top_k = max(1, min(top_k, 20))
    units, bm25, definitions = search_units(keys, day, include_addenda)
    scores = bm25.scores(tokenize(expand_query(query)))
    ranked = sorted(range(len(units)), key=lambda i: scores[i], reverse=True)

    hits = [i for i in ranked[:top_k] if scores[i] > 0]
    # 검색된 조문에 나오는 정의 용어의 정의 항. 같은 법령의 정의만 붙이고, 이미 결과에 있으면 다시 넣지 않는다
    extra: dict[int, str] = {}
    for i in hits:
        for (law_key, term), (d, text) in definitions.items():
            if (law_key == units[i]["law_key"] and term in units[i]["text"] and d not in hits and d not in extra
                    and len(extra) < MAX_DEFINITIONS):
                extra[d] = text

    results = []
    for i in hits + list(extra):
        u, v = units[i], units[i]["version"]
        if u["article"] is None:
            results.append({"law_key": u["law_key"], "law_name": v["law_name"],
                            "type": "부칙", "title": u["addendum"], "text": u["text"][:1500],
                            "citation": f"「{v['law_name']}」 {u['addendum']}",
                            "effective_date": v["effective_date"], "score": round(scores[i], 2)})
        else:
            a = u["article"]
            results.append({"law_key": u["law_key"], "law_name": v["law_name"],
                            "type": "조문" if i in hits else "정의", "article_no": a["no"], "title": a["title"],
                            "paragraph": u["paragraph"], "text": extra.get(i, u["text"])[:1500],
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


# Refresh is an authenticated HTTP control endpoint. It is intentionally absent from MCP tools.
def activate_dataset(laws, collected_at):
    global LAWS, DATASET_ID, DATASET_LOADED_AT, DATASET_COLLECTED_AT
    with DATASET_LOCK:
        LAWS = laws
        DATASET_ID = hashlib.sha256(json.dumps(laws, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        DATASET_COLLECTED_AT = collected_at
        DATASET_LOADED_AT = datetime.now(timezone.utc).isoformat()
        _search_cache.clear()


REFRESH_MANAGER = None
LAW_ADMIN_API_KEY = os.getenv("LAW_ADMIN_API_KEY", "")
if os.getenv("LAW_RUNTIME_DIR"):
    from law_refresh import LawRefreshManager
    REFRESH_MANAGER = LawRefreshManager(os.environ["LAW_RUNTIME_DIR"], LAWS, activate_dataset)


def refresh_auth(request):
    key = request.headers.get("X-Law-Admin-Key", "")
    return bool(LAW_ADMIN_API_KEY and secrets.compare_digest(key, LAW_ADMIN_API_KEY))


@mcp.custom_route("/admin/refresh-jobs", methods=["POST", "GET"])
async def refresh_jobs(request):
    if not refresh_auth(request):
        return JSONResponse({"error_code": "UNAUTHORIZED"}, status_code=401)
    if REFRESH_MANAGER is None:
        return JSONResponse({"error_code": "REFRESH_UNAVAILABLE"}, status_code=503)
    if request.query_params or await request.body():
        return JSONResponse({"error_code": "VALIDATION_ERROR"}, status_code=400)
    if request.method == "GET":
        return JSONResponse({"item": REFRESH_MANAGER.latest()})
    from law_refresh import RefreshBusy
    try:
        return JSONResponse(REFRESH_MANAGER.start(), status_code=202)
    except RefreshBusy:
        return JSONResponse({"error_code": "REFRESH_BUSY"}, status_code=409)
    except OSError:
        return JSONResponse({"error_code": "REFRESH_UNAVAILABLE"}, status_code=503)


@mcp.custom_route("/admin/refresh-jobs/{job_id}", methods=["GET"])
async def refresh_job(request):
    if not refresh_auth(request):
        return JSONResponse({"error_code": "UNAUTHORIZED"}, status_code=401)
    if REFRESH_MANAGER is None:
        return JSONResponse({"error_code": "REFRESH_UNAVAILABLE"}, status_code=503)
    if request.query_params or await request.body():
        return JSONResponse({"error_code": "VALIDATION_ERROR"}, status_code=400)
    job = REFRESH_MANAGER.get(request.path_params["job_id"])
    return JSONResponse(job if job else {"error_code": "LAW_NOT_FOUND"}, status_code=200 if job else 404)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--http", type=int, metavar="PORT",
                        help="Streamable HTTP로 실행할 포트 (생략하면 stdio)")
    args = parser.parse_args()
    if args.http:
        mcp.run(transport="http", host="0.0.0.0", port=args.http)
    else:
        mcp.run()
