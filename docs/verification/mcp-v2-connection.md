# MCP v2 연결 검증

검증일은 2026-10-22다. 기준 코드는 main의 `928abce`다. 기존 `feat/32-spring-http-backend` 브랜치에서 연결 누락을 수정했다. 작업 중 추가로 머지된 MCP 연결 풀과 캐시 변경도 반영하고 해당 테스트와 Docker 검증을 다시 실행했다.

## 변경 범위

- Agent가 `energy_ratio`와 `as_of`를 `check_offer`에 전달한다. 비중 `0`도 유지한다.
- 근거 조문은 MCP 응답의 유효한 `basis.as_of`로 조회한다. 이전 응답에 기준일이 없으면 요청의 `as_of`를 사용한다.
- 같은 법령과 조문도 기준일이 다르면 따로 조회한다. 같은 날짜의 항별 중복 조회는 제거한다.
- 공개 오류 코드 4개를 HTTP 응답에 전달한다. Spring은 기존 5필드 응답도 계속 받는다.
- 오류 코드가 없는 응답은 기존 형태로 직렬화한다. 내부 코드와 진단 원문은 공개하지 않는다.
- OpenAPI와 프런트 API 타입을 갱신했다. 화면 기능은 변경하지 않았다.

## 자동 테스트

실제 모델을 호출하지 않는 테스트를 별도 PostgreSQL에서 실행했다. 실행 중인 로컬 DB를 테스트 초기화에 사용하지 않았다.

| 대상 | 결과 |
| --- | --- |
| shared 전체 | 142개 통과 |
| Agent 전체 | 1257개 통과 |
| business-mcp 전체 | 124개 통과 |
| law-mcp 전체 | 63개 통과 |
| Spring 전체 | 623개 통과. 실패와 오류 및 스킵 없음 |
| 통합 테스트 도우미 | 45개 통과 |
| Docker Agent 시작 설정 | 10개 통과 |
| Compose 설정 | 7개 통과 |
| 프런트 | 타입 검사와 빌드 및 API 회귀 검사 통과 |

자동 테스트 합계는 2271개다. Spring은 이전 빌드 보고서가 섞이지 않도록 `clean test`로 다시 확인했다. Compose 설정 테스트는 Docker CI의 환경변수를 지정해서 실행해야 한다.

주요 실행 명령은 다음과 같다. Spring의 `TEST_DATABASE_URL`과 사용자 및 비밀번호는 별도 테스트 DB를 지정한다. business-mcp의 `DATABASE_URL`도 같은 원칙을 따른다.

```bash
(cd shared && uv run --locked python -m pytest -q)
(cd agent && uv run --locked python -m pytest -q)
(cd mcp-servers/business-mcp && uv run --locked python -m pytest -q)
(cd mcp-servers/law-mcp && uv run --locked python -m pytest -q)
(cd backend && mvn -B -ntp clean test)
python3 -B -m unittest discover -s tests/integration -p 'test_*.py'
(cd agent && uv run --locked python -B -m unittest discover -s ../deploy/local -p 'test_*.py')
COMPOSE_FILE=compose.yaml:compose.test.yaml COMPOSE_PROJECT_NAME=negopilot-ci TEST_WEB_PORT=5173 \
  python3 -B -m unittest discover -s tests/integration -p 'compose_config_test.py'
(cd frontend && npm run build && npm run check:api)
```

## 실제 Docker와 AI 검증

`negopilot-local` 프로젝트를 같은 `.env`와 named volume으로 재빌드했다. Spring과 Python은 모두 live 모드다. 질문 해석과 답변 생성은 OpenAI `gpt-4.1-mini`를 사용했다.

`SUP-0032`와 `RFQ-2026-003`을 대상으로 단가 1500원과 지급기한 75일 및 계약 기간 365일을 지정했다. 원재료 비중은 5%이고 에너지 비중은 20%다. 가격 연동 약정은 없는 조건으로 질문했다.

| 검증 | 확인 결과 |
| --- | --- |
| 계약 기준일 `2026-09-01` | 도구 인자에 `energy_ratio=0.2`와 기준일이 전달됐다. 응답은 도구 스키마 `2`였다. 에너지 규칙의 검사 결과가 포함됐다. 근거 조문은 `2026-08-11` 시행판을 반환했다 |
| 후속 질문으로 기준일만 `2026-07-01`로 변경 | 이전 조건과 에너지 비중을 유지했다. 해당 에너지 규칙의 시행 전이므로 그 검사 결과는 포함되지 않았다. 조회한 하도급법 근거는 `2025-12-17` 시행판이었다 |
| 없는 RFQ `RFQ-2026-999` | Spring 응답의 `error_code`가 `RFQ_NOT_FOUND`였다. 오류 원문은 고정 문구로 정제됐다. 답변은 RFQ 번호 확인을 안내했다 |

최종 코드에서 세 응답 모두 live 모드였고 전체 실행과 답변 생성 상태는 `COMPLETED`였다. 최종 테스트 대화에 메시지 6개가 저장됐다. 추가 main 반영 전의 검증도 포함하면 테스트 대화 2개와 메시지 12개를 추가했다. 공개 오류 코드가 있는 도구 실패도 사용자 안내를 생성해 전체 실행이 완료되면 기존 저장 정책을 따른다. 전체 실행 자체가 `FAILED`인 응답의 저장 정책은 변경하지 않았다.

## 데이터 보존

- 재빌드 전에 애플리케이션 DB와 business DB를 `pg_dump`로 백업했다. 역할과 법령 volume도 별도로 백업했다.
- 기존 회원 2명과 대화 2개 및 메시지 8개의 원본 행이 유지됐다. business DB의 원본 행도 유지됐다.
- 재빌드 전 발급한 로그인 세션으로 재빌드 후 인증과 기존 대화 조회에 성공했다. 기존 메시지와 전체 응답의 해시도 일치했다.
- 검증 후 회원은 2명이다. 검증용 대화 2개가 추가되어 대화는 4개이고 메시지는 20개다.
- DB와 법령 volume 및 `.env`를 유지했다. 공유 서버 컨테이너는 교체하지 않았다.
- DB 백업과 세션 쿠키 및 실제 응답 원문은 개인 로컬 경로에 보관했다. Git에는 포함하지 않았다.

## 확인 범위

`/review` 화면의 별도 검사 API 연결은 포함하지 않는다. 이번 검증은 채팅을 통한 실제 Agent 실행 경로를 확인했다. 법령 색인의 최초 시행판보다 이른 날짜를 처리하는 기존 law-mcp 동작은 변경하지 않았으며 해당 범위의 정확성은 검증하지 않았다.
