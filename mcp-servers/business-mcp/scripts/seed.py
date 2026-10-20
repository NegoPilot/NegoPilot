"""협력사·완성차 기업 DB를 채운다.

1) data/dart_companies.csv, data/dart_financials.csv 가 있으면 (collect_dart.py 결과)
   그 회사들을 실제 공시 값으로 먼저 넣는다.  source = 'DART'
2) 나머지는 비상장 소규모 2차 협력사를 가상으로 만들어 N_SUPPLIERS 곳까지 채운다.  source = 'MOCK'
3) 생산능력·품질·기존 계약·견적 이력·인증·RFQ는 공시에 없는 기업 내부/거래 데이터라 전부 가상으로 만든다.

실행:  uv run python scripts/seed.py
같은 seed(42)를 쓰므로 입력 CSV가 같으면 몇 번을 실행해도 같은 데이터가 만들어진다.
"""

import csv
import os
import random
from datetime import date, timedelta
from pathlib import Path

import psycopg

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://agent:agent@localhost:5432/supplier_db")
ROOT = Path(__file__).resolve().parent.parent   # mcp-servers/business-mcp
DATA = ROOT / "data"
N_SUPPLIERS = 150
YEARS = [2023, 2024, 2025]

# 공정별로 다룰 수 있는 소재와 기준 단가(원/개)
MATERIALS = {
    "다이캐스팅": {"ADC12": 4000, "A380": 4500},
    "프레스": {"SPCC": 1200, "SPFH590": 1800, "AL5052": 2200},
    "사출": {"PP": 800, "PA66": 1500, "ABS": 1000},
    "단조": {"S45C": 3500, "SCM440": 4200},
    "CNC가공": {"AL6061": 6000, "S45C": 5000},
}
# 한국표준산업분류(303xx) → 그 업종에서 흔한 공정. 공시에 공정 정보가 없어서 업종으로 추정한다.
KSIC_PROCESSES = {
    "3031": ["다이캐스팅", "CNC가공", "단조"],   # 엔진용 부품
    "3032": ["프레스", "사출"],                 # 차체용 부품
    "3033": ["사출", "CNC가공"],                # 전기장치
    "3039": ["프레스", "단조", "CNC가공", "사출"],  # 기타 부품(조향·현가·제동·동력전달 등)
}
REGION_ABBR = {
    "서울": "서울", "부산": "부산", "대구": "대구", "인천": "인천", "광주": "광주", "대전": "대전",
    "울산": "울산", "세종": "세종", "경기": "경기", "강원": "강원", "충청북": "충북", "충북": "충북",
    "충청남": "충남", "충남": "충남", "전라북": "전북", "전북": "전북", "전라남": "전남", "전남": "전남",
    "경상북": "경북", "경북": "경북", "경상남": "경남", "경남": "경남", "제주": "제주",
}
NAME_HEADS = ["가온", "누리", "다솜", "라온", "미르", "바름", "새길", "아라", "이음", "자람",
              "한결", "해솔", "윤슬", "온새", "나래", "도담", "보람", "시온", "여울", "하랑"]
NAME_TAILS = {"다이캐스팅": "다이캐스팅", "프레스": "프레스", "사출": "폴리머", "단조": "단조", "CNC가공": "정밀"}
MOCK_REGIONS = ["경남", "경남", "울산", "울산", "경북", "대구", "충남", "경기", "광주"]

OEMS = [
    ("OEM-A", "A자동차(가상)", "IATF 16949"),
    ("OEM-B", "B자동차(가상)", "IATF 16949"),
]
RFQS = [
    # rfq_id, oem, 차종, 부품, 공정, 소재, 연간 물량, 목표 단가, 필수 인증, 마감
    ("RFQ-2026-001", "OEM-A", "NX5", "알루미늄 브래킷", "다이캐스팅", "ADC12", 120000, 3900, "IATF16949", date(2026, 11, 20)),
    ("RFQ-2026-002", "OEM-A", "NX5", "시트 레일 보강판", "프레스", "SPFH590", 240000, 1750, "IATF16949", date(2026, 11, 27)),
    ("RFQ-2026-003", "OEM-B", "EV3", "배터리 커버 클립", "사출", "PA66", 600000, 1450, None, date(2026, 12, 4)),
    ("RFQ-2026-004", "OEM-B", "EV3", "구동축 플랜지", "단조", "SCM440", 80000, 4100, "IATF16949", date(2026, 12, 11)),
    ("RFQ-2026-005", "OEM-A", "SX2", "센서 하우징", "CNC가공", "AL6061", 50000, 5900, None, date(2026, 12, 18)),
    ("RFQ-2026-006", "OEM-B", "EV3", "모터 브래킷", "다이캐스팅", "A380", 90000, 4400, "IATF16949", date(2027, 1, 8)),
]


def read_csv(name):
    path = DATA / name
    if not path.exists():
        return []
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def to_int(v):
    return int(v) if v not in (None, "") else None


def region_of(adres):
    for k, v in REGION_ABBR.items():
        if adres.startswith(k):
            return v
    return "기타"


def mask_biz_no(bizr_no):
    d = "".join(ch for ch in bizr_no if ch.isdigit())
    return f"{d[:3]}-{d[3:5]}-*****" if len(d) == 10 else "***-**-*****"


def dart_suppliers():
    """collect_dart.py 결과를 suppliers·financials 행으로 바꾼다."""
    companies = read_csv("dart_companies.csv")
    fins = read_csv("dart_financials.csv")
    by_corp = {}
    for f in fins:
        by_corp.setdefault(f["corp_code"], []).append(f)

    suppliers, financials = [], []
    for i, c in enumerate(companies, 1):
        sid = f"SUP-{i:04d}"
        rows = sorted(by_corp.get(c["corp_code"], []), key=lambda f: f["bsns_year"])
        latest_rev = to_int(rows[-1]["revenue"]) if rows else None
        suppliers.append({
            "supplier_id": sid, "source": "DART", "corp_code": c["corp_code"], "stock_code": c["stock_code"],
            "induty_code": c["induty_code"], "name": c["corp_name"], "region": region_of(c["adres"]),
            "employees": to_int(c["employees"]),
            "revenue_eok": round(latest_rev / 1e8) if latest_rev else None,
            "founded_year": int(c["est_dt"][:4]) if c["est_dt"] else 1990,
            "biz_no_masked": mask_biz_no(c["bizr_no"]),
        })
        for f in rows:
            financials.append((sid, int(f["bsns_year"]), f["fs_div"], "DART",
                               *(to_int(f[k]) for k in ("revenue", "operating_income", "net_income",
                                                         "total_assets", "total_liabilities", "total_equity"))))
    return suppliers, financials


def mock_suppliers(rng, start, count, used_names):
    """공시 대상이 아닌 비상장 소규모 2차 협력사를 가상으로 만든다. 재무도 공시 형식(원 단위)으로 만든다."""
    suppliers, financials = [], []
    for i in range(start, start + count):
        sid = f"SUP-{i:04d}"
        proc = rng.choice(list(MATERIALS))
        name = f"{rng.choice(NAME_HEADS)}{NAME_TAILS[proc]}"
        while name in used_names:
            name = f"{rng.choice(NAME_HEADS)}{rng.choice(NAME_HEADS)}{NAME_TAILS[proc]}"
        used_names.add(name)

        employees = rng.randint(20, 300)
        revenue = int(employees * rng.uniform(1.5e8, 3.0e8))       # 1인당 매출 1.5~3억 원
        suppliers.append({
            "supplier_id": sid, "source": "MOCK", "corp_code": None, "stock_code": None,
            "induty_code": None, "name": name, "region": rng.choice(MOCK_REGIONS), "employees": employees,
            "revenue_eok": round(revenue / 1e8), "founded_year": rng.randint(1985, 2018),
            "biz_no_masked": f"{rng.randint(100, 899)}-{rng.randint(81, 88)}-*****",
            "main_process": proc,
        })
        assets = revenue * rng.uniform(0.6, 1.1)
        debt_ratio = rng.uniform(0.8, 2.5)                          # 부채/자본
        for year in YEARS:                                          # 최근 연도로 갈수록 매출이 조금씩 변함
            rev = int(round(revenue * (1 + rng.uniform(-0.08, 0.06)) ** (year - YEARS[-1]), -6))
            op = int(round(rev * rng.uniform(-0.01, 0.06), -6))
            total = int(round(assets * rev / revenue * rng.uniform(0.97, 1.03), -6))
            equity = int(round(total / (1 + debt_ratio * rng.uniform(0.95, 1.05)), -6))
            financials.append((sid, year, "OFS", "MOCK", rev, op, int(round(op * rng.uniform(0.5, 0.8), -6)),
                               total, total - equity, equity))
    return suppliers, financials


def processes_for(s, rng):
    if s.get("main_process"):
        main = s["main_process"]
    else:
        options = KSIC_PROCESSES.get((s["induty_code"] or "")[:4], list(MATERIALS))
        main = rng.choice(options)
    extra = rng.choice(list(MATERIALS)) if rng.random() < 0.25 else None
    return list(dict.fromkeys([main] + ([extra] if extra else [])))


def internal_data(rng, suppliers):
    """공시에 없는 기업 내부·거래 데이터: 인증, 생산능력, 품질, 기존 계약, 견적 이력."""
    caps, quality, contracts, quotes, certs = [], [], [], [], {}
    for s in suppliers:
        sid = s["supplier_id"]
        big = (s["employees"] or 100) >= 300
        certs[sid] = (rng.random() < (0.9 if big else 0.5), rng.random() < (0.7 if big else 0.3))
        price_factor = rng.gauss(0.97 if big else 1.0, 0.08)
        for proc in processes_for(s, rng):
            for mat in rng.sample(list(MATERIALS[proc]), k=rng.randint(1, len(MATERIALS[proc]))):
                cap = rng.choice([20000, 40000, 60000, 100000] if big else [3000, 5000, 8000, 12000, 20000, 40000])
                caps.append((sid, proc, mat, cap))
                for _ in range(rng.randint(1, 3)):
                    quotes.append((sid, proc, mat,
                                   int(round(MATERIALS[proc][mat] * price_factor * rng.uniform(0.97, 1.03), -1)),
                                   date(2025, 1, 1) + timedelta(days=rng.randint(0, 640))))
        years = rng.choice([0, 0, 0, 1, 2, 3, 5, 8, 12])
        quality.append((sid, int(rng.lognormvariate(5.2 if big else 5.6, 0.8)),
                        round(rng.uniform(0.88 if big else 0.85, 0.995), 3),
                        rng.choices("ABCD", weights=[4, 4, 1, 1] if big else [2, 4, 3, 1])[0], years))
        if years > 0:
            contracts.append((sid, rng.choice([30, 45, 60, 60, 60, 75, 90]),   # 75·90일은 하도급법 점검용
                              rng.random() < 0.3, round(rng.uniform(0.3, 0.6), 2), rng.random() < 0.7))
    return certs, caps, quality, contracts, quotes


def dump_csv(name, header, rows):
    DATA.mkdir(exist_ok=True)
    with open(DATA / f"{name}.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def main():
    rng = random.Random(42)
    suppliers, financials = dart_suppliers()
    used = {s["name"] for s in suppliers}
    mock_s, mock_f = mock_suppliers(rng, len(suppliers) + 1, max(0, N_SUPPLIERS - len(suppliers)), used)
    suppliers += mock_s
    financials += mock_f
    certs, caps, quality, contracts, quotes = internal_data(rng, suppliers)

    sup_cols = ["supplier_id", "source", "corp_code", "stock_code", "induty_code", "name", "region",
                "employees", "revenue_eok", "founded_year", "biz_no_masked"]
    sup_rows = [tuple(s[c] for c in sup_cols) + certs[s["supplier_id"]] for s in suppliers]
    fin_cols = ["supplier_id", "bsns_year", "fs_div", "source", "revenue", "operating_income", "net_income",
                "total_assets", "total_liabilities", "total_equity"]

    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:
        cur.execute((ROOT / "schema.sql").read_text(encoding="utf-8"))
        cur.executemany("INSERT INTO oems VALUES (%s,%s,%s)", OEMS)
        cur.executemany(f"INSERT INTO suppliers ({','.join(sup_cols)}, iatf16949, iso14001) "
                        f"VALUES ({','.join(['%s'] * (len(sup_cols) + 2))})", sup_rows)
        cur.executemany(f"INSERT INTO financials ({','.join(fin_cols)}) VALUES ({','.join(['%s'] * 10)})", financials)
        cur.executemany("INSERT INTO capabilities VALUES (%s,%s,%s,%s)", caps)
        cur.executemany("INSERT INTO quality VALUES (%s,%s,%s,%s,%s)", quality)
        cur.executemany("INSERT INTO contracts VALUES (%s,%s,%s,%s,%s)", contracts)
        cur.executemany("INSERT INTO quotes (supplier_id, process, material, unit_price, quoted_at) "
                        "VALUES (%s,%s,%s,%s,%s)", quotes)
        cur.executemany("INSERT INTO rfqs VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", RFQS)

    # 엑셀로 열어 보기 쉽게 DB에 들어간 그대로 CSV도 남긴다
    dump_csv("oems", ["oem_id", "name", "quality_std"], OEMS)
    dump_csv("suppliers", sup_cols + ["iatf16949", "iso14001"], sup_rows)
    dump_csv("financials", fin_cols, financials)
    dump_csv("capabilities", ["supplier_id", "process", "material", "monthly_capacity"], caps)
    dump_csv("quality", ["supplier_id", "defect_ppm", "on_time_rate", "audit_grade", "years_dealing"], quality)
    dump_csv("contracts", ["supplier_id", "payment_days", "price_linkage", "raw_material_ratio", "auto_renewal"],
             contracts)
    dump_csv("quotes", ["supplier_id", "process", "material", "unit_price", "quoted_at"], quotes)
    dump_csv("rfqs", ["rfq_id", "oem_id", "vehicle", "part_name", "process", "material", "annual_volume",
                      "target_price", "required_cert", "due_date"], RFQS)

    n_dart = sum(s["source"] == "DART" for s in suppliers)
    print(f"완료: 협력사 {len(suppliers)}곳 (DART {n_dart}, 가상 {len(suppliers) - n_dart}), 재무 {len(financials)}행, "
          f"생산능력 {len(caps)}건, 기존 계약 {len(contracts)}건, 견적 {len(quotes)}건, RFQ {len(RFQS)}건")
    if n_dart == 0:
        print("참고: data/dart_companies.csv 가 없어 전부 가상 데이터로 채웠습니다. scripts/collect_dart.py 를 먼저 실행하세요.")


if __name__ == "__main__":
    main()
