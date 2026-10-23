-- 협력사·완성차 기업 DB (PostgreSQL 16)
-- source = 'DART' 인 협력사의 기본 정보·재무는 OpenDART 공시 값이고,
-- 생산능력·품질·계약·견적·인증·RFQ는 seed 기준 모두 실습용 가상 데이터다.
-- 관리자 입력 자료는 각 *_source와 quotes.source(V3 migration)가 MANUAL이다.

DROP VIEW IF EXISTS supplier_overview;
DROP TABLE IF EXISTS financials, quotes, contracts, quality, capabilities, rfqs, suppliers, oems CASCADE;

-- 완성차 기업
CREATE TABLE oems (
    oem_id      TEXT PRIMARY KEY,                 -- OEM-A
    name        TEXT NOT NULL,
    quality_std TEXT NOT NULL                     -- 요구 품질 기준 (예: IATF 16949)
);

-- 2차 협력사 후보
CREATE TABLE suppliers (
    supplier_id    TEXT PRIMARY KEY,              -- SUP-0001
    source         TEXT NOT NULL CHECK (source IN ('DART','MOCK')),  -- 기본 정보 출처
    corp_code      TEXT UNIQUE,                   -- DART 고유번호 (MOCK이면 NULL)
    stock_code     TEXT,                          -- 종목코드
    induty_code    TEXT,                          -- 한국표준산업분류 (303xx = 자동차 부품)
    name           TEXT NOT NULL,
    region         TEXT NOT NULL,                 -- 경남, 울산 ...
    employees      INT  CHECK (employees > 0),    -- 공시에 없으면 NULL
    revenue_eok    INT,                           -- 최근 사업연도 매출(억 원)
    founded_year   INT  NOT NULL,
    biz_no_masked  TEXT NOT NULL,                 -- 123-45-*****
    iatf16949      BOOLEAN NOT NULL DEFAULT FALSE,
    iso14001       BOOLEAN NOT NULL DEFAULT FALSE
);

-- 공정·소재별 생산 능력 (한 협력사가 여러 공정 가능)
CREATE TABLE capabilities (
    supplier_id      TEXT NOT NULL REFERENCES suppliers ON DELETE CASCADE,
    process          TEXT NOT NULL CHECK (process IN ('다이캐스팅','프레스','사출','단조','CNC가공')),
    material         TEXT NOT NULL,               -- ADC12, SPFH590, PA66 ...
    monthly_capacity INT  NOT NULL CHECK (monthly_capacity > 0),  -- 월 생산 가능 수량(개)
    PRIMARY KEY (supplier_id, process, material)
);
CREATE INDEX idx_cap_process_material ON capabilities (process, material);

-- 품질·납기 실적
CREATE TABLE quality (
    supplier_id   TEXT PRIMARY KEY REFERENCES suppliers ON DELETE CASCADE,
    defect_ppm    INT     NOT NULL CHECK (defect_ppm >= 0),       -- 불량률(ppm)
    on_time_rate  NUMERIC(4,3) NOT NULL CHECK (on_time_rate BETWEEN 0 AND 1),  -- 납기 준수율
    audit_grade   CHAR(1) NOT NULL CHECK (audit_grade IN ('A','B','C','D')),   -- 최근 품질 감사 등급
    years_dealing INT     NOT NULL CHECK (years_dealing >= 0)     -- 우리 회사와 거래 기간(년), 0 = 신규 후보
);

-- 기존 거래 조건 (거래 이력이 있는 협력사만)
CREATE TABLE contracts (
    supplier_id        TEXT PRIMARY KEY REFERENCES suppliers ON DELETE CASCADE,
    payment_days       INT     NOT NULL CHECK (payment_days > 0),
    price_linkage      BOOLEAN NOT NULL,          -- 납품대금 연동 약정 여부
    raw_material_ratio NUMERIC(3,2) NOT NULL CHECK (raw_material_ratio BETWEEN 0 AND 1),  -- 단가 중 원재료 비중
    auto_renewal       BOOLEAN NOT NULL
);

-- 과거 견적 이력 (단가 비교 근거)
CREATE TABLE quotes (
    quote_id    SERIAL PRIMARY KEY,
    supplier_id TEXT NOT NULL REFERENCES suppliers ON DELETE CASCADE,
    process     TEXT NOT NULL,
    material    TEXT NOT NULL,
    unit_price  INT  NOT NULL CHECK (unit_price > 0),   -- 개당 단가(원)
    quoted_at   DATE NOT NULL
);
CREATE INDEX idx_quotes_lookup ON quotes (process, material, quoted_at DESC);

-- 완성차가 낸 신차 부품 견적 요청
CREATE TABLE rfqs (
    rfq_id        TEXT PRIMARY KEY,               -- RFQ-2026-001
    oem_id        TEXT NOT NULL REFERENCES oems,
    vehicle       TEXT NOT NULL,                  -- 차종 코드
    part_name     TEXT NOT NULL,
    process       TEXT NOT NULL,
    material      TEXT NOT NULL,
    annual_volume INT  NOT NULL CHECK (annual_volume > 0),
    target_price  INT  NOT NULL CHECK (target_price > 0),  -- 2차 협력사 목표 단가(원)
    required_cert TEXT,                           -- 'IATF16949' 또는 NULL
    due_date      DATE NOT NULL
);

-- 연도별 주요 재무 (DART 단일회사 주요계정, 단위: 원)
CREATE TABLE financials (
    supplier_id       TEXT NOT NULL REFERENCES suppliers ON DELETE CASCADE,
    bsns_year         INT  NOT NULL,
    fs_div            TEXT NOT NULL CHECK (fs_div IN ('CFS','OFS')),  -- 연결 / 별도
    source            TEXT NOT NULL CHECK (source IN ('DART','MOCK')),
    revenue           BIGINT,
    operating_income  BIGINT,
    net_income        BIGINT,
    total_assets      BIGINT,
    total_liabilities BIGINT,
    total_equity      BIGINT,
    PRIMARY KEY (supplier_id, bsns_year)
);

-- 에이전트가 한 번에 읽기 좋은 협력사 요약 뷰
CREATE VIEW supplier_overview AS
SELECT s.*, q.defect_ppm, q.on_time_rate, q.audit_grade, q.years_dealing,
       c.payment_days, c.price_linkage, c.raw_material_ratio, c.auto_renewal,
       f.bsns_year AS fin_year,
       ROUND(f.operating_income::numeric / NULLIF(f.revenue, 0), 3)        AS operating_margin,  -- 영업이익률
       ROUND(f.total_liabilities::numeric / NULLIF(f.total_equity, 0), 2)  AS debt_ratio         -- 부채비율(배)
FROM suppliers s
JOIN quality q USING (supplier_id)
LEFT JOIN contracts c USING (supplier_id)
LEFT JOIN LATERAL (
    SELECT * FROM financials fi WHERE fi.supplier_id = s.supplier_id
    ORDER BY bsns_year DESC LIMIT 1
) f ON TRUE;
