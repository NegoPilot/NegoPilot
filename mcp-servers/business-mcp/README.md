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

- `suppliers.source`와 `financials.source`가 `DART`인지 `MOCK`인지로 구분합니다.
- DART 회사의 공정은 공시에 없어서 업종 코드(KSIC 303xx)로 추정합니다.
- 팀원 모두 같은 seed 데이터로 작업하도록 DART 수집 결과(`data/dart_companies.csv`, `data/dart_financials.csv`, 자동차 부품 상장사 30곳)를 레포에 포함합니다. 공시된 공공데이터라 팀에서 포함하기로 정했습니다. `seed.py`가 이 CSV를 읽으므로 인증키 없이 모두 같은 DB가 만들어집니다.
- 회사를 더 넣거나 최신 공시로 바꾸는 절차는 [SETUP.md의 데이터 추가·갱신 방법](SETUP.md#데이터-추가갱신-방법)을 보세요.

## 도구

| 종류 | 이름 | 설명 |
| --- | --- | --- |
| Tool | `list_rfqs()` | 완성차 견적 요청(RFQ) 목록, 마감일 순 |
| Tool | `search_suppliers(process, material?, region?, iatf_required?, min_monthly_capacity?, limit?)` | 조건으로 협력사 후보 검색, 불량률 낮은 순 |
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
uv run python server.py                    # stdio
uv run python server.py --http 9102        # Streamable HTTP: http://localhost:9102/mcp
uv run python -m pytest -q                 # MCP Client로 도구 호출 테스트
```

- 접속 정보는 `DATABASE_URL` 환경변수로 바꿉니다. 기본값은 `postgresql://agent:agent@localhost:5432/supplier_db`입니다.
- 테스트는 같은 DB 안에 임시 스키마(`pytest_business`)를 만들어 seed한 뒤 지우므로, 작업 중인 데이터는 건드리지 않습니다. DB에 연결할 수 없으면 테스트를 건너뜁니다.

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
docs/ERD.png                ERD
docker-compose.yml          PostgreSQL 16 컨테이너
SETUP.md                    단계별 실행 가이드, 데이터 추가·갱신 방법, 자주 나는 오류
```
