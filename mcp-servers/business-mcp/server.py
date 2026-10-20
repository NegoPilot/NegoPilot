"""협력사·완성차 기업 DB를 조회하는 MCP Server (business-mcp).

PostgreSQL(supplier_db)만 읽고, 실행 중에는 외부 API를 부르지 않는다.
순위 계산과 제외 판단은 모두 이 파일의 코드가 한다. LLM은 결과를 읽고 설명만 한다.

실행:
    uv run python server.py                 # stdio
    uv run python server.py --http 9102     # Streamable HTTP (http://localhost:9102/mcp)
"""

import argparse
import json
import os
import re
from datetime import date
from decimal import Decimal

import psycopg
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from psycopg.rows import dict_row

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://agent:agent@localhost:5432/supplier_db")
PAYMENT_DAYS_LIMIT = 60          # 하도급법 지급 기한
DEBT_RATIO_WARN = 2.0           # 부채비율 200% 초과 시 경고
GRADE_SCORE = {"A": 1.0, "B": 0.67, "C": 0.33, "D": 0.0}

PROCESSES = ["다이캐스팅", "프레스", "사출", "단조", "CNC가공"]   # schema.sql의 CHECK와 같다

# 사람이 부르는 이름 → 공정 (공백·대소문자 무시)
PROCESS_ALIASES = {
    "다이캐스팅": "다이캐스팅", "다이캐스트": "다이캐스팅", "diecasting": "다이캐스팅", "알루미늄주조": "다이캐스팅",
    "프레스": "프레스", "스탬핑": "프레스", "stamping": "프레스", "판금": "프레스", "press": "프레스",
    "사출": "사출", "사출성형": "사출", "플라스틱사출": "사출", "injection": "사출", "injectionmolding": "사출",
    "단조": "단조", "포징": "단조", "forging": "단조",
    "cnc가공": "CNC가공", "cnc": "CNC가공", "절삭가공": "CNC가공", "절삭": "CNC가공", "머시닝": "CNC가공",
    "machining": "CNC가공", "가공": "CNC가공",
}
# 소재 계열 이름 → 실제 소재 코드 (여러 개일 수 있음)
MATERIAL_ALIASES = {
    "알루미늄": ["ADC12", "A380", "AL5052", "AL6061"], "aluminum": ["ADC12", "A380", "AL5052", "AL6061"],
    "알루미늄합금": ["ADC12", "A380", "AL5052", "AL6061"],
    "강판": ["SPCC", "SPFH590"], "냉연강판": ["SPCC"], "열연강판": ["SPFH590"], "고장력강": ["SPFH590"],
    "탄소강": ["S45C"], "기계구조용탄소강": ["S45C"], "합금강": ["SCM440"], "크롬몰리브덴강": ["SCM440"],
    "나일론": ["PA66"], "폴리아미드": ["PA66"], "폴리프로필렌": ["PP"],
}
# 시·도 이름 → DB에 저장된 약칭
REGION_ALIASES = {
    "서울": "서울", "부산": "부산", "대구": "대구", "인천": "인천", "광주": "광주", "대전": "대전", "울산": "울산",
    "세종": "세종", "경기": "경기", "강원": "강원", "제주": "제주",
    "충북": "충북", "충청북": "충북", "충남": "충남", "충청남": "충남", "전북": "전북", "전라북": "전북",
    "전남": "전남", "전라남": "전남", "경북": "경북", "경상북": "경북", "경남": "경남", "경상남": "경남",
}
COMPANY_SUFFIX = r"\(주\)|주식회사|㈜|\s"

mcp = FastMCP("BusinessServer", instructions=(
    "2차 협력사와 완성차의 기업 정보를 PostgreSQL DB에서 조회한다. "
    "완성차 견적 요청(RFQ), 협력사의 공정·소재별 생산능력, 품질·납기 실적, 기존 계약 조건, "
    "과거 견적 단가, 연도별 재무(매출·영업이익·부채 등)를 다루고, RFQ에 맞는 협력사 순위를 코드로 계산한다. "
    "source가 'DART'인 기본 정보·재무는 공시 값이고 'MOCK'은 가상 값이다. "
    "쓸 수 있는 공정·소재·지역·RFQ 값은 business://catalog 에 있다. "
    "법 조문 내용과 해석은 다루지 않는다. notes에 하도급법·연동제 관련 내용이 나오면 law-mcp로 조문을 확인한다."
))


# ---------- DB ----------

def query(sql: str, params=()) -> list[dict]:
    try:
        with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
            rows = conn.execute(sql, params).fetchall()
    except psycopg.OperationalError as exc:
        raise ToolError("협력사 DB에 연결할 수 없다. PostgreSQL이 켜져 있는지, seed.py를 실행했는지 확인한다 "
                        f"(SETUP.md 참고). 원인: {str(exc).splitlines()[0]}") from exc
    return [{k: plain(v) for k, v in r.items()} for r in rows]


def plain(v):
    """JSON으로 보낼 수 있게 Decimal·date를 기본 타입으로 바꾼다."""
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, date):
        return v.isoformat()
    return v


# ---------- 입력 정규화 ----------

def squash(text: str) -> str:
    return re.sub(r"[\s·_\-]", "", text).lower()


def resolve_process(process: str) -> str:
    key = squash(process)
    if key in PROCESS_ALIASES:
        return PROCESS_ALIASES[key]
    hit = {v for k, v in PROCESS_ALIASES.items() if k in key}
    if len(hit) == 1:
        return hit.pop()
    raise ToolError(f"알 수 없는 공정 '{process}'. 사용 가능: {', '.join(PROCESSES)}")


def materials_by_process() -> dict[str, list[str]]:
    out = {p: [] for p in PROCESSES}
    for r in query("SELECT DISTINCT process, material FROM capabilities ORDER BY process, material"):
        out[r["process"]].append(r["material"])
    return out


def resolve_materials(material: str, process: str) -> list[str]:
    """소재 코드나 계열 이름(알루미늄, 강판 등)을 그 공정에서 쓰는 소재 코드 목록으로 바꾼다."""
    available = materials_by_process()[process]
    code = material.strip().upper().replace(" ", "")
    if code in available:
        return [code]
    matched = [m for m in MATERIAL_ALIASES.get(squash(material), []) if m in available]
    if matched:
        return matched
    raise ToolError(f"{process} 공정에 소재 '{material}'가 없다. 사용 가능: {', '.join(available) or '없음'}")


def resolve_region(region: str) -> str:
    key = re.sub(r"(특별자치시|특별자치도|특별시|광역시|도)$", "", region.strip().replace(" ", ""))
    for name, abbr in REGION_ALIASES.items():
        if key.startswith(name):
            return abbr
    raise ToolError(f"알 수 없는 지역 '{region}'. 시·도 이름(예: 경남, 경상남도, 울산광역시)으로 입력한다")


def resolve_supplier_id(supplier: str) -> str:
    """SUP-0001, 0001, 1 같은 ID나 회사명(일부)을 supplier_id 하나로 바꾼다."""
    text = supplier.strip()
    m = re.fullmatch(r"(?:SUP-?)?(\d{1,4})", text, flags=re.IGNORECASE)
    if m:
        sid = f"SUP-{int(m.group(1)):04d}"
        if query("SELECT 1 FROM suppliers WHERE supplier_id = %s", (sid,)):
            return sid
        raise ToolError(f"{sid} 협력사가 없다. search_suppliers나 회사명으로 찾는다")
    name = re.sub(COMPANY_SUFFIX, "", text)
    if not name:
        raise ToolError("supplier 가 비어 있다")
    rows = query("SELECT supplier_id, name FROM suppliers "
                 "WHERE regexp_replace(name, %s, '', 'g') ILIKE %s ORDER BY supplier_id",
                 (COMPANY_SUFFIX, f"%{name}%"))
    exact = [r for r in rows if re.sub(COMPANY_SUFFIX, "", r["name"]) == name]
    if len(exact) == 1 or len(rows) == 1:
        return (exact or rows)[0]["supplier_id"]
    if not rows:
        raise ToolError(f"'{supplier}'에 해당하는 협력사가 없다. supplier_id(SUP-0001)나 회사명 일부로 입력한다")
    names = ", ".join(f"{r['supplier_id']} {r['name']}" for r in rows[:10])
    raise ToolError(f"'{supplier}'에 해당하는 협력사가 {len(rows)}곳이다. 하나를 골라 supplier_id로 다시 호출한다: {names}")


def get_rfq(rfq_id: str) -> dict:
    rfq = query("SELECT * FROM rfqs WHERE rfq_id = %s", (rfq_id.strip().upper(),))
    if not rfq:
        ids = [r["rfq_id"] for r in query("SELECT rfq_id FROM rfqs ORDER BY rfq_id")]
        raise ToolError(f"RFQ '{rfq_id}'가 없다. 사용 가능: {', '.join(ids)}")
    return rfq[0]


def minmax(value, values, higher_is_better=True):
    lo, hi = min(values), max(values)
    if hi == lo:
        return 1.0
    score = (value - lo) / (hi - lo)
    return score if higher_is_better else 1 - score


# ---------- Tools ----------

@mcp.tool(annotations={"readOnlyHint": True})
def list_rfqs() -> list[dict]:
    """완성차가 낸 신차 부품 견적 요청(RFQ) 목록을 마감일 순으로 반환한다.

    RFQ마다 차종, 부품, 공정, 소재, 연간 물량, 목표 단가(원), 필수 인증, 마감일이 있다.
    '어떤 견적 요청이 들어와 있나', '이번 달 마감 RFQ' 같은 질문에 쓴다. 협력사 순위는 rank_suppliers로 구한다.
    """
    return query("""
        SELECT r.*, o.name AS oem_name
        FROM rfqs r JOIN oems o USING (oem_id)
        ORDER BY due_date
    """)


@mcp.tool(annotations={"readOnlyHint": True})
def search_suppliers(process: str, material: str | None = None, region: str | None = None,
                     iatf_required: bool = False, min_monthly_capacity: int | None = None,
                     limit: int = 20) -> list[dict]:
    """공정·소재·지역·인증·월 생산능력 조건으로 2차 협력사 후보를 찾아 불량률이 낮은 순으로 반환한다.

    process: 다이캐스팅, 프레스, 사출, 단조, CNC가공 중 하나. '스탬핑', '사출성형', 'CNC' 같은 별칭도 된다
    material: 소재 코드(ADC12, SPFH590, PA66 등)나 계열 이름(알루미늄, 강판, 나일론). 생략하면 공정만으로 찾는다
    region: 시·도 (경남, 경상남도, 울산광역시 등). 생략하면 전국
    iatf_required: True면 IATF 16949 인증이 있는 곳만
    min_monthly_capacity: 월 생산능력 하한(개). 연간 물량이면 12로 나눠서 넣는다
    limit: 최대 개수 (1~100)
    특정 RFQ에 맞는 순위가 필요하면 rank_suppliers를 쓴다.
    """
    proc = resolve_process(process)
    sql = """
        SELECT o.supplier_id, o.source, o.name, o.region, o.employees, o.revenue_eok, o.iatf16949,
               o.operating_margin, o.debt_ratio,
               c.process, c.material, c.monthly_capacity,
               o.defect_ppm, o.on_time_rate, o.audit_grade, o.years_dealing
        FROM capabilities c JOIN supplier_overview o USING (supplier_id)
        WHERE c.process = %s
    """
    params: list = [proc]
    if material:
        sql += " AND c.material = ANY(%s)"
        params.append(resolve_materials(material, proc))
    if region:
        sql += " AND o.region = %s"
        params.append(resolve_region(region))
    if iatf_required:
        sql += " AND o.iatf16949"
    if min_monthly_capacity:
        sql += " AND c.monthly_capacity >= %s"
        params.append(min_monthly_capacity)
    sql += " ORDER BY o.defect_ppm, o.supplier_id LIMIT %s"
    params.append(max(1, min(limit, 100)))
    return query(sql, params)


@mcp.tool(annotations={"readOnlyHint": True})
def get_supplier(supplier: str) -> dict:
    """협력사 한 곳의 기본 정보, 품질·납기, 기존 계약 조건, 생산 능력, 연도별 재무, 최근 견적을 반환한다.

    supplier: supplier_id(SUP-0001, 1) 또는 회사명(서연이화, (주)서연이화, 이름 일부도 된다).
              이름이 여러 곳에 맞으면 후보 목록을 오류로 알려 주니, 하나를 골라 supplier_id로 다시 부른다
    source가 'DART'면 기본 정보와 재무는 공시 값이고, 'MOCK'이면 가상 값이다. 금액 단위는 원, revenue_eok는 억 원.
    """
    sid = resolve_supplier_id(supplier)
    overview = query("SELECT * FROM supplier_overview WHERE supplier_id = %s", (sid,))[0]
    return {
        **overview,
        "capabilities": query("SELECT process, material, monthly_capacity FROM capabilities "
                              "WHERE supplier_id = %s ORDER BY process, material", (sid,)),
        "financials": query("SELECT bsns_year, fs_div, source, revenue, operating_income, net_income, "
                            "total_assets, total_liabilities, total_equity FROM financials "
                            "WHERE supplier_id = %s ORDER BY bsns_year", (sid,)),
        "recent_quotes": query("SELECT process, material, unit_price, quoted_at FROM quotes "
                               "WHERE supplier_id = %s ORDER BY quoted_at DESC LIMIT 5", (sid,)),
    }


@mcp.tool(annotations={"readOnlyHint": True})
def rank_suppliers(rfq_id: str, w_price: float = 0.5, w_quality: float = 0.3,
                   w_delivery: float = 0.2, top_n: int = 10) -> dict:
    """RFQ 하나에 대해 견적을 요청할 2차 협력사 순위를 코드로 계산한다.

    rfq_id: RFQ-2026-001 처럼 입력 (목록은 list_rfqs 또는 business://catalog)
    w_price, w_quality, w_delivery: 가격·품질·납기 가중치 (기본 0.5 / 0.3 / 0.2)
    top_n: ranked에 담을 최대 개수
    점수 = 가격(최근 견적 단가) × w_price + 품질(불량률·감사 등급) × w_quality + 납기 준수율 × w_delivery
    인증이 없거나 생산 능력이 모자란 곳은 순위에서 빼고 excluded에 제외 사유를 담는다.
    notes의 하도급법 지급 기한·연동 약정 내용은 law-mcp로 조문을 확인해 인용한다.
    """
    rfq = get_rfq(rfq_id)
    needed_monthly = -(-rfq["annual_volume"] // 12)   # 올림

    rows = query("""
        SELECT o.supplier_id, o.source, o.name, o.region, o.iatf16949, c.monthly_capacity,
               o.defect_ppm, o.on_time_rate, o.audit_grade, o.years_dealing,
               o.payment_days, o.price_linkage, o.fin_year, o.operating_margin, o.debt_ratio,
               (SELECT unit_price FROM quotes q
                 WHERE q.supplier_id = o.supplier_id AND q.process = c.process AND q.material = c.material
                 ORDER BY quoted_at DESC LIMIT 1) AS last_price
        FROM capabilities c JOIN supplier_overview o USING (supplier_id)
        WHERE c.process = %s AND c.material = %s
    """, (rfq["process"], rfq["material"]))

    candidates, excluded = [], []
    for r in rows:
        if rfq["required_cert"] == "IATF16949" and not r["iatf16949"]:
            excluded.append({"supplier_id": r["supplier_id"], "name": r["name"], "reason": "IATF 16949 인증 없음"})
        elif r["monthly_capacity"] < needed_monthly:
            excluded.append({"supplier_id": r["supplier_id"], "name": r["name"],
                             "reason": f"월 생산능력 {r['monthly_capacity']:,}개 < 필요 {needed_monthly:,}개"})
        else:
            candidates.append(r)

    if candidates:
        prices = [c["last_price"] for c in candidates]
        ppms = [c["defect_ppm"] for c in candidates]
        rates = [c["on_time_rate"] for c in candidates]
        for c in candidates:
            price_s = minmax(c["last_price"], prices, higher_is_better=False)
            quality_s = 0.7 * minmax(c["defect_ppm"], ppms, higher_is_better=False) + 0.3 * GRADE_SCORE[c["audit_grade"]]
            delivery_s = minmax(c["on_time_rate"], rates)
            c["scores"] = {"price": round(price_s, 3), "quality": round(quality_s, 3), "delivery": round(delivery_s, 3)}
            c["total_score"] = round(w_price * price_s + w_quality * quality_s + w_delivery * delivery_s, 3)
            c["gap_to_target_pct"] = round((c["last_price"] / rfq["target_price"] - 1) * 100, 1)

            notes = []
            if c["years_dealing"] == 0:
                notes.append("신규 후보(거래 이력 없음)")
            if c["payment_days"] and c["payment_days"] > PAYMENT_DAYS_LIMIT:
                notes.append(f"기존 계약 결제 {c['payment_days']}일: 하도급법 지급 기한 {PAYMENT_DAYS_LIMIT}일 초과, 점검 필요")
            if c["operating_margin"] is not None and c["operating_margin"] < 0:
                notes.append(f"{c['fin_year']}년 영업적자: 공급 안정성 확인 필요")
            if c["debt_ratio"] is not None and c["debt_ratio"] > DEBT_RATIO_WARN:
                notes.append(f"부채비율 {c['debt_ratio'] * 100:.0f}%: 재무 안정성 확인 필요")
            if c["price_linkage"]:
                notes.append("납품대금 연동 약정 있음: 원재료 변동분은 연동 공식으로 반영")
            c["notes"] = notes
        candidates.sort(key=lambda c: c["total_score"], reverse=True)

    return {
        "rfq": rfq,
        "needed_monthly_capacity": needed_monthly,
        "weights": {"price": w_price, "quality": w_quality, "delivery": w_delivery},
        "ranked": candidates[:top_n],
        "candidate_count": len(candidates),
        "excluded": excluded,
    }


# ---------- Resources ----------

@mcp.resource("business://catalog", mime_type="application/json")
def catalog() -> str:
    """쓸 수 있는 공정·소재(공정별), 별칭, 지역, RFQ 목록과 협력사 수. 쿼리를 정확한 값으로 바꿀 때 쓴다."""
    regions = query("SELECT region, count(*) AS suppliers FROM suppliers GROUP BY region ORDER BY count(*) DESC, region")
    counts = query("SELECT source, count(*) AS suppliers FROM suppliers GROUP BY source ORDER BY source")
    rfqs = query("SELECT rfq_id, part_name, process, material, required_cert, due_date FROM rfqs ORDER BY rfq_id")
    return json.dumps({"processes": materials_by_process(),
                       "process_aliases": PROCESS_ALIASES, "material_aliases": MATERIAL_ALIASES,
                       "regions": regions, "suppliers_by_source": counts, "rfqs": rfqs},
                      ensure_ascii=False, indent=1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--http", type=int, metavar="PORT",
                        help="Streamable HTTP로 실행할 포트 (생략하면 stdio)")
    args = parser.parse_args()
    if args.http:
        mcp.run(transport="http", host="0.0.0.0", port=args.http)
    else:
        mcp.run()
