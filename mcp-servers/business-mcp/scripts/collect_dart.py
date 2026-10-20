"""OpenDART에서 자동차 부품 제조사의 기업 개황·재무·직원 수를 모은다.

준비:  https://opendart.fss.or.kr 에서 인증키를 발급받고,
       business-mcp 폴더의 .env 파일에  DART_API_KEY=발급받은키  한 줄을 넣는다.
실행:  uv run python scripts/collect_dart.py            (상장사 전체를 훑어 자동차 부품 업종만 수집)
       uv run python scripts/collect_dart.py --max 30   (30곳만 모으고 멈춤, 테스트용)

결과:  data/dart_companies.csv, data/dart_financials.csv
       응답 원본은 data/dart_raw/ 에 캐시되어, 다시 실행하면 이미 받은 것은 건너뛴다.

업종 기준: 한국표준산업분류 303 (자동차 신품 부품 제조업).
고유번호 목록에는 업종이 없어서, 종목코드가 있는 회사 약 4,000곳(상장폐지 회사 포함)의 기업 개황을
모두 한 번씩 조회한다. 동시에 WORKERS개씩 불러 첫 실행은 수 분이 걸리고, 다음부터는 캐시를 쓴다.
OpenDART 일일 호출 한도는 20,000건이다.
"""

import argparse
import csv
import io
import json
import os
import time
import urllib.parse
import urllib.request
import zipfile
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent   # mcp-servers/business-mcp
DATA = ROOT / "data"
RAW = DATA / "dart_raw"
BASE_URL = os.getenv("DART_BASE_URL", "https://opendart.fss.or.kr/api")
AUTO_PARTS_KSIC = "303"
LISTED_CLS = ("Y", "K", "N")     # 유가증권·코스닥·코넥스 (E = 기타: 상장폐지 회사 등은 제외)
WORKERS = 4                       # 기업 개황 동시 조회 수 (너무 늘리면 OpenDART가 접속을 막을 수 있음)
YEARS = ["2023", "2024", "2025"]
REPORT_ANNUAL = "11011"           # 사업보고서
ACCOUNTS = {                      # account_nm → 저장할 열 이름
    "매출액": "revenue", "수익(매출액)": "revenue", "영업수익": "revenue",
    "영업이익": "operating_income", "영업이익(손실)": "operating_income",
    "당기순이익": "net_income", "당기순이익(손실)": "net_income",
    "자산총계": "total_assets", "부채총계": "total_liabilities", "자본총계": "total_equity",
}


def load_key():
    key = os.getenv("DART_API_KEY")
    env = ROOT / ".env"
    if not key and env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("DART_API_KEY="):
                key = line.split("=", 1)[1].strip()
    if not key:
        raise SystemExit("DART_API_KEY가 없습니다. .env 파일에 DART_API_KEY=발급받은키 를 넣어 주세요.")
    return key


def get(path, key, **params):
    url = f"{BASE_URL}/{path}?" + urllib.parse.urlencode({"crtfc_key": key, **params})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return r.read()
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))


def get_json(path, key, cache_name, **params):
    """응답을 캐시에 저장하고, 이미 있으면 다시 부르지 않는다."""
    cache = RAW / path.replace(".json", "") / f"{cache_name}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    body = json.loads(get(path, key, **params))
    if body.get("status") == "020":
        raise SystemExit("OpenDART 일일 호출 한도를 넘었습니다. 내일 다시 실행하면 이어서 수집합니다.")
    if body.get("status") not in ("000", "013"):          # 013 = 조회된 데이터 없음
        raise SystemExit(f"OpenDART 오류 {body.get('status')}: {body.get('message')}")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    time.sleep(0.1)
    return body


def corp_list(key):
    cache = RAW / "CORPCODE.xml"
    if not cache.exists():
        RAW.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(get("corpCode.xml", key))) as z:
            cache.write_bytes(z.read(z.namelist()[0]))
    root = ET.parse(cache).getroot()
    return [{"corp_code": e.findtext("corp_code"), "corp_name": e.findtext("corp_name"),
             "stock_code": (e.findtext("stock_code") or "").strip()} for e in root.iter("list")]


def to_int(s):
    s = (s or "").replace(",", "").strip()
    return int(s) if s.lstrip("-").isdigit() else None


def financials(key, corp_code):
    rows = []
    for year in YEARS:
        body = get_json("fnlttSinglAcnt.json", key, f"{corp_code}_{year}",
                        corp_code=corp_code, bsns_year=year, reprt_code=REPORT_ANNUAL)
        items = body.get("list", [])
        fs_div = "CFS" if any(i["fs_div"] == "CFS" for i in items) else "OFS"   # 연결 우선
        row = {"corp_code": corp_code, "bsns_year": year, "fs_div": fs_div}
        for i in items:
            col = ACCOUNTS.get(i["account_nm"].replace(" ", ""))
            if i["fs_div"] == fs_div and col and col not in row:
                row[col] = to_int(i["thstrm_amount"])
        if len(row) > 3:
            rows.append(row)
    return rows


def employees(key, corp_code):
    for year in reversed(YEARS):
        body = get_json("empSttus.json", key, f"{corp_code}_{year}",
                        corp_code=corp_code, bsns_year=year, reprt_code=REPORT_ANNUAL)
        total = sum(to_int(i.get("sm")) or 0 for i in body.get("list", []))
        if total:
            return total
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=0, help="자동차 부품사를 이만큼 모으면 멈춤 (0 = 전체)")
    args = ap.parse_args()
    key = load_key()

    listed = [c for c in corp_list(key) if c["stock_code"]]
    print(f"종목코드가 있는 회사 {len(listed)}곳에서 자동차 부품 업종(KSIC {AUTO_PARTS_KSIC}) 상장사 찾는 중...")

    companies, fin_rows = [], []
    pool = ThreadPoolExecutor(WORKERS)
    infos = pool.map(lambda c: get_json("company.json", key, c["corp_code"], corp_code=c["corp_code"]), listed)
    for n, (c, info) in enumerate(zip(listed, infos), 1):
        if n % 200 == 0:
            print(f"  ... {n}/{len(listed)}곳 확인, 자동차 부품사 {len(companies)}곳")
        if info.get("corp_cls") not in LISTED_CLS or not str(info.get("induty_code", "")).startswith(AUTO_PARTS_KSIC):
            continue
        fins = financials(key, c["corp_code"])
        companies.append({
            "corp_code": c["corp_code"], "corp_name": info["corp_name"], "stock_code": info["stock_code"],
            "corp_cls": info["corp_cls"], "induty_code": info["induty_code"], "ceo_nm": info["ceo_nm"],
            "adres": info["adres"], "est_dt": info["est_dt"], "bizr_no": info["bizr_no"],
            "employees": employees(key, c["corp_code"]),
        })
        fin_rows += fins
        print(f"  [{n}/{len(listed)}] {info['corp_name']} ({info['induty_code']}) 재무 {len(fins)}개년")
        if args.max and len(companies) >= args.max:
            break
    pool.shutdown(cancel_futures=True)

    DATA.mkdir(exist_ok=True)
    with open(DATA / "dart_companies.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(companies[0]) if companies else ["corp_code"])
        w.writeheader()
        w.writerows(companies)
    fin_cols = ["corp_code", "bsns_year", "fs_div", "revenue", "operating_income", "net_income",
                "total_assets", "total_liabilities", "total_equity"]
    with open(DATA / "dart_financials.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fin_cols)
        w.writeheader()
        w.writerows(fin_rows)
    print(f"완료: 자동차 부품사 {len(companies)}곳, 재무 {len(fin_rows)}행 → data/dart_*.csv")


if __name__ == "__main__":
    main()
