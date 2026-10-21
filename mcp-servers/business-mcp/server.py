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
DEBT_RATIO_WARN = 2.0           # 부채비율 200% 초과 시 경고
GRADE_SCORE = {"A": 1.0, "B": 0.67, "C": 0.33, "D": 0.0}

# 협상안 검사 기준. 근거 조문은 law-mcp(law_key, 조문 번호)로 확인·인용한다.
PAYMENT_DAYS_LIMIT = 60          # 하도급법 제13조①: 수령일부터 60일 이내 지급
MAJOR_MATERIAL_RATIO = 0.10      # 하도급법 제2조⑯: 비용이 하도급대금의 10% 이상이면 주요 원재료
LINKAGE_EXEMPT_DAYS = 90         # 하도급법 제3조④2: 거래 기간 90일 이내 (법률 상한, 시행령 값 확인)
LINKAGE_EXEMPT_AMOUNT = 100_000_000   # 하도급법 제3조④3: 하도급대금 1억 원 이하 (법률 상한, 시행령 값 확인)
MARKET_PRICE_FLOOR = 0.90        # 시장 중앙값의 90% 미만이면 제4조(부당한 하도급대금 결정) 위험으로 본다 (내부 기준)
LAST_QUOTE_CUT = 0.05            # 직전 견적보다 5% 넘게 낮으면 인하 근거 확인 (내부 기준)
SME_REVENUE_EOK = 1000           # 중소기업기본법 시행령 별표1 자동차 제조업 평균매출 기준(억 원). law-mcp 색인에 별표가 없어 확인 필요

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
    "source가 'DART'인 기본 정보·재무는 공시 값이고 'MOCK'은 가상 값이다. 'MANUAL'은 관리자 입력이다. "
    "인증과 생산능력 및 품질의 출처는 각각 certification_source와 capability_source 및 quality_source로 확인한다. "
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
    for r in query("SELECT DISTINCT c.process, c.material FROM capabilities c JOIN suppliers s USING (supplier_id) "
                   "WHERE s.status = 'ACTIVE' ORDER BY c.process, c.material"):
        out[r["process"]].append(r["material"])
    return out


def resolve_materials(material: str, process: str | None) -> list[str]:
    """소재 코드나 계열 이름을 실제 지원 코드로 바꾼다. 공정이 없으면 전체 공정을 확인한다."""
    by_process = materials_by_process()
    available = (by_process[process] if process is not None
                 else sorted({code for codes in by_process.values() for code in codes}))
    code = material.strip().upper().replace(" ", "")
    exact = [stored for stored in available if stored.upper().replace(" ", "") == code]
    if exact:
        return exact
    matched = [m for m in MATERIAL_ALIASES.get(squash(material), []) if m in available]
    if matched:
        return matched
    scope = f"{process} 공정" if process is not None else "전체 공정"
    raise ToolError(f"{scope}에 소재 '{material}'가 없다. 사용 가능: {', '.join(available) or '없음'}")


def resolve_region(region: str) -> str:
    key = re.sub(r"(특별자치시|특별자치도|특별시|광역시|도)$", "", region.strip().replace(" ", ""))
    for name, abbr in REGION_ALIASES.items():
        if key.startswith(name):
            return abbr
    raise ToolError(f"알 수 없는 지역 '{region}'. 시·도 이름(예: 경남, 경상남도, 울산광역시)으로 입력한다")


def resolve_supplier_id(supplier: str) -> str:
    """SUP-0001, 0001, 1 같은 ID나 회사명(일부)을 supplier_id 하나로 바꾼다."""
    text = supplier.strip()
    m = re.fullmatch(r"(?:SUP-?)?(\d{1,19})", text, flags=re.IGNORECASE)
    if m:
        sid = f"SUP-{int(m.group(1)):04d}"
        if query("SELECT 1 FROM suppliers WHERE supplier_id = %s AND status = 'ACTIVE'", (sid,)):
            return sid
        raise ToolError(f"{sid} 협력사가 없다. search_suppliers나 회사명으로 찾는다")
    name = re.sub(COMPANY_SUFFIX, "", text)
    if not name:
        raise ToolError("supplier 가 비어 있다")
    literal_name = name.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    rows = query("SELECT supplier_id, name FROM suppliers "
                 "WHERE status = 'ACTIVE' AND regexp_replace(name, %s, '', 'g') ILIKE %s ESCAPE '!' ORDER BY supplier_id",
                 (COMPANY_SUFFIX, f"%{literal_name}%"))
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


def finding(code: str, severity: str, message: str, *refs: tuple[str, str]) -> dict:
    """검사 결과 한 건. law_refs는 law-mcp get_article(law, article_no)로 그대로 넘길 수 있다."""
    return {"code": code, "severity": severity, "message": message,
            "law_refs": [{"law": law, "article_no": no} for law, no in refs]}


def linkage_findings(raw_material_ratio: float | None, price_linkage: bool, contract_amount: int | None,
                     contract_days: int | None, waived: bool = False, buyer_is_small_business: bool = False) -> list[dict]:
    """주요 원재료 연동 약정(하도급법 제3조②3·④, 제2조⑯) 검사."""
    if price_linkage:
        return []
    if raw_material_ratio is None:
        return [finding("RAW_MATERIAL_RATIO_UNKNOWN", "warning",
                        "원재료 비중을 몰라 연동 약정 대상인지 판단할 수 없다. 원재료 비용이 하도급대금의 10% 이상이면 "
                        "연동 사항을 서면에 적어야 한다", ("subcontract_act", "제2조"), ("subcontract_act", "제3조"))]
    if raw_material_ratio < MAJOR_MATERIAL_RATIO:
        return []
    pct = f"{raw_material_ratio * 100:.0f}%"
    exemptions = []
    if buyer_is_small_business:
        exemptions.append("원사업자가 소기업")
    if contract_days is not None and contract_days <= LINKAGE_EXEMPT_DAYS:
        exemptions.append(f"거래 기간 {contract_days}일(90일 이내)")
    if contract_amount is not None and contract_amount <= LINKAGE_EXEMPT_AMOUNT:
        exemptions.append(f"하도급대금 {contract_amount:,}원(1억 원 이하)")
    if exemptions:
        return [finding("LINKAGE_EXEMPT", "info",
                        f"원재료 비중 {pct}로 연동 대상이지만 예외에 해당할 수 있다: {', '.join(exemptions)}. "
                        "기간·금액 기준은 시행령 값을 확인한다", ("subcontract_act", "제3조"))]
    if waived:
        return [finding("LINKAGE_WAIVED_NEEDS_WRITTEN_REASON", "warning",
                        f"원재료 비중 {pct}로 연동 대상인데 연동하지 않기로 합의했다. 그 취지와 사유를 서면에 분명하게 "
                        "적어야 하고, 거래상 지위를 남용해 합의를 유도하면 안 된다", ("subcontract_act", "제3조"))]
    return [finding("LINKAGE_TERMS_MISSING", "violation",
                    f"원재료 비중 {pct}로 주요 원재료(10% 이상)에 해당하는데 연동 약정이 없다. 연동 대상 원재료, 조정요건, "
                    "기준 지표, 산식 등을 서면에 적어야 한다", ("subcontract_act", "제2조"), ("subcontract_act", "제3조"))]


def payment_findings(payment_days: int) -> list[dict]:
    if payment_days > PAYMENT_DAYS_LIMIT:
        return [finding("PAYMENT_DAYS_OVER_60", "violation",
                        f"지급 기한 {payment_days}일: 목적물 수령일부터 {PAYMENT_DAYS_LIMIT}일 이내로 정해야 한다 "
                        "(대등한 지위에서 정했거나 업종 특수성이 인정되는 예외 제외). 초과 기간에는 지연이자가 붙는다",
                        ("subcontract_act", "제13조"))]
    return []


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
def search_suppliers(process: str | None = None, material: str | None = None, region: str | None = None,
                     iatf_required: bool = False, min_monthly_capacity: int | None = None,
                     limit: int = 20, distinct_suppliers: bool = False) -> list[dict]:
    """공정·소재·지역·인증·월 생산능력 조건으로 2차 협력사 후보를 찾아 불량률이 낮은 순으로 반환한다.

    process: 다이캐스팅, 프레스, 사출, 단조, CNC가공 중 하나. 별칭도 된다. 생략하면 모든 공정에서 찾는다
    material: 소재 코드(ADC12, SPFH590, PA66 등)나 계열 이름(알루미늄, 강판, 나일론).
              공정 없이 지정하면 모든 공정의 실제 지원 코드에서 찾는다
    region: 시·도 (경남, 경상남도, 울산광역시 등). 생략하면 전국
    iatf_required: True면 IATF 16949 인증이 있는 곳만
    min_monthly_capacity: 월 생산능력 하한(개). 연간 물량이면 12로 나눠서 넣는다
    limit: 반환 행의 최대 개수 (1~100). 기본 모드에서는 한 기업이 여러 행으로 나올 수 있다
    distinct_suppliers: True면 조건에 맞는 기업마다 대표 공정과 소재 행 하나를 반환한다.
                        이때 limit은 기업 수다. 대표 행의 생산능력은 해당 공정과 소재의 값이며
                        기업 전체 생산능력이나 모든 지원 공정을 뜻하지 않는다. 전체 정보는 get_supplier로 조회한다
    특정 RFQ에 맞는 순위가 필요하면 rank_suppliers를 쓴다.
    """
    proc = resolve_process(process) if process is not None else None
    distinct = "DISTINCT ON (o.supplier_id) " if distinct_suppliers else ""
    sql = f"""
        SELECT {distinct}o.supplier_id, o.source, o.name, o.region, o.employees, o.revenue_eok, o.iatf16949,
               o.operating_margin, o.debt_ratio,
               c.process, c.material, c.monthly_capacity,
               o.defect_ppm, o.on_time_rate, o.audit_grade, o.years_dealing,
               o.certification_source, o.capability_source, o.quality_source
        FROM capabilities c JOIN supplier_overview o USING (supplier_id)
    """
    conditions: list[str] = ["o.status = 'ACTIVE'"]
    params: list = []
    if proc is not None:
        conditions.append("c.process = %s")
        params.append(proc)
    if material:
        conditions.append("c.material = ANY(%s)")
        params.append(resolve_materials(material, proc))
    if region:
        conditions.append("o.region = %s")
        params.append(resolve_region(region))
    if iatf_required:
        conditions.append("o.iatf16949")
    if min_monthly_capacity:
        conditions.append("c.monthly_capacity >= %s")
        params.append(min_monthly_capacity)
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    if distinct_suppliers:
        # 모든 조건에 맞는 행에서 대표를 고른 뒤 기업을 정렬하고 제한한다.
        # 먼저 행 수를 제한하면 한 기업의 여러 공정이 다른 기업을 밀어낼 수 있다.
        sql += " ORDER BY o.supplier_id, c.process, c.material"
        sql = f"SELECT * FROM ({sql}) matched_suppliers ORDER BY defect_ppm, supplier_id LIMIT %s"
    else:
        sql += " ORDER BY o.defect_ppm, o.supplier_id, c.process, c.material LIMIT %s"
    params.append(max(1, min(limit, 100)))
    return query(sql, params)


@mcp.tool(annotations={"readOnlyHint": True})
def find_suppliers(supplier: str, limit: int = 10) -> dict:
    """이름이나 느슨한 ID로 협력사 후보를 찾는다. 상세 조회 전에 대상을 선택할 때 쓴다.

    supplier: SUP-0001, 0001, 1 또는 회사명. 회사 표기와 공백은 제거하고 대소문자를 구분하지 않는다.
              정확한 회사명 일치가 있으면 그 후보만 반환한다. 없으면 이름 일부로 찾는다.
              이름의 %, _와 !는 검색 명령이 아닌 문자 그대로 해석한다.
    limit: matches에 담을 최대 기업 수 (1~100). 기본 10.
    결과: {matches: [{supplier_id, name, region}], total, match_type: id|exact|partial}.
          total은 limit 전의 후보 수다. 0건은 빈 matches로 반환하며 DB 오류는 예외로 전달한다.
    """
    text = supplier.strip()
    if not text:
        raise ToolError("supplier 가 비어 있다")
    bounded_limit = max(1, min(limit, 100))
    identifier = re.fullmatch(r"(?:SUP-?)?(\d{1,19})", text, flags=re.IGNORECASE)
    if identifier:
        sid = f"SUP-{int(identifier.group(1)):04d}"
        rows = query("SELECT supplier_id, name, region, count(*) OVER () AS total FROM suppliers "
                     "WHERE supplier_id = %s AND status = 'ACTIVE' ORDER BY supplier_id LIMIT %s", (sid, bounded_limit))
        match_type = "id"
    else:
        name = re.sub(COMPANY_SUFFIX, "", text)
        if not name:
            raise ToolError("supplier 가 비어 있다")
        literal_name = name.replace("!", "!!").replace("%", "!%").replace("_", "!_")
        sql = ("SELECT supplier_id, name, region, count(*) OVER () AS total FROM suppliers "
               "WHERE status = 'ACTIVE' AND regexp_replace(name, %s, '', 'g') ILIKE %s ESCAPE '!' "
               "ORDER BY supplier_id LIMIT %s")
        rows = query(sql, (COMPANY_SUFFIX, literal_name, bounded_limit))
        match_type = "exact"
        if not rows:
            rows = query(sql, (COMPANY_SUFFIX, f"%{literal_name}%", bounded_limit))
            match_type = "partial"
    return {
        "matches": [{key: row[key] for key in ("supplier_id", "name", "region")} for row in rows],
        "total": int(rows[0]["total"]) if rows else 0,
        "match_type": match_type,
    }


@mcp.tool(annotations={"readOnlyHint": True})
def get_supplier(supplier: str) -> dict:
    """협력사 한 곳의 기본 정보, 품질·납기, 기존 계약 조건, 생산 능력, 연도별 재무, 최근 견적을 반환한다.

    supplier: supplier_id(SUP-0001, 1) 또는 회사명(서연이화, (주)서연이화, 이름 일부도 된다).
              이름이 여러 곳에 맞으면 후보 목록을 오류로 알려 주니, 하나를 골라 supplier_id로 다시 부른다
    source는 기본 정보의 출처다. DART는 공시 값이고 MOCK은 가상 값이며 MANUAL은 관리자 입력이다.
    재무 출처는 financials의 각 source를 확인한다. 운영 자료의 출처는 각각 *_source로 반환한다.
    금액 단위는 원, revenue_eok는 억 원.
    """
    sid = resolve_supplier_id(supplier)
    records = query("SELECT * FROM supplier_overview WHERE supplier_id = %s AND status = 'ACTIVE'", (sid,))
    if not records:
        raise ToolError("활성 협력사가 없다")
    overview = records[0]
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
    notes는 사람이 읽는 요약이고, flags는 기존 계약 조건의 법규 점검 결과다
    ({code, severity, message, law_refs}). law_refs를 law-mcp get_article로 넘겨 조문을 인용한다.
    새 협상안을 검사하려면 check_offer를 쓴다.
    """
    rfq = get_rfq(rfq_id)
    needed_monthly = -(-rfq["annual_volume"] // 12)   # 올림

    rows = query("""
        SELECT o.supplier_id, o.source, o.name, o.region, o.iatf16949, c.monthly_capacity,
               o.defect_ppm, o.on_time_rate, o.audit_grade, o.years_dealing,
               o.payment_days, o.price_linkage, o.raw_material_ratio, o.fin_year, o.operating_margin, o.debt_ratio,
               o.certification_source, o.capability_source, o.quality_source,
               (SELECT unit_price FROM quotes q
                 WHERE q.supplier_id = o.supplier_id AND q.process = c.process AND q.material = c.material
                 ORDER BY quoted_at DESC LIMIT 1) AS last_price
        FROM capabilities c JOIN supplier_overview o USING (supplier_id)
        WHERE c.process = %s AND c.material = %s AND o.status = 'ACTIVE'
    """, (rfq["process"], rfq["material"]))

    candidates, excluded = [], []
    for r in rows:
        if any(r[key] is None for key in ("defect_ppm", "on_time_rate", "audit_grade", "years_dealing", "last_price")):
            excluded.append({"supplier_id": r["supplier_id"], "name": r["name"], "reason": "품질 또는 견적 자료 미등록"})
        elif rfq["required_cert"] == "IATF16949" and not r["iatf16949"]:
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
            # 기존 계약 조건의 법규 점검 결과. law_refs를 law-mcp get_article로 넘겨 조문을 인용한다
            c["flags"] = (payment_findings(c["payment_days"]) if c["payment_days"] else []) + (
                linkage_findings(c["raw_material_ratio"], c["price_linkage"], None, None) if c["years_dealing"] else [])
        candidates.sort(key=lambda c: c["total_score"], reverse=True)

    return {
        "rfq": rfq,
        "needed_monthly_capacity": needed_monthly,
        "weights": {"price": w_price, "quality": w_quality, "delivery": w_delivery},
        "ranked": candidates[:top_n],
        "candidate_count": len(candidates),
        "excluded": excluded,
    }


@mcp.tool(annotations={"readOnlyHint": True})
def check_offer(supplier: str, rfq_id: str, unit_price: int, payment_days: int, price_linkage: bool,
                raw_material_ratio: float | None = None, contract_days: int = 365,
                linkage_waived_by_agreement: bool = False, buyer_is_small_business: bool = False) -> dict:
    """협상안(단가·지급 기한·연동 약정)이 하도급법에 어긋나는지 코드 규칙으로 검사한다.

    supplier: 협상 상대 협력사 (SUP-0001, 회사명 등. get_supplier와 같은 입력)
    rfq_id: 협상안이 대응하는 RFQ (RFQ-2026-001 등)
    unit_price: 제시할 개당 단가(원)
    payment_days: 목적물 수령일부터 대금 지급까지 일수
    price_linkage: 납품대금 연동 약정을 넣는지
    raw_material_ratio: 단가 중 원재료 비용 비중(0~1). 생략하면 그 협력사와의 기존 계약 값을 쓴다
    contract_days: 거래 기간(일). 하도급대금은 RFQ 연간 물량 × 단가 × 기간/365로 계산한다
    linkage_waived_by_agreement: 연동하지 않기로 협력사와 합의했는지
    buyer_is_small_business: 우리(원사업자)가 중소기업기본법상 소기업인지

    결과의 각 항목은 {code, severity(violation/warning/info), message, law_refs}이다.
    violation은 법 위반 소지, warning은 확인이 필요한 위험, info는 참고다.
    law_refs의 {law, article_no}를 law-mcp get_article로 넘겨 조문 원문을 인용한다.
    판정은 이 도구가 하고, 법 해석이나 예외 해당 여부의 최종 판단은 법무 검토로 확인한다.
    """
    if unit_price <= 0 or payment_days <= 0 or contract_days <= 0:
        raise ToolError("unit_price, payment_days, contract_days 는 0보다 커야 한다")
    if raw_material_ratio is not None and not 0 <= raw_material_ratio <= 1:
        raise ToolError("raw_material_ratio 는 0~1 사이 비율로 입력한다 (예: 40% → 0.4)")
    sid = resolve_supplier_id(supplier)
    rfq = get_rfq(rfq_id)
    records = query("SELECT * FROM supplier_overview WHERE supplier_id = %s AND status = 'ACTIVE'", (sid,))
    if not records:
        raise ToolError("활성 협력사가 없다")
    s = records[0]
    cap = query("SELECT monthly_capacity FROM capabilities WHERE supplier_id = %s AND process = %s AND material = %s",
                (sid, rfq["process"], rfq["material"]))
    last = query("SELECT unit_price FROM quotes WHERE supplier_id = %s AND process = %s AND material = %s "
                 "ORDER BY quoted_at DESC LIMIT 1", (sid, rfq["process"], rfq["material"]))
    market = query("""
        SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY unit_price) AS median, count(*) AS n
        FROM (SELECT DISTINCT ON (q.supplier_id) q.unit_price FROM quotes q
              JOIN suppliers s USING (supplier_id)
              WHERE q.process = %s AND q.material = %s AND s.status = 'ACTIVE'
              ORDER BY q.supplier_id, q.quoted_at DESC, q.quote_id DESC) latest
    """, (rfq["process"], rfq["material"]))[0]

    ratio = raw_material_ratio if raw_material_ratio is not None else s["raw_material_ratio"]
    contract_amount = round(rfq["annual_volume"] * unit_price * contract_days / 365)
    needed_monthly = -(-rfq["annual_volume"] // 12)
    findings = payment_findings(payment_days)
    findings += linkage_findings(ratio, price_linkage, contract_amount, contract_days,
                                 linkage_waived_by_agreement, buyer_is_small_business)

    # 단가: 일반적으로 지급되는 대가보다 낮은지 (제4조①, ②5 일방적으로 낮은 단가, ②1 일률적 인하)
    if market["median"] and unit_price < market["median"] * MARKET_PRICE_FLOOR:
        findings.append(finding(
            "PRICE_BELOW_MARKET", "warning",
            f"단가 {unit_price:,}원이 같은 공정·소재 최근 견적 중앙값 {market['median']:,.0f}원({market['n']}곳)의 "
            f"{unit_price / market['median'] * 100:.0f}%다. 일반적으로 지급되는 대가보다 낮게 정하면 부당한 하도급대금 "
            "결정이 될 수 있으니 원가 근거와 협의 기록을 남긴다", ("subcontract_act", "제4조")))
    if last and unit_price < last[0]["unit_price"] * (1 - LAST_QUOTE_CUT):
        findings.append(finding(
            "PRICE_CUT_FROM_LAST_QUOTE", "warning",
            f"이 협력사의 직전 견적 {last[0]['unit_price']:,}원보다 {(1 - unit_price / last[0]['unit_price']) * 100:.1f}% 낮다. "
            "정당한 사유 없는 일률적 인하나 일방적으로 낮은 단가 결정이 되지 않도록 인하 근거를 확인한다",
            ("subcontract_act", "제4조")))
    if unit_price > rfq["target_price"]:
        findings.append(finding("ABOVE_TARGET_PRICE", "info",
                                f"단가가 RFQ 목표 단가 {rfq['target_price']:,}원보다 "
                                f"{(unit_price / rfq['target_price'] - 1) * 100:.1f}% 높다"))

    # 하도급법 적용 대상(수급사업자 = 중소기업자)인지
    if s["revenue_eok"] is None:
        findings.append(finding("SME_STATUS_UNKNOWN", "info",
                                "매출 정보가 없어 수급사업자(중소기업자) 해당 여부를 판단할 수 없다",
                                ("subcontract_act", "제2조")))
    elif s["revenue_eok"] <= SME_REVENUE_EOK:
        findings.append(finding("SUBCONTRACT_ACT_APPLIES", "info",
                                f"최근 매출 {s['revenue_eok']:,}억 원으로 중소기업일 가능성이 높아 하도급법상 수급사업자로 "
                                "보고 검사했다 (중소기업 기준은 중소기업기본법 시행령 별표1로 확인)",
                                ("subcontract_act", "제2조")))
    else:
        findings.append(finding("SME_STATUS_CHECK", "info",
                                f"최근 매출 {s['revenue_eok']:,}억 원으로 중소기업이 아닐 수 있다. 수급사업자 해당 여부를 "
                                "확인하고, 중견기업이면 지급 조항 확대 적용 대상인지 본다",
                                ("subcontract_act", "제2조"), ("subcontract_act", "제13조")))

    # RFQ 요건 (법규가 아닌 거래 조건)
    if rfq["required_cert"] == "IATF16949" and not s["iatf16949"]:
        findings.append(finding("CERT_MISSING", "warning", "RFQ 필수 인증 IATF 16949가 없다"))
    if not cap:
        findings.append(finding("CAPABILITY_MISSING", "warning",
                                f"{rfq['process']}·{rfq['material']} 생산 능력이 등록되어 있지 않다"))
    elif cap[0]["monthly_capacity"] < needed_monthly:
        findings.append(finding("CAPACITY_SHORT", "warning",
                                f"월 생산능력 {cap[0]['monthly_capacity']:,}개 < 필요 {needed_monthly:,}개"))

    by = {k: [f for f in findings if f["severity"] == k] for k in ("violation", "warning", "info")}
    refs = list(dict.fromkeys((r["law"], r["article_no"]) for f in findings for r in f["law_refs"]))
    return {
        "verdict": "위반 소지 있음" if by["violation"] else "확인 필요" if by["warning"] else "문제 없음",
        "supplier": {"supplier_id": sid, "name": s["name"], "source": s["source"], "revenue_eok": s["revenue_eok"],
                     "certification_source": s["certification_source"],
                     "capability_source": s["capability_source"], "quality_source": s["quality_source"]},
        "rfq": {k: rfq[k] for k in ("rfq_id", "part_name", "process", "material", "annual_volume", "target_price")},
        "offer": {"unit_price": unit_price, "payment_days": payment_days, "price_linkage": price_linkage,
                  "raw_material_ratio": ratio, "contract_days": contract_days, "contract_amount": contract_amount},
        "violations": by["violation"],
        "warnings": by["warning"],
        "info": by["info"],
        "law_refs": [{"law": law, "article_no": no} for law, no in refs],
    }


# ---------- Resources ----------

@mcp.resource("business://catalog", mime_type="application/json")
def catalog() -> str:
    """쓸 수 있는 공정·소재(공정별), 별칭, 지역, RFQ 목록과 협력사 수. 쿼리를 정확한 값으로 바꿀 때 쓴다."""
    regions = query("SELECT region, count(*) AS suppliers FROM suppliers WHERE status = 'ACTIVE' GROUP BY region ORDER BY count(*) DESC, region")
    counts = query("SELECT source, count(*) AS suppliers FROM suppliers WHERE status = 'ACTIVE' GROUP BY source ORDER BY source")
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
