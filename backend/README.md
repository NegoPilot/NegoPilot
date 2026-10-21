# NegoPilot Spring HTTP API

Java 21과 Spring Boot 4.1.1을 사용하는 로컬 동기 API입니다. 입력을 검증한 뒤 저장소의 Python 내부 API를 호출합니다. 새 `/api/v2` 경로는 분해와 Routing 및 Generation을 실행한 전체 Answer를 반환합니다. 선택 기업 문맥과 대화 이력도 같은 Agent 실행에 전달합니다.

기존 `/api/v1` 경로는 분해와 Routing 결과를 반환하는 v0.1 계약 및 fixture 대화의 v0.2 계약을 유지합니다. 생성 답변을 사용하는 클라이언트는 [Generation HTTP 계약 v0.3](../docs/contracts/agent-generation.md)의 `/api/v2` 경로를 사용합니다. 테스트와 `GET /health`는 Python 서버 없이 실행할 수 있습니다. 실제 기능 연결에는 Python 서버가 필요합니다. 사용자 인증과 DB 영구 저장은 제공하지 않습니다.

이 문서의 명령은 저장소 루트에서 실행합니다.

## 버전 선택

2026년 10월 21일에 [Spring 공식 시스템 요구 사항](https://docs.spring.io/spring-boot/system-requirements.html)에서 안정 버전 4.1.1의 Java 21 지원과 Maven 3.6.3 이상 요구 사항을 확인했습니다. 버전은 `backend/pom.xml`에 고정했습니다. [Spring의 동기 RestClient](https://docs.spring.io/spring-framework/reference/integration/rest-clients.html)를 사용합니다. [Spring Boot 기본 JSON 라이브러리](https://docs.spring.io/spring-boot/reference/features/json.html)인 Jackson 3을 사용합니다.

## 빌드와 검사

Java 21과 Maven 3.6.3 이상이 필요합니다. 검증 환경은 Java 21.0.12와 Maven 3.9.16입니다. 최초 빌드에서 Maven Central 의존성을 받습니다.

```bash
java -version
mvn -version
mvn -f backend/pom.xml -B -ntp test
mvn -f backend/pom.xml -B -ntp package
```

실행 파일은 `backend/target/negopilot-backend-0.1.0-SNAPSHOT.jar`입니다. Maven wrapper 대신 위 명령과 고정된 parent 버전으로 빌드를 재현합니다.

테스트는 별도 포트의 로컬 HTTP 스텁과 실제 Spring HTTP 서버를 사용합니다. Python 서버와 모델 API 및 MCP 서버가 없어도 실행됩니다.

## 실행

기능을 연결하려면 [agent 실행 안내](../agent/README.md)에 따라 Python 의존성을 준비하고 내부 HTTP 서버를 `127.0.0.1:9200`에서 실행합니다. live에서는 분해 모델과 Generation 모델 및 MCP 서버 설정이 필요합니다. 모델은 Claude와 Ollama 및 OpenAI 설정을 지원합니다. 설정이나 연결에 문제가 생겨도 stub 답변으로 전환하지 않습니다. 모델 없이 연동할 때는 Python에 `AGENT_EXECUTION_MODE=stub`를 명시합니다. Spring은 응답에 표시된 live 또는 stub 모드를 보존합니다.

두 프로세스에 같은 테스트 전용 키를 환경 변수로 설정합니다. 아래 값은 교체할 예시이며 실제 비밀값이 아닙니다.

```bash
export AGENT_INTERNAL_API_KEY='replace-with-a-local-test-key'
export AGENT_BASE_URL='http://127.0.0.1:9200'
java -jar backend/target/negopilot-backend-0.1.0-SNAPSHOT.jar
```

Maven으로 실행할 수도 있습니다.

```bash
mvn -f backend/pom.xml -B -ntp spring-boot:run
```

기본 주소는 `127.0.0.1:8080`입니다. 내부 키가 비어 있어도 앱과 health는 시작됩니다. Python 호출이 필요한 요청은 내부 키가 없으면 호출 전에 503으로 응답합니다. v2 대화 생성과 조회는 Python을 호출하지 않습니다.

| 환경 변수 | 기본값 | 의미 |
| --- | --- | --- |
| `SERVER_ADDRESS` | `127.0.0.1` | Spring 수신 주소 |
| `SERVER_PORT` | `8080` | Spring 수신 포트 |
| `AGENT_BASE_URL` | `http://127.0.0.1:9200` | Python origin. 경로와 사용자 정보 및 query는 허용하지 않음 |
| `AGENT_INTERNAL_API_KEY` | 빈 값 | Python과 공유하는 내부 키 |
| `AGENT_CONNECT_TIMEOUT_SECONDS` | `2` | 연결 및 연결 풀 대기 제한 시간 |
| `AGENT_RESPONSE_TIMEOUT_SECONDS` | `35` | 응답 및 소켓 읽기 대기 제한 시간 |
| `AGENT_EXECUTION_MODE` | `live` | 기존 v0.2 fixture 경로의 stub 가드. v2 생성 경로에는 이 가드를 적용하지 않음 |

시간 설정은 양의 정수 초 단위입니다. Spring property `agent.connect-timeout`과 `agent.response-timeout`을 직접 설정할 때는 `250ms` 같은 Duration 표기를 사용할 수 있습니다. 공통 계약은 Python 처리 제한 시간의 기본값을 30초로 정의합니다.

## 생성 답변과 대화 API v0.3

| 공개 경로 | 동작 |
| --- | --- |
| `POST /api/v2/agent/queries` | 저장 없이 전체 Agent 실행 |
| `POST /api/v2/conversations` | 메모리 대화 생성. 성공 시 201 |
| `GET /api/v2/conversations/{conversation_id}` | 대화 문맥과 저장 메시지 조회 |
| `POST /api/v2/conversations/{conversation_id}/messages` | 저장 문맥과 이력으로 전체 Agent 실행 |

질의는 Python의 `POST /internal/v2/query-executions`로 전달합니다. query와 history의 원문은 보존합니다. request_id를 생략하면 UUID를 생성하며 history는 빈 배열로 시작합니다. context를 생략하면 선택 기업이 없는 문맥을 사용합니다. 명시적인 null과 정의되지 않은 필드는 거절합니다.

```bash
curl --fail-with-body http://127.0.0.1:8080/api/v2/agent/queries \
  -H 'Content-Type: application/json' \
  -d '{"query":"이 회사의 재무 상태를 알려줘","context":{"selected_supplier_ids":["SUP-0034"]}}'

curl --fail-with-body http://127.0.0.1:8080/api/v2/conversations \
  -H 'Content-Type: application/json' \
  -d '{"context":{"selected_supplier_ids":["SUP-0034"]}}'
```

생성된 conversation_id로 메시지를 보냅니다. 아래 UUID를 실제 응답 값으로 교체합니다.

```bash
curl --fail-with-body http://127.0.0.1:8080/api/v2/conversations/00000000-0000-0000-0000-000000000000/messages \
  -H 'Content-Type: application/json' \
  -d '{"query":"이 회사의 납기와 품질은 어때?"}'
```

선택 기업 ID는 0개 또는 1개이며 `^[A-Za-z0-9_-]{1,100}$` 형식을 따릅니다. Spring은 ID 형식만 확인합니다. 기업 존재 여부와 사실은 Agent의 도구 실행에서 확인합니다. 선택 문맥은 대명사 해석을 돕는 데이터이며 질문에 명시된 다른 기업을 덮어쓰거나 접근 권한을 부여하지 않습니다. v2에는 fixture 수치 비교와 `DEMO_MODE_REQUIRED` 가드를 적용하지 않습니다.

실행 응답은 `schema_version=0.3`이며 decomposition과 routing 및 전체 answer를 포함합니다. decomposition.original_query가 전달한 query와 다르면 502로 거절합니다. Answer의 text와 citations 및 options와 grounded 및 notices와 evidence 및 verification을 보존합니다. 빈 문자열이나 공백만 있는 answer.text는 502로 거절해 대화 이력에 저장하지 않습니다. 정상 본문에는 길이 상한을 적용하거나 원문을 수정하지 않습니다. citation 번호와 evidence ID의 고유성을 확인합니다. 각 citation은 citable인 evidence를 참조해야 하며 label과 source_url이 일치해야 합니다. `verification.masked=true`와 함께 오류 목록이 남은 보정 결과도 정상으로 받습니다. 고정 안내의 grounded가 true이고 citations가 비어 있는 경우도 허용합니다.

| 최종 answer.kind | execution_status | generation_status |
| --- | --- | --- |
| answer | COMPLETED | COMPLETED |
| clarify | NEEDS_INPUT | COMPLETED |
| refusal 또는 no_result | COMPLETED | COMPLETED |
| failed | FAILED | FAILED |

실행 상태는 최종 Answer로 판단합니다. 일부 Routing 오류나 missing_args가 남아 있어도 최종 답변이 만들어지면 COMPLETED입니다. 부분 실패 설명은 routing과 answer.notices에 유지합니다. Answer.kind가 failed이면 분해 실패에는 DECOMPOSITION_FAILED를 사용하고 나머지는 GENERATION_FAILED를 사용합니다. 두 오류의 retryable은 false입니다. HTTP 200에서도 FAILED가 올 수 있습니다.

대화 메시지의 query는 앞뒤 공백을 제거합니다. context를 생략하면 저장 문맥을 사용하며 명시하면 해당 문맥으로 실행합니다. 같은 대화는 한 요청씩 처리하고 다른 대화는 독립적으로 처리합니다. Python에는 이전 저장 메시지 중 최신 20개를 전달합니다. 긴 assistant 본문은 저장할 때 그대로 두고 이력 전송 때만 4000 code point로 제한합니다. 처음 4000 code point가 모두 공백이면 전송할 이력의 앞 공백을 제외한 뒤 다시 제한해 실제 답변 내용을 포함합니다. 저장된 assistant.content는 answer.text이며 response에는 전체 메시지 응답을 보관합니다. 사용자와 assistant를 쌍으로 저장하고 100개를 넘으면 가장 오래된 쌍부터 제거합니다.

COMPLETED와 NEEDS_INPUT이면 메시지 쌍과 새 문맥을 함께 저장합니다. HTTP 오류와 응답 검증 실패 및 HTTP 200의 FAILED에서는 기존 메시지와 문맥을 유지합니다. 대화 저장은 메모리 범위이며 재시작하면 초기화됩니다. v1 대화와 v2 대화는 별도로 보관합니다.

내부 HTTP 인증 실패와 잘못된 응답은 502로 처리합니다. 유효한 Python 400과 503 및 504는 같은 상태의 안전한 오류로 반환하며 유효한 500은 INTERNAL_ERROR로 반환합니다. Routing 진단 문구는 공개 응답 사본에서 아래 v0.1과 같은 고정 문구로 정제합니다. Answer의 모델 출력과 업무 데이터 및 인용은 고정 테스트 문장으로 교체하지 않습니다.

## 호환 질의 API v0.1

```bash
curl --fail http://127.0.0.1:8080/health

curl --fail-with-body http://127.0.0.1:8080/api/v1/agent/queries \
  -H 'Content-Type: application/json' \
  -d '{"query":"하도급법 제4조 보여줘"}'
```

`GET /health`는 `{"status":"ok"}`를 반환합니다. Python이나 모델의 준비 상태를 검사하지 않습니다.

`POST /api/v1/agent/queries`는 [공통 HTTP 계약](../docs/contracts/agent-http.md)을 따릅니다. 정의되지 않은 필드는 중첩 history 항목에서도 거절합니다. JSON 중복 필드와 본문 뒤의 추가 JSON도 거절합니다. query와 history.content는 Unicode code point 기준 1자 이상 4000자 이하입니다. 공백만 있는 문자열은 거절합니다. history는 생략 시 빈 배열이며 최대 20개입니다. role은 `user` 또는 `assistant`입니다. 명시적인 null은 허용하지 않습니다.

request_id를 생략하면 UUID를 생성합니다. 제공한 UUID는 표준 하이픈 형태로 정규화한 뒤 Python에 보냅니다. 내부 키는 `X-Internal-Api-Key` 헤더로만 전송합니다. 자동 재시도와 리다이렉트 추적은 하지 않습니다.

| 계약의 stub 질문 | execution_status |
| --- | --- |
| 하도급법 제4조 보여줘 | `COMPLETED` |
| 그 업체 괜찮아? | `NEEDS_INPUT` |
| ㅁㄴㅇㄹ 그거 저번처럼 해줘 | `FAILED` |

Python v0.1 어댑터는 stub의 다른 질문에 400을 반환합니다. HTTP 200에서 `FAILED`가 나올 수 있습니다. 이 호환 경로의 `COMPLETED`는 분해와 Routing 처리 완료를 뜻합니다. 이 경로는 Generation을 호출하지 않으므로 `generation_status`는 항상 `NOT_IMPLEMENTED`입니다.

## v0.1 응답 검증과 오류 처리

Python HTTP 200의 request_id와 schema_version 및 필수 필드를 검사합니다. 분해 상태와 clarification 및 하위 질문의 관계를 확인합니다. Routing 결과의 ID와 intent를 하위 질문과 대조하고 상태 매핑 우선순위를 검증합니다. 실패 상태의 retryable은 false여야 합니다. 임의 JSON인 성공 도구 결과는 보존합니다. 공용 intent 등록표의 업무 규칙은 Python이 담당합니다.

Routing은 [최신 Routing 계약](../docs/contracts/routing.md)과 [공용 스키마](../shared/src/negopilot_shared/routing.py)를 따릅니다. `routing.schema_version`은 `0.1`이며 각 outcome에는 `referenced_articles` 배열이 포함됩니다. 도구 없는 결과는 `general_knowledge`와 `out_of_scope`로 구분합니다. 각각 개념 설명과 업무 범위 밖 질문을 나타냅니다.

`referenced_articles`는 주요 도구 결과에 붙은 법령 참조를 조회한 결과입니다. 참조 조문 조회가 실패해 해당 항목의 `error`가 채워져도 outcome의 `status`가 `ok`이면 그 오류만으로 HTTP 응답의 `execution_status`를 `FAILED`로 바꾸지 않습니다. Routing outcome의 `status`가 `error`일 때 `ROUTING_FAILED`로 매핑합니다. 참조 조문을 포함한 오류 원문은 사용자 문장으로 사용하지 않습니다.

Spring은 Python HTTP 200의 원본 응답을 검증한 다음 공개 응답 사본의 오류 문구를 정제합니다.

| 정제 대상 | 공개 응답 문구 |
| --- | --- |
| `calls`와 `referenced_articles`의 null이 아닌 `error` | `Tool execution failed.` |
| 각 outcome의 null이 아닌 `message` | `Routing details are unavailable.` |
| routing의 null이 아닌 `halt_reason` | `Routing halted because a dependency returned no results.` |
| 최상위 null이 아닌 `error`의 `message` | 검증된 `code`에 대응하는 고정 문구 |

모든 `result` JSON은 업무 데이터와 인용 원문 및 성공한 부분 결과까지 보존합니다. null과 상태 및 ID와 참조 연결도 유지합니다. 이 정제는 공개 응답 사본에만 적용합니다.

| 조건 | Spring HTTP | code |
| --- | --- | --- |
| 입력 오류 | 400 | `VALIDATION_ERROR` |
| Python의 유효한 400 | 400 | `VALIDATION_ERROR` |
| 연결 실패와 내부 키 미설정 또는 유효한 Python 503 | 503 | `UPSTREAM_UNAVAILABLE` |
| 응답 시간 초과 또는 유효한 Python 504 | 504 | `UPSTREAM_TIMEOUT` |
| 응답 ID 불일치와 잘못된 JSON 및 구조 또는 모순된 상태 | 502 | `UPSTREAM_INVALID_RESPONSE` |
| Python 인증 실패와 알 수 없는 상태 또는 비정상 Content-Type | 502 | `UPSTREAM_INVALID_RESPONSE` |
| 예상하지 못한 Spring 오류 | 500 | `INTERNAL_ERROR` |

Python 오류 envelope도 code와 retryable 및 필수 구조와 ID를 검사합니다. Python 400의 request_id가 null이면 Spring이 부여한 ID로 응답합니다. 전달할 오류 문구는 안전한 고정 문구로 다시 만듭니다. HTTP 오류 원문과 요청 본문 및 키와 예외 스택을 응답하거나 애플리케이션에서 기록하지 않습니다.

자동 테스트는 로컬 HTTP 스텁을 사용합니다. 실제 LLM API와 메일 발송 및 DB seed는 실행하지 않습니다. 실제 Python과의 두 프로세스 검사는 agent 서버를 준비한 뒤 수행합니다. Spring의 v0.1 API는 stub과 live 모두 같은 내부 HTTP 경로를 사용하지만 자동 테스트는 live 모델 실행을 검증하지 않습니다.

## 공급사 조회와 선택 기업 대화 v0.2

[공급사 대화 계약](../docs/contracts/supplier-chat.md)에 따른 fixture 기반 호환 API입니다. 아래 `/api/v1` 경로는 Spring과 Python 양쪽에 `AGENT_EXECUTION_MODE=stub`를 설정해야 사용할 수 있습니다. Spring 설정의 기본값은 live입니다. live에서는 이 fixture API 모두 503 `DEMO_MODE_REQUIRED`와 `retryable=false`를 반환합니다. 기업 미선택 대화 생성과 메모리 대화 조회에도 같은 제한을 적용합니다. `/api/v2` 생성 경로에는 이 제한이 없습니다.

```bash
export AGENT_EXECUTION_MODE=stub
export AGENT_INTERNAL_API_KEY='replace-with-a-local-test-key'
export AGENT_BASE_URL='http://127.0.0.1:9200'
java -jar backend/target/negopilot-backend-0.1.0-SNAPSHOT.jar
```

포트와 접속 제한 및 응답 제한 시간은 기존 설정을 재사용합니다. 브라우저가 보낸 내부 키 헤더는 사용하지 않습니다. Python 호출에는 서버 설정의 키만 넣습니다. Vue 프런트엔드를 연결할 때는 개발 서버의 `/api` 요청을 이 Spring 서버로 프록시하도록 설정합니다.

| 메서드와 경로 | 동작 |
| --- | --- |
| `GET /api/v1/business/catalog` | 공정과 소재 및 지역 목록 |
| `GET /api/v1/suppliers` | 필터에 맞는 기업 목록 |
| `GET /api/v1/suppliers/{supplier_id}` | 기업 상세 |
| `POST /api/v1/conversations` | 메모리 대화 생성. 성공 시 201 |
| `GET /api/v1/conversations/{conversation_id}` | 문맥과 저장 메시지 조회 |
| `POST /api/v1/conversations/{conversation_id}/messages` | 현재 문맥에서 질문 처리 |

```bash
curl --fail http://127.0.0.1:8080/api/v1/business/catalog

curl --fail-with-body --get http://127.0.0.1:8080/api/v1/suppliers \
  --data-urlencode 'process=다이캐스팅' \
  --data-urlencode 'material=ADC12' \
  --data-urlencode 'iatf_required=true' \
  --data-urlencode 'limit=20'

curl --fail http://127.0.0.1:8080/api/v1/suppliers/SUP-0034

curl --fail-with-body http://127.0.0.1:8080/api/v1/conversations \
  -H 'Content-Type: application/json' \
  -d '{"context":{"selected_supplier_ids":["SUP-0034"]}}'
```

생성 응답의 conversation_id로 메시지를 전송합니다. 다음 명령의 UUID를 생성된 값으로 교체합니다.

```bash
curl --fail-with-body http://127.0.0.1:8080/api/v1/conversations/00000000-0000-0000-0000-000000000000/messages \
  -H 'Content-Type: application/json' \
  -d '{"query":"이 회사의 재무 상태를 알려줘"}'
```

검색 문자열과 질문은 앞뒤 공백을 제거한 뒤 검사합니다. 검색 문자열은 최대 100 code point입니다. 질문은 최대 4000 code point입니다. 검색의 알 수 없는 파라미터와 중복 파라미터 및 알 수 없는 JSON 필드는 400입니다. 기업 ID는 `^[A-Za-z0-9_-]{1,100}$`만 허용합니다. 공백과 슬래시 및 한글을 포함한 ID는 400입니다. 대소문자는 그대로 조회합니다. 정상 형식이지만 없는 기업 ID는 404입니다. 대화 ID는 표준 하이픈 UUID 형식을 검사하며 원문 그대로 조회합니다. 대소문자를 바꾼 별칭으로 기존 대화를 조회하지 않습니다.

대화 생성과 명시적인 문맥 변경 시 Python 상세 조회로 기업을 확인합니다. 메시지의 context를 생략하면 저장된 문맥을 사용합니다. context를 명시하면 해당 문맥으로 처리하고 성공 후 저장합니다. 기업을 선택하지 않은 빈 배열도 지원합니다. 기업 미선택과 지원하지 않는 질문은 `NEEDS_INPUT`으로 저장합니다.

같은 대화의 요청은 하나씩 처리합니다. 다른 대화는 독립적으로 처리합니다. Python에는 저장된 메시지 중 최신 20개를 전달하며 현재 질문을 이력에 중복해서 넣지 않습니다. HTTP 오류나 응답 검증 실패 시 메시지와 문맥 모두 유지합니다. 정상 응답은 user와 assistant 메시지를 함께 저장합니다. 100개를 넘으면 가장 오래된 두 메시지부터 제거합니다. 대화는 메모리에만 있으므로 Spring 재시작 시 초기화됩니다.

공통 가상 데이터는 `docs/contracts/fixtures/supplier-chat.json` 하나입니다. Maven이 이 파일을 JAR의 `fixtures/supplier-chat.json`으로 포함합니다. 별도 사본을 소스에 두지 않습니다. 빌드 시 저장소의 디렉터리 구조를 유지해야 합니다.

Spring은 조회 결과와 기업 카드 수치를 fixture와 비교합니다. Chat 응답의 요청 및 대화 ID와 문맥 및 상태와 카드 및 근거 관계도 검사합니다. 설명문의 정확한 표현 규칙은 계약에 없으므로 문장 의미 전체는 검증하지 않습니다. v0.2 계약의 설명 응답은 고정 템플릿이며 실제 모델이나 MCP 실행 결과로 표시하지 않습니다. 모든 성공 응답에 `mode=stub`를 표시합니다. 납기 비율 96.5와 부채비율 105.0은 각각 96.5%와 105%이며 Spring에서 100을 곱하지 않습니다.

통합 검사에 사용할 JAR을 먼저 빌드합니다. 해당 JAR로 두 프로세스 검사를 진행하는 동안에는 JAR을 다시 빌드하지 않습니다.
