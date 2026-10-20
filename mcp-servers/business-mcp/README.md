# business-mcp

공급사 협상 Agent가 2차 협력사 후보를 찾고 순위를 매길 때 쓰는 **협력사·완성차 기업 DB(PostgreSQL)와 MCP Server**입니다.
법 조문 해석은 다루지 않습니다. `rank_suppliers`의 `notes`에 하도급법·연동제 관련 내용이 나오면 Agent가 law-mcp로 조문을 확인합니다.

처음 실행한다면 단계별 안내인 [SETUP.md](SETUP.md)를 먼저 보세요.

## 데이터 출처

| 데이터 | 출처 |
| --- | --- |
| 기업 개황 (회사명, 지역, 설립연도, 업종, 사업자번호, 직원 수) | **OpenDART 공시** (`collect_dart.py`) |
| 연도별 재무 (매출, 영업이익, 순이익, 자산·부채·자본) | **OpenDART 공시** (사업보고서 주요계정) |
| 생산능력, 품질·납기, 기존 계약, 견적 이력, 인증, RFQ | 가상 데이터 (공시에 없는 기업 내부·거래 데이터) |
| 공시 대상이 아닌 소규모 비상장 협력사 | 가상 데이터 (공시와 같은 형식) |

`suppliers.source`와 `financials.source`가 `DART`인지 `MOCK`인지로 구분한다.
DART 회사의 공정은 공시에 없어서 업종 코드(KSIC 303xx)로 추정한다.

## 실행 순서

```bash
docker compose up -d                  # 1. PostgreSQL 띄우기 (Docker Desktop 필요)
uv sync                               # 2. 패키지 설치
uv run python collect_dart.py         # 3. (선택) DART 새로 수집. 레포 CSV를 쓰면 건너뜀, 인증키 필요
uv run python seed.py                 # 4. 테이블 만들고 데이터 넣기 (다시 실행하면 처음부터 새로 만듦)
uv run python server.py               # 5. MCP 서버 실행 (stdio)
uv run python server.py --http 9102   #    또는 Streamable HTTP: http://localhost:9102/mcp
```

**OpenDART 인증키**는 `collect_dart.py`로 데이터를 새로 수집할 때만 필요하다. `seed.py`와 `server.py`는 키를 쓰지 않는다.
필요하면 https://opendart.fss.or.kr 에서 회원가입 후 인증키를 신청한다.
`.env.example`을 `.env`로 복사하고(`cp .env.example .env`) `DART_API_KEY=` 뒤에 키를 붙여 넣는다. `.env`는 git에 올라가지 않으며 다른 사람과 공유하지 않는다.
수집은 상장사 중 업종 코드 303(자동차 신품 부품 제조업)인 회사를 모으고, 2023~2025 사업보고서 재무를 가져온다.
응답은 `data/dart_raw/`에 캐시되어 중간에 끊겨도 다시 실행하면 이어서 받는다.
팀원 모두 같은 seed 데이터로 작업하도록 수집 결과(`data/dart_companies.csv`, `data/dart_financials.csv`, 자동차 부품 상장사 30곳)를 레포에 포함한다.
공시된 공공데이터라 팀에서 포함하기로 정했다. 그래서 인증키 없이 `collect_dart.py`를 건너뛰고 `seed.py`만 실행해도 모두 같은 DB가 만들어진다.
`collect_dart.py`를 다시 실행하면 두 파일을 덮어쓰므로, 데이터를 바꿀 때만 실행하고 바뀐 CSV를 PR로 올린다.

Docker 없이 PostgreSQL을 직접 설치했다면 DB와 계정만 만든 뒤
`DATABASE_URL` 환경변수로 접속 정보를 바꾸면 된다.
기본값은 `postgresql://agent:agent@localhost:5432/supplier_db`.

## 파일

| 파일 | 내용 |
| --- | --- |
| `schema.sql` | 테이블 8개 + 요약 뷰 `supplier_overview` (최근 영업이익률·부채비율 포함) |
| `collect_dart.py` | OpenDART에서 자동차 부품사 기업 개황·재무·직원 수 수집 → `data/dart_*.csv` |
| `seed.py` | DART 회사를 먼저 넣고 나머지를 가상으로 채워 150곳 구성 (seed 42). `data/`에 CSV도 저장 |
| `docs/ERD.png` | ERD |
| `server.py` | MCP 서버. 도구 4개, stdio 기본, `--http PORT`로 Streamable HTTP |
| `pyproject.toml`, `uv.lock` | 의존성 (fastmcp, psycopg) |
| `docker-compose.yml` | PostgreSQL 16 컨테이너 |

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

## MCP 도구

| 도구 | 하는 일 |
| --- | --- |
| `list_rfqs()` | RFQ 목록 |
| `search_suppliers(process, material, region, iatf_required, min_monthly_capacity)` | 조건으로 후보 검색 (SQL 필터) |
| `get_supplier(supplier_id)` | 한 곳의 상세 정보 |
| `rank_suppliers(rfq_id, w_price, w_quality, w_delivery)` | RFQ 기준 후보 순위와 제외 사유. 점수는 코드로 계산 |

`rank_suppliers`는 결제 기간 60일 초과 계약, 연동 약정 여부, 신규 후보 여부, 최근 영업적자, 부채비율 200% 초과를 `notes`로 함께 알려 준다.

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
