# business-mcp

공급사 협상 Agent가 2차 협력사 후보를 찾고 순위를 매길 때 쓰는 **협력사·완성차 기업 DB(PostgreSQL)와 MCP Server**입니다.
실행 중에는 외부 API를 부르지 않고 PostgreSQL만 읽습니다.
법 조문 해석은 다루지 않습니다. `rank_suppliers`의 `notes`에 하도급법·연동제 관련 내용이 나오면 Agent가 law-mcp로 조문을 확인합니다.

처음 실행한다면 단계별 안내인 [SETUP.md](SETUP.md)를 먼저 보세요.

## 데이터 출처

| 데이터 | 출처 |
| --- | --- |
| 기업 개황 (회사명, 지역, 설립연도, 업종, 사업자번호, 직원 수) | **OpenDART 공시** (`scripts/collect_dart.py`) |
| 연도별 재무 (매출, 영업이익, 순이익, 자산·부채·자본) | **OpenDART 공시** (사업보고서 주요계정) |
| 생산능력, 품질·납기, 기존 계약, 견적 이력, 인증, RFQ | 가상 데이터 (공시에 없는 기업 내부·거래 데이터) |
| 공시 대상이 아닌 소규모 비상장 협력사 | 가상 데이터 (공시와 같은 형식) |

- `suppliers.source`는 `DART`와 `MOCK` 및 `MANUAL`을 구분합니다. MANUAL은 관리자가 등록하거나 수정한 기본 정보입니다. `financials.source`는 각 연도 재무의 DART 또는 MOCK 출처를 유지합니다. 기본 정보 출처로 재무 출처를 추정하지 않습니다.
- 인증과 생산능력 및 품질은 각각 `certification_source`와 `capability_source` 및 `quality_source`로 구분합니다. 기존 가상 자료는 MOCK입니다. 관리자가 수정한 영역은 MANUAL입니다. DART 기업의 인증도 공시 자료를 뜻하지 않습니다.
- DART 회사의 공정은 공시에 없어서 업종 코드(KSIC 303xx)로 추정합니다.
- 팀원 모두 같은 seed 데이터로 작업하도록 DART 수집 결과(`data/dart_companies.csv`, `data/dart_financials.csv`, 자동차 부품 상장사 30곳)를 레포에 포함합니다. 공시된 공공데이터라 팀에서 포함하기로 정했습니다. `seed.py`가 이 CSV를 읽으므로 인증키 없이 모두 같은 DB가 만들어집니다.
- 회사를 더 넣거나 최신 공시로 바꾸는 절차는 [SETUP.md의 데이터 추가·갱신 방법](SETUP.md#데이터-추가갱신-방법)을 보세요.

## 도구

| 종류 | 이름 | 설명 |
| --- | --- | --- |
| Tool | `list_rfqs()` | 완성차 견적 요청(RFQ) 목록, 마감일 순 |
| Tool | `search_suppliers(process?, material?, region?, iatf_required?, min_monthly_capacity?, limit?, distinct_suppliers?, iso14001_required?)` | 주어진 조건으로 협력사 후보 검색. 불량률 낮은 순 |
| Tool | `find_suppliers(supplier, limit?)` | 이름이나 ID로 기업 후보 조회. 정확한 회사명 일치 우선 |
| Tool | `get_supplier(supplier)` | 협력사 한 곳의 기본 정보, 품질, 계약, 생산능력, 재무, 최근 견적 |
| Tool | `rank_suppliers(rfq_id, w_price?, w_quality?, w_delivery?, top_n?)` | RFQ 기준 후보 순위와 제외 사유. 점수는 코드로 계산 |
| Tool | `check_offer(supplier, rfq_id, unit_price, payment_days, price_linkage, raw_material_ratio?, contract_days?, linkage_waived_by_agreement?, buyer_is_small_business?)` | 협상안의 하도급법 검사. 위반·경고·참고와 근거 조문 |
| Resource | `business://catalog` | 공정별 소재, 별칭, 지역, RFQ 목록, 출처별 협력사 수 |

- 모든 Tool은 `readOnlyHint`가 붙은 조회 도구입니다.
- 쿼리 리라이트 결과를 그대로 받을 수 있게 입력을 정규화합니다.
  - `process`: `스탬핑` → 프레스, `사출성형` → 사출, `CNC` → CNC가공 등
  - `material`: 소재 코드(`ADC12`) 또는 계열 이름(`알루미늄` → ADC12·A380 등, `강판` → SPCC·SPFH590)
  - `region`: `경상남도`, `울산광역시` → 경남, 울산
  - `supplier`: `SUP-0001`, `1`, 회사명(`서연이화`, `(주)서연이화`, 이름 일부)
- 잘못된 값이 들어오면 `ToolError`로 사용 가능한 값이나 후보를 알려 줍니다. 예: `알 수 없는 공정 '용접'. 사용 가능: 다이캐스팅, 프레스, 사출, 단조, CNC가공`
- `rank_suppliers`는 신규 후보 여부, 최근 영업적자, 부채비율 200% 초과 등을 `notes`(사람이 읽는 요약)로, 기존 계약의 법규 점검 결과를 `flags`로 함께 알려 줍니다.

### 공정 없는 검색과 기업 이름 조회

`search_suppliers`는 공정을 생략할 수 있습니다. 지역이나 인증 또는 소재만 지정해도 검색합니다. 조건을 모두 생략하면 제한된 전체 목록을 반환합니다. 소재를 지정하면 실제 DB에 있는 지원 코드를 확인합니다. 공정이 있으면 해당 공정의 코드만 사용하며 공정이 없으면 모든 공정의 코드 합집합을 사용합니다.

기본값인 `distinct_suppliers=False`에서는 기존과 같은 공정별 소재 행을 반환합니다. 한 기업이 여러 행으로 나올 수 있습니다. `limit`은 기업 수가 아닌 반환 행 수이며 기본 20입니다. 1보다 작으면 1로 제한하고 100보다 크면 100으로 제한합니다. 정렬은 불량률 다음 기업 ID 다음 공정 다음 소재 순서입니다. 반환 행 수를 전체 기업 수로 해석하면 안 됩니다.

기업 수를 지정한 검색에는 `distinct_suppliers=True`를 사용합니다. 예를 들어 `search_suppliers(region="부산", limit=3, distinct_suppliers=True)`는 조건에 맞는 서로 다른 기업을 최대 3곳 반환합니다. 모든 검색 조건을 적용한 행 중 기업마다 공정과 소재 순서로 대표 행 하나를 선택합니다. 그다음 불량률과 기업 ID 순서로 정렬하고 마지막에 `limit`을 적용합니다. 이 모드의 `limit`은 기업 수이며 범위는 동일하게 1부터 100까지입니다. 기본 모드의 동작과 응답 필드는 유지합니다.

대표 행의 공정과 소재 및 월 생산능력은 해당 조건에 맞는 한 행의 값입니다. 기업 전체의 생산능력이나 모든 지원 공정을 뜻하지 않습니다. 전체 생산능력 목록과 재무 등은 `get_supplier`로 조회합니다. 반환 수가 `limit`과 같으면 전체 후보가 그 수뿐이라고 단정할 수 없습니다.

`find_suppliers`는 상세 조회 전에 기업을 선택할 때 사용합니다. `SUP-0001`과 `0001` 및 `1`은 같은 ID로 조회합니다. 이름은 회사 표기와 공백을 제거하고 대소문자를 구분하지 않습니다. 정규화한 회사명이 정확히 일치하면 해당 후보만 반환합니다. 정확한 일치가 없을 때 부분 일치를 조회합니다. `%`와 `_` 및 `!`는 문자 그대로 검색합니다.

```json
{
  "matches": [{"supplier_id": "SUP-0001", "name": "예시 기업", "region": "경남"}],
  "total": 1,
  "match_type": "exact"
}
```

위 내용은 응답 형식을 보여 주는 예시입니다. `match_type`은 ID 조회일 때 `id`이며 이름 조회일 때 `exact` 또는 `partial`입니다. `matches`는 기업 ID 순서입니다. `limit`의 기본값은 10이며 1부터 100까지로 제한합니다. `total`은 이 제한을 적용하기 전의 후보 수입니다. 정확 일치가 있으면 `total`도 정확 일치 후보만 셉니다. 여러 후보를 임의로 한 기업으로 결정하지 않습니다.

0건은 `matches: []`와 `total: 0`으로 반환합니다. 없는 ID의 `match_type`은 `id`이며 없는 이름은 `partial`입니다. 빈 입력은 `ToolError`입니다. DB 장애도 예외로 전달합니다. `get_supplier`의 기존 상세 응답과 모호한 이름 및 없는 기업의 오류 동작은 유지합니다.

## 협상안 검사 (`check_offer`)

법 조문을 찾아 인용하는 일은 law-mcp가, 협상안이 법에 어긋나는지 판정하는 일은 이 서버가 **코드 규칙으로** 합니다.
결과 항목은 `{code, severity, message, law_refs}`이고, `law_refs`의 `{law, article_no}`를 law-mcp `get_article`에 그대로 넘기면 조문 원문과 `citation`을 받을 수 있습니다.

| code | severity | 기준 | 근거 |
| --- | --- | --- | --- |
| `PAYMENT_DAYS_OVER_60` | violation | 지급 기한이 수령일부터 60일 초과 | 하도급법 제13조① |
| `LINKAGE_TERMS_MISSING` | violation | 원재료 비중 10% 이상(주요 원재료)인데 연동 약정 없음 | 제2조⑯, 제3조②3 |
| `LINKAGE_WAIVED_NEEDS_WRITTEN_REASON` | warning | 연동하지 않기로 합의함 → 취지·사유를 서면에 기재 | 제3조④4 |
| `LINKAGE_EXEMPT` | info | 원사업자 소기업, 거래 90일 이내, 하도급대금 1억 원 이하 | 제3조④1~3 |
| `RAW_MATERIAL_RATIO_UNKNOWN` | warning | 원재료 비중을 몰라 연동 대상 판단 불가 | 제2조⑯, 제3조 |
| `PRICE_BELOW_MARKET` | warning | 같은 공정·소재 최근 견적 중앙값의 90% 미만 | 제4조 |
| `PRICE_CUT_FROM_LAST_QUOTE` | warning | 그 협력사 직전 견적보다 5% 넘게 낮음 | 제4조②1·5 |
| `SUBCONTRACT_ACT_APPLIES` / `SME_STATUS_CHECK` / `SME_STATUS_UNKNOWN` | info | 수급사업자(중소기업) 해당 가능성 (최근 매출 1,000억 원 기준) | 제2조③ |
| `ABOVE_TARGET_PRICE`, `CERT_MISSING`, `CAPABILITY_MISSING`, `CAPACITY_SHORT` | info / warning | RFQ 목표 단가·인증·생산능력 (거래 조건) | - |

- `verdict`는 violation이 있으면 `위반 소지 있음`, warning만 있으면 `확인 필요`, 없으면 `문제 없음`입니다.
- 90%·5% 단가 기준은 팀 내부 기준이고, 법이 정한 수치가 아닙니다.
- 거래 기간 90일·하도급대금 1억 원은 법률이 정한 상한이고 실제 값은 시행령으로 정합니다. 중소기업 매출 기준은 중소기업기본법 시행령 별표1인데 law-mcp 색인에 별표가 없어 확인이 필요합니다.
- 판정은 위험 신호입니다. 예외(대등한 지위에서 정한 지급기일 등) 해당 여부의 최종 판단은 법무 검토로 확인합니다.

## 실행

```bash
docker compose up -d                       # PostgreSQL (이미 설치된 PostgreSQL을 써도 됨, SETUP.md 참고)
uv sync
uv run python scripts/seed.py              # 테이블을 만들고 데이터를 넣음 (다시 실행하면 처음부터 새로 만듦)
uv run python scripts/migrate_management.py # DATABASE_URL을 명시한 뒤 비파괴 관리 스키마 적용
uv run python server.py                    # stdio
uv run python server.py --http 9102        # Streamable HTTP: http://localhost:9102/mcp
uv run python -m pytest -q                 # MCP Client로 도구 호출 테스트
```

- 접속 정보는 `DATABASE_URL` 환경변수로 바꿉니다. 기본값은 `postgresql://agent:agent@localhost:5432/supplier_db`입니다.
- 테스트는 같은 DB 안에 고유한 `pytest_business_` 접두사의 임시 스키마를 만들어 seed와 관리 migration을 적용한 뒤 지웁니다. 공개 스키마와 저장소 CSV는 변경하지 않습니다. DB에 연결할 수 없으면 테스트를 건너뜁니다.

DB 없이 검색 규칙과 SQL 인자 및 상세 조회 호환성만 확인하려면 아래 명령을 사용합니다. `query`를 대역으로 바꾸고 DB 연결을 차단합니다. 기존 `tests/conftest.py`의 자동 seed를 실행하지 않습니다.

```bash
uv run python -m pytest offline_tests -q --confcutdir=offline_tests
```

## LangGraph에서 연결하기

```python
from langchain_mcp_adapters.client import MultiServerMCPClient

client = MultiServerMCPClient({
    # stdio: Agent가 서버를 직접 띄움 (경로는 각자 절대 경로로)
    "business": {"command": "uv", "args": ["run", "--directory", "/절대/경로/NegoPilot/mcp-servers/business-mcp",
                                           "python", "server.py"], "transport": "stdio"},
    # 또는 HTTP: 서버를 따로 띄워 두고 주소로 연결 (uv run python server.py --http 9102)
    # "business": {"url": "http://localhost:9102/mcp", "transport": "streamable_http"},
})
tools = await client.get_tools()
```

## 테이블

| 테이블 | 내용 |
| --- | --- |
| `oems` | 완성차 (가상 2곳) |
| `rfqs` | 완성차의 신차 부품 견적 요청: 공정, 소재, 연간 물량, 목표 단가, 필수 인증 |
| `suppliers` | 협력사 기본 정보: 출처, DART 고유번호, 업종, 지역, 규모, 인증, 사업자번호(마스킹) |
| `financials` | 연도별 매출·영업이익·순이익·자산·부채·자본 (원 단위, 연결 우선) |
| `capabilities` | 공정·소재별 월 생산능력 |
| `quality` | 불량률(ppm), 납기 준수율, 감사 등급, 거래 기간 |
| `contracts` | 기존 거래 조건: 결제 기간, 연동 약정 여부, 원재료 비중 |
| `quotes` | 과거 견적 단가 |

요약 뷰 `supplier_overview`는 협력사·품질·계약과 최근 연도 재무(영업이익률, 부채비율)를 한 줄로 합칩니다. ERD는 `docs/ERD.png`에 있습니다.

## 폴더 구조

```
schema.sql                  테이블 8개 + 요약 뷰 supplier_overview
data/dart_companies.csv     DART 수집 결과: 자동차 부품 상장사 개황 (레포에 포함)
data/dart_financials.csv    DART 수집 결과: 2023~2025 재무 (레포에 포함)
data/dart_raw/              OpenDART 응답 캐시 (git 제외)
scripts/collect_dart.py     OpenDART에서 개황·재무·직원 수 수집 → data/dart_*.csv (인증키 필요)
scripts/seed.py             DART 회사 + 가상 협력사로 150곳 구성 (seed 42), DB에 넣고 data/*.csv 사본 저장
server.py                   MCP Server
tests/                      MCP Client 테스트 (임시 스키마 사용)
offline_tests/              DB 없는 검색과 상세 조회 회귀 테스트
docs/ERD.png                ERD
docker-compose.yml          PostgreSQL 16 컨테이너
SETUP.md                    단계별 실행 가이드, 데이터 추가·갱신 방법, 자주 나는 오류
```


## 관리자 변경과 읽기 전용 MCP 계정

[관리자 협력사 계약](../../docs/contracts/admin-suppliers.md)은 Spring의 등록과 수정 및 비활성화를 정의합니다. 재무와 계약 및 견적은 조회만 제공합니다. 모든 수정은 version을 확인하고 수정자 UUID와 변경 전후 자료를 기록합니다. DART 기본 정보는 수정하지 않습니다. MOCK 기본 정보를 수정하면 MANUAL로 표시하며 최초 원본은 보존합니다.

새 MCP는 관리 스키마의 status를 사용합니다. 기존 데이터가 있는 DB에서는 seed를 다시 실행하지 않습니다. 스키마 변경 권한을 가진 접속 정보를 DATABASE_URL 환경 변수로 명시한 뒤 다음 명령만 실행합니다.

```bash
uv run python scripts/migrate_management.py
```

이 명령은 저장소의 `backend/src/main/resources/db/business/V1__supplier_management.sql`을 적용합니다. 기존 8개 테이블이 없으면 중단하며 자동으로 만들거나 seed하지 않습니다. 빈 실습 DB는 먼저 기존 seed를 명시적으로 실행해야 합니다. 같은 migration을 반복하거나 이후 Spring Flyway를 실행해도 원본과 수정 버전 및 감사 이력이 유지됩니다. 적용을 마친 다음 MCP를 재시작합니다.

비활성 기업은 검색과 이름 조회 및 상세와 추천 및 협상안 검사에서 제외됩니다. 카탈로그와 시장 견적 중앙값에서도 제외됩니다. 과거 채팅에 이미 저장한 근거는 변경하지 않습니다. 품질이나 견적이 없는 활성 기업은 상세 조회가 가능하지만 추천 점수 계산에서 빠지고 자료 미등록 사유를 반환합니다.

MCP 실행 계정에는 기존 데이터 조회 권한만 부여합니다. 마이그레이션 계정과 Spring 관리 계정 및 회원 DB 계정을 재사용하지 않습니다. 아래는 DBA가 적용할 권한 예시입니다. DB 이름과 schema는 실제 환경에 맞춥니다. 비밀번호는 psql의 대화형 명령으로 지정합니다.

```sql
CREATE ROLE negopilot_business_reader LOGIN;
\password negopilot_business_reader
GRANT CONNECT ON DATABASE supplier_db TO negopilot_business_reader;
GRANT USAGE ON SCHEMA public TO negopilot_business_reader;
GRANT SELECT ON public.suppliers, public.capabilities, public.quality,
    public.contracts, public.quotes, public.financials, public.oems,
    public.rfqs, public.supplier_overview TO negopilot_business_reader;
```

이 계정은 테이블 소유자나 superuser가 아니어야 합니다. 추가 그룹 권한도 확인합니다. supplier_change_events와 회원 DB에는 권한을 주지 않습니다. MCP 프로세스의 DATABASE_URL만 이 읽기 계정으로 설정합니다. 관리자 변경은 인증된 Spring 관리 API에서 실행하며 MCP에는 쓰기 도구를 추가하지 않습니다.

## 공식 별표 기반 기업 규모 검토

`SME_REVENUE_EOK=1000` 고정값과 단년도 매출에 의한 적용대상 추정을 제거했다.
`assess_company_size(company, as_of)` 및 `check_offer(..., supplier_size=..., buyer_size=..., as_of=...)`
에서 `company_size.py`의 동일한 규칙을 사용한다. 기준 원문과 SHA-256은
`data/legal_size_rules.json`에 기록한다. 제조업 C10~C34를 지원하며 다른 업종은 보류한다.

입력 예시(금액 단위: 억 원):

```json
{
  "company": {
    "ksic_code": "C30399",
    "average_revenue_eok": 1100,
    "assets_eok": 2000,
    "independence_met": true,
    "has_related_enterprises": false,
    "transition_reviewed": true,
    "deemed_sme": false,
    "evidence_reference": "검토한 재무자료·기업확인서 식별자"
  },
  "as_of": "2026-10-23"
}
```

- C30 기본 1,200억 원 / C30393 자동차용 신품 의자 1,500억 원. 소기업은 120억 원.
  C30만 입력하면 의자 예외를 배제할 세부코드 또는 `seat_manufacturing=false`가 필요하다.
- 자산총액 5,000억 원 **미만**, 독립성, 관계기업, 유예·경과조치 검토를 함께 요구한다.
- 평균매출액은 법정 산정 방식으로 확인한 값. DB 최근 1년 매출을 자동 대입하지 않는다.
- 관계기업이 있으면 별표 2로 검증한 `related_aggregate_revenue_eok`와
  `related_calculation_verified=true`가 필요하다. 복잡한 지배구조 합산을 자동 추정하지 않는다.
- `transition_reviewed`, `deemed_sme`, `evidence_reference`는 검토 근거의 진술이며
  외부 기업확인서 진위를 자동 검증하는 기능은 아니다.
- 부족한 정보는 `unknown`과 `missing`으로 반환한다. 기업 규모 충족만으로 하도급법 적용을 확정하지 않는다.
- 구매자의 검증된 `is_small_business=true`만 연동 소기업 예외에 반영한다.
  이전 `buyer_is_small_business` 단독 입력은 경고만 반환한다.
- 현재 검증 범위는 2026-01-02~2026-10-23이다. 이 기간 밖의 규모 판정은 보류한다.
  운영 시 공식 별표·경과조치 재검토 후 규칙의 검증일을 갱신해야 한다.
- 공식 고시·지침·서식은 law-mcp 검색 근거로 추가했다. 모든 지침 사례를 자동 위법 판정
  규칙으로 변환한 것은 아니며, 기존 단가 경고 비율은 내부 위험탐지 기준이다.

검증: `PYTHONPATH=. uv run pytest offline_tests tests -q`.
DB 통합 테스트는 별도 임시 스키마를 만들고 종료 시 삭제한다.
