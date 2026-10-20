"""협력사·완성차 기업 DB를 조회하는 MCP 서버 (business-mcp).

실행:  uv run python server.py              # stdio (LangGraph·Claude Desktop 등에서 연결)
       uv run python server.py --http 9102  # Streamable HTTP (http://localhost:9102/mcp)
도구:  list_rfqs, search_suppliers, get_supplier, rank_suppliers

순위 계산과 제외 판단은 모두 이 파일의 코드가 한다. LLM은 결과를 읽고 설명만 한다.
"""

import argparse
import os
from datetime import date
from decimal import Decimal

import psycopg
from fastmcp import FastMCP
from psycopg.rows import dict_row

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://agent:agent@localhost:5432/supplier_db")
PAYMENT_DAYS_LIMIT = 60          # 하도급법 지급 기한
DEBT_RATIO_WARN = 2.0           # 부채비율 200% 초과 시 경고
GRADE_SCORE = {"A": 1.0, "B": 0.67, "C": 0.33, "D": 0.0}

mcp = FastMCP("BusinessServer", instructions=(
    "2차 협력사와 완성차의 기업 정보를 PostgreSQL DB에서 조회한다. "
    "완성차 견적 요청(RFQ), 협력사의 공정·소재별 생산능력, 품질·납기 실적, 기존 계약 조건, "
    "과거 견적 단가, 연도별 재무(매출·영업이익·부채 등)를 다루고, RFQ에 맞는 협력사 순위를 코드로 계산한다. "
    "source가 'DART'인 기본 정보·재무는 공시 값이고 'MOCK'은 가상 값이다. "
    "법 조문 내용과 해석은 다루지 않는다. notes에 하도급법·연동제 관련 내용이 나오면 law-mcp로 조문을 확인한다."
))


def _query(sql, params=()):
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [{k: _plain(v) for k, v in r.items()} for r in rows]


def _plain(v):
    """JSON으로 보낼 수 있게 Decimal·date를 기본 타입으로 바꾼다."""
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, date):
        return v.isoformat()
    return v


def _minmax(value, values, higher_is_better=True):
    lo, hi = min(values), max(values)
    if hi == lo:
        return 1.0
    score = (value - lo) / (hi - lo)
    return score if higher_is_better else 1 - score


@mcp.tool()
def list_rfqs() -> list[dict]:
    """완성차가 낸 신차 부품 견적 요청(RFQ) 목록을 마감일 순으로 돌려준다."""
    return _query("""
        SELECT r.*, o.name AS oem_name
        FROM rfqs r JOIN oems o USING (oem_id)
        ORDER BY due_date
    """)


@mcp.tool()
def search_suppliers(process: str, material: str | None = None, region: str | None = None,
                     iatf_required: bool = False, min_monthly_capacity: int | None = None,
                     limit: int = 20) -> list[dict]:
    """공정·소재·지역·인증·월 생산능력 조건으로 2차 협력사 후보를 찾는다.

    process: 다이캐스팅, 프레스, 사출, 단조, CNC가공 중 하나
    material: ADC12, SPFH590, PA66 등 (생략하면 공정만으로 찾음)
    """
    sql = """
        SELECT o.supplier_id, o.source, o.name, o.region, o.employees, o.revenue_eok, o.iatf16949,
               o.operating_margin, o.debt_ratio,
               c.process, c.material, c.monthly_capacity,
               o.defect_ppm, o.on_time_rate, o.audit_grade, o.years_dealing
        FROM capabilities c JOIN supplier_overview o USING (supplier_id)
        WHERE c.process = %s
    """
    params = [process]
    if material:
        sql += " AND c.material = %s"
        params.append(material)
    if region:
        sql += " AND o.region = %s"
        params.append(region)
    if iatf_required:
        sql += " AND o.iatf16949"
    if min_monthly_capacity:
        sql += " AND c.monthly_capacity >= %s"
        params.append(min_monthly_capacity)
    sql += " ORDER BY o.defect_ppm LIMIT %s"
    params.append(limit)
    return _query(sql, params)


@mcp.tool()
def get_supplier(supplier_id: str) -> dict:
    """협력사 한 곳의 기본 정보, 품질·납기, 기존 계약 조건, 생산 능력, 연도별 재무, 최근 견적을 돌려준다.

    source가 'DART'면 기본 정보와 재무는 공시 값이고, 'MOCK'이면 가상 값이다.
    """
    overview = _query("SELECT * FROM supplier_overview WHERE supplier_id = %s", (supplier_id,))
    if not overview:
        return {"error": f"{supplier_id} 협력사가 없습니다."}
    return {
        **overview[0],
        "capabilities": _query("SELECT process, material, monthly_capacity FROM capabilities "
                               "WHERE supplier_id = %s", (supplier_id,)),
        "financials": _query("SELECT bsns_year, fs_div, source, revenue, operating_income, net_income, "
                             "total_assets, total_liabilities, total_equity FROM financials "
                             "WHERE supplier_id = %s ORDER BY bsns_year", (supplier_id,)),
        "recent_quotes": _query("SELECT process, material, unit_price, quoted_at FROM quotes "
                                "WHERE supplier_id = %s ORDER BY quoted_at DESC LIMIT 5", (supplier_id,)),
    }


@mcp.tool()
def rank_suppliers(rfq_id: str, w_price: float = 0.5, w_quality: float = 0.3,
                   w_delivery: float = 0.2, top_n: int = 10) -> dict:
    """RFQ 하나에 대해 견적을 요청할 2차 협력사 순위를 코드로 계산한다.

    점수 = 가격(최근 견적 단가) × w_price + 품질(불량률·감사 등급) × w_quality + 납기 준수율 × w_delivery
    인증이 없거나 생산 능력이 모자란 곳은 순위에서 빼고 제외 사유를 함께 돌려준다.
    """
    rfq = _query("SELECT * FROM rfqs WHERE rfq_id = %s", (rfq_id,))
    if not rfq:
        return {"error": f"{rfq_id} RFQ가 없습니다."}
    rfq = rfq[0]
    needed_monthly = -(-rfq["annual_volume"] // 12)   # 올림

    rows = _query("""
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
            price_s = _minmax(c["last_price"], prices, higher_is_better=False)
            quality_s = 0.7 * _minmax(c["defect_ppm"], ppms, higher_is_better=False) + 0.3 * GRADE_SCORE[c["audit_grade"]]
            delivery_s = _minmax(c["on_time_rate"], rates)
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--http", type=int, metavar="PORT",
                        help="Streamable HTTP로 실행할 포트 (생략하면 stdio)")
    args = parser.parse_args()
    if args.http:
        mcp.run(transport="http", host="0.0.0.0", port=args.http)
    else:
        mcp.run()
