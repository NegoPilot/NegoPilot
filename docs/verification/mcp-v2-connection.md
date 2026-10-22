# 공개 오류 코드 전달 검증

이번 PR은 MCP 공개 오류 코드가 Agent HTTP 응답과 Spring 응답 및 저장 대상 대화 JSON에 전달되도록 연결한다. 기존 `feat/32-spring-http-backend` 브랜치에 main의 `bc29117`까지 반영했다. 공개 오류 코드 전달로 변경 범위를 제한한다.

## 변경 범위

- shared `ToolCall.error_code`가 공개 오류 코드 4개를 선택 필드로 직렬화한다. 허용 코드는 `RFQ_NOT_FOUND`와 `SUPPLIER_NOT_FOUND` 및 `SUPPLIER_AMBIGUOUS`와 `VALUE_OUT_OF_RANGE`다.
- Spring은 기존 5필드 응답과 공개 코드가 추가된 응답을 모두 받는다. 명시적인 `null`도 허용한다. 잘못된 코드와 자료형은 거절한다.
- 오류 코드가 없으면 Agent는 기존 형태로 직렬화한다. 공개 코드는 `error`가 있는 실패 호출에만 포함한다. 내부 코드와 진단 원문은 공개하지 않는다.
- 정상 저장 대상 대화는 전체 응답 JSON에 공개 코드도 보존한다. 기존 대화 JSON은 변환하지 않는다. 전체 실행이 `FAILED`인 응답의 기존 저장 정책은 유지한다.
- OpenAPI와 프런트 API 타입 및 관련 테스트를 갱신한다.

Agent 라우팅은 main에 머지된 [PR #142](https://github.com/NegoPilot/NegoPilot/pull/142)의 구현을 그대로 사용한다. `energy_ratio`와 `as_of` 전달 및 기준일별 조문 조회와 근거 항 우선 인용은 해당 PR의 범위다. 이번 PR에 있던 중복 라우팅 구현과 관련 테스트를 제거했다. `basis.as_of`를 우선 사용하던 추가 처리도 이번 PR에서 제외했다.

## 최신 main 병합 후 검증

2026-10-22에 main의 `bc29117`을 반영했다. #150에서 삭제한 v1 명세 `agent-http.openapi.json`은 삭제를 유지했다. 현재 사용하는 `agent-generation.openapi.json`의 공개 오류 코드 선택 필드와 오류 동반 검증은 유지했다. Agent 라우팅 파일은 main과 동일하다.

| 대상 | 결과 |
| --- | --- |
| shared 전체 | 147개 통과 |
| Agent 전체 | 908개 통과 |
| Spring 전체 | 별도 PostgreSQL에서 `clean test` 623개 통과. 실패와 오류 및 스킵 없음 |
| business-mcp 전체 | 별도 PostgreSQL에서 131개 통과 |
| 프런트 | 타입 검사와 빌드 및 API 회귀 검사 통과 |

자동 테스트 합계는 1809개다. 이전 검증보다 Agent 테스트 수가 줄어든 것은 main에서 v1 경로와 해당 테스트를 제거했기 때문이다. 이 병합 검증에서는 기존 로컬 서비스와 DB를 재기동하거나 변경하지 않았다. 아래 실제 AI 및 데이터 보존 기록은 앞선 `11bdfa1` 통합 시점의 결과다.

## 범위 조정 후 검증

검증일은 2026-10-22다. 최신 main의 `11bdfa1`을 병합하고 중복 라우팅을 제거한 코드에서 실행했다. `router.py`와 `test_routing.py`가 main과 동일한 것도 확인했다.

| 대상 | 실행 결과 |
| --- | --- |
| shared 전체 | 공개 코드 직렬화와 기존 응답 호환성을 포함해 144개 통과 |
| Agent 전체 | 직접 질문과 Generation HTTP 계약 및 main 라우팅을 포함해 1256개 통과 |
| Spring 전체 | 별도 PostgreSQL에서 `clean test` 623개 통과. 실패와 오류 및 스킵 없음 |
| OpenAPI와 프런트 API 타입 | Agent HTTP 계약 검사와 프런트 타입 검사 및 빌드와 API 회귀 검사 통과 |
| Docker 재빌드 | `negopilot-local`을 기존 설정과 named volume으로 재실행. 서비스 정상 상태 확인 |
| 실제 AI 오류 코드 전달 | 없는 RFQ 질문의 `RFQ_NOT_FOUND`가 Spring HTTP 응답에 포함됨. 오류 원문은 `Tool execution failed.`로 정제됨 |
| 대화 저장 | 전체 실행과 답변 생성 상태는 `COMPLETED`. 저장 후 조회한 전체 응답이 최초 응답과 일치함 |
| 기존 데이터 보존 | 기존 회원 2명과 대화 4개 및 메시지 34개의 원본 행 보존. 기존 로그인 세션과 메시지 응답 해시도 확인 |

자동 테스트는 총 2023개가 통과했다. 실제 모델 검증은 OpenAI `gpt-4.1-mini`로 수행했다. 검증용 대화 1개와 메시지 2개를 추가해 최종 대화는 5개이고 메시지는 36개다. DB와 법령 volume 및 개인 설정을 유지했고 공유 서버 컨테이너는 변경하지 않았다.

기존 데이터는 재빌드 전에 백업했다. 백업과 로그인 쿠키 및 실제 응답 원문은 개인 로컬 경로에 보관하며 Git에 포함하지 않는다.

## 이전 통합 검증

아래는 범위를 조정하기 전인 2026-10-22의 검증 기록이다. 당시 기준 코드는 main의 `928abce`였다. 이후 최종 범위에서 제외한 Agent 라우팅 변경도 포함해 검증했다. MCP 연결 풀과 캐시 변경을 반영한 뒤 해당 테스트와 Docker 검증을 다시 실행한 기록을 함께 보존한다. 이 결과는 범위 조정 후 코드의 재검증 결과가 아니다.

### 자동 테스트

당시 실제 모델을 호출하지 않는 테스트를 별도 PostgreSQL에서 실행했다. 실행 중인 로컬 DB를 테스트 초기화에 사용하지 않았다.

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

당시 자동 테스트 합계는 2271개였다. Spring은 이전 빌드 보고서가 섞이지 않도록 `clean test`로 다시 확인했다. Compose 설정 테스트는 Docker CI의 환경변수를 지정해서 실행했다.

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

### 실제 Docker와 AI 검증

`negopilot-local` 프로젝트를 같은 `.env`와 named volume으로 재빌드했다. Spring과 Python은 모두 live 모드다. 질문 해석과 답변 생성은 OpenAI `gpt-4.1-mini`를 사용했다.

`SUP-0032`와 `RFQ-2026-003`을 대상으로 단가 1500원과 지급기한 75일 및 계약 기간 365일을 지정했다. 원재료 비중은 5%이고 에너지 비중은 20%다. 가격 연동 약정은 없는 조건으로 질문했다.

| 검증 | 확인 결과 |
| --- | --- |
| 계약 기준일 `2026-09-01` | 도구 인자에 `energy_ratio=0.2`와 기준일이 전달됐다. 응답은 도구 스키마 `2`였다. 에너지 규칙의 검사 결과가 포함됐다. 근거 조문은 `2026-08-11` 시행판을 반환했다 |
| 후속 질문으로 기준일만 `2026-07-01`로 변경 | 이전 조건과 에너지 비중을 유지했다. 해당 에너지 규칙의 시행 전이므로 그 검사 결과는 포함되지 않았다. 조회한 하도급법 근거는 `2025-12-17` 시행판이었다 |
| 없는 RFQ `RFQ-2026-999` | Spring 응답의 `error_code`가 `RFQ_NOT_FOUND`였다. 오류 원문은 고정 문구로 정제됐다. 답변은 RFQ 번호 확인을 안내했다 |

당시 검증한 세 응답 모두 live 모드였고 전체 실행과 답변 생성 상태는 `COMPLETED`였다. 마지막 테스트 대화에 메시지 6개가 저장됐다. 그에 앞선 main 반영 전의 검증도 포함하면 테스트 대화 2개와 메시지 12개를 추가했다. 공개 오류 코드가 있는 도구 실패도 사용자 안내를 생성해 전체 실행이 완료되면 기존 저장 정책을 따랐다. 전체 실행 자체가 `FAILED`인 응답의 저장 정책은 변경하지 않았다.

### 데이터 보존

- 재빌드 전에 애플리케이션 DB와 business DB를 `pg_dump`로 백업했다. 역할과 법령 volume도 별도로 백업했다.
- 기존 회원 2명과 대화 2개 및 메시지 8개의 원본 행이 유지됐다. business DB의 원본 행도 유지됐다.
- 재빌드 전 발급한 로그인 세션으로 재빌드 후 인증과 기존 대화 조회에 성공했다. 기존 메시지와 전체 응답의 해시도 일치했다.
- 당시 검증 후 회원은 2명이었다. 검증용 대화 2개가 추가되어 대화는 4개였고 메시지는 20개였다.
- DB와 법령 volume 및 `.env`를 유지했다. 공유 서버 컨테이너는 교체하지 않았다.
- DB 백업과 세션 쿠키 및 실제 응답 원문은 개인 로컬 경로에 보관했다. Git에는 포함하지 않았다.

### 당시 확인 범위

`/review` 화면의 별도 검사 API 연결은 포함하지 않았다. 당시 검증은 채팅을 통한 실제 Agent 실행 경로를 확인했다. 법령 색인의 최초 시행판보다 이른 날짜를 처리하는 기존 law-mcp 동작은 변경하지 않았으며 해당 범위의 정확성은 검증하지 않았다.
