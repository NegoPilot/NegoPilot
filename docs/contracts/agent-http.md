# Spring과 Python 내부 HTTP 계약 v0.1

기준 main 커밋은 `8d48d7b2b200c88f0ccfe33c3177057811e38cae`입니다. 이 문서는 Spring의 공개 API와 후속 Python HTTP 어댑터 사이의 요청과 응답을 정의합니다. 이번 PR에는 Spring 백엔드와 HTTP 계약 및 가상 데이터만 포함합니다. Python HTTP 어댑터는 포함하지 않습니다. 내부 경로가 이 PR만으로 실행된다는 의미가 아닙니다.

분해와 Routing은 기존 공용 스키마를 사용합니다. Routing의 기준은 [공용 모델](../../shared/src/negopilot_shared/routing.py)과 [Routing 계약](routing.md)입니다. 이 문서는 공용 모델이나 Router의 의미를 변경하지 않습니다. 이전 로컬 통합의 완료 기록이나 검사 수치는 이 기준 커밋의 저장소 기능 완료를 뜻하지 않습니다.

## HTTP 연동 흐름

`POST /api/v1/agent/queries` → Spring → `POST /internal/v1/query-executions` → Python의 QueryDecomposer와 Router → 구조화된 실행 결과

동기 요청입니다. 접수 후 응답을 유지하고 결과를 반환합니다. 별도 실행 큐와 DB 저장 및 조회 API는 이번 계약에 없습니다. `request_id`는 요청 추적용이며 중복 실행 방지 키가 아닙니다. 자동 재시도하지 않습니다.

기준 main에는 이미 Generation과 [전체 그래프](../../agent/src/negopilot_agent/graph.py)가 있습니다. 이 HTTP 계약은 분해와 Routing 결과까지만 반환하므로 `generation_status: NOT_IMPLEMENTED`를 표시합니다. 이는 이 HTTP 응답에서 생성 답변을 제공하지 않는다는 뜻입니다. 코어 Generation의 구현 상태를 나타내지 않습니다. `COMPLETED`는 분해와 Routing 단계의 처리 완료를 뜻합니다. Generation의 Answer나 그래프 실행 결과를 이 envelope에 추가하지 않습니다.

## 요청

JSON 필드 이름은 snake_case입니다. 정의되지 않은 요청 필드는 거절합니다.

| 필드 | 규칙 |
| --- | --- |
| request_id | 내부 API에서 필수인 UUID. Spring 공개 API에서는 생략 가능하며 Spring이 생성 |
| query | 공백만 있는 값 금지. 길이 1부터 4000 |
| history | 생략하면 빈 배열. 최대 20개. 각 항목은 role과 content만 포함 |
| history.role | user 또는 assistant |
| history.content | 공백만 있는 값 금지. 길이 1부터 4000 |

Spring은 입력을 검증한 뒤 같은 request_id를 내부 API에 전달합니다. 후속 Python HTTP 어댑터는 요청에 전달받은 이력만 사용해야 합니다. 이 계약은 대화 저장이나 체크포인트 재개를 제공하지 않습니다.

## 정상 처리 응답

HTTP 200 응답은 다음 필드를 모두 포함합니다.

| 필드 | 규칙 |
| --- | --- |
| schema_version | 문자열 0.1 |
| request_id | 요청과 같은 UUID |
| execution_status | COMPLETED 또는 NEEDS_INPUT 또는 FAILED |
| generation_status | NOT_IMPLEMENTED |
| mode | live 또는 stub |
| decomposition | 기존 DecompositionResult를 JSON으로 반환. alias 사용 |
| routing | 최신 shared RouteResult를 JSON으로 반환. 자체 schema_version도 포함 |
| error | 정상과 추가 입력 대기에서는 null. 처리 실패에서는 아래 오류 객체 |

상태 매핑 순서는 다음과 같습니다.

1. 분해가 failed이면 FAILED와 DECOMPOSITION_FAILED 오류를 반환합니다. routing은 not_routed입니다.
2. 분해가 clarify이면 NEEDS_INPUT을 반환합니다. routing은 not_routed입니다.
3. Routing outcome 중 error가 있으면 FAILED와 ROUTING_FAILED 오류를 반환합니다. 부분 결과는 보존합니다.
4. Routing outcome 중 missing_args가 있으면 NEEDS_INPUT을 반환합니다.
5. 그 밖에는 COMPLETED입니다. general_knowledge와 out_of_scope 및 unsupported와 empty와 skipped의 상세 상태는 routing에 그대로 남깁니다. 도구 실행 성공이나 최종 답변 생성 성공으로 해석하면 안 됩니다.

### Routing 직렬화와 상태 의미

HTTP 응답의 routing은 공용 모델을 기본 필드까지 포함해 직렬화한 객체입니다. envelope의 schema_version과 routing.schema_version은 별도 필드이며 현재 값은 모두 `"0.1"`입니다.

| 필드 | HTTP 직렬화 규칙 |
| --- | --- |
| routing.schema_version | 필수. `"0.1"` |
| routing.status | routed 또는 not_routed |
| routing.outcomes | 분해 sub_queries와 같은 순서. id와 intent도 대응해야 함 |
| routing.halted와 halt_reason | 중단 시 true와 이유 문자열. 중단하지 않으면 false와 null |
| outcome.status | ok와 general_knowledge 및 out_of_scope와 unsupported 및 missing_args와 empty 및 skipped와 error |
| outcome.calls | 본래 하위 질문을 처리한 ToolCall 배열 |
| outcome.referenced_articles | 결과의 law_refs를 조회한 ToolCall 배열. 조회하지 않았으면 빈 배열 |
| outcome.missing | missing_args일 때만 한 개 이상의 누락 필드. 다른 상태에서는 빈 배열 |
| outcome.message | null 또는 안전한 설명 문자열. UI에 개발용 내용을 그대로 표시하지 않음 |

공용 모델의 일관성 규칙을 유지합니다. routed에는 outcome이 한 개 이상 있어야 하고 not_routed에는 outcome이 없어야 합니다. outcome의 id는 중복될 수 없습니다. ok와 error에는 calls가 한 개 이상 있어야 합니다. halted가 true이면 halt_reason과 empty outcome이 있어야 합니다.

참조 조문은 별도 새 결과 형식이 아닌 기존 ToolCall 형식을 사용합니다. Router는 결과의 law_refs를 최대 5개까지 조회해 referenced_articles에 담습니다. 참조 조문 조회가 실패해도 본래 outcome의 ok를 error로 바꾸지 않습니다. 해당 참조 ToolCall의 error만 채웁니다. 따라서 referenced_articles의 오류만으로 execution_status를 FAILED로 바꾸거나 ROUTING_FAILED를 만들지 않습니다. 다른 outcome이 모두 완료 상태라면 실행은 COMPLETED를 유지합니다.

도구를 사용하지 않는 상태는 general_knowledge와 out_of_scope입니다. 이전 no_tool 값은 이 계약에서 받지 않습니다. 두 상태를 서로 합치거나 생성 답변 완료로 해석하지 않습니다.

DECOMPOSITION_FAILED와 ROUTING_FAILED의 retryable은 false입니다. 기존 Router가 오류 원인을 구조화하지 않으므로 문자열로 재시도 가능 여부를 추정하지 않습니다.

후속 Python HTTP 어댑터는 오류 원문을 HTTP 응답으로 보내기 전에 정제해야 합니다. Spring도 upstream의 원본 구조를 검증한 다음 공개 응답 사본을 정제합니다. 앞 단계가 정제했다고 가정해 원문을 그대로 전달하지 않습니다.

| 정제 대상 | 공개 응답 규칙 |
| --- | --- |
| outcomes의 calls와 referenced_articles 안의 non-null error | `Tool execution failed.` |
| outcome의 non-null message | `Routing details are unavailable.` |
| routing의 non-null halt_reason | `Routing halted because a dependency returned no results.` |
| 최상위 DECOMPOSITION_FAILED 오류의 message | `Query decomposition failed.` |
| 최상위 ROUTING_FAILED 오류의 message | `Query routing failed.` |

null과 상태 및 ID와 근거 연결은 유지합니다. 참조 조문 오류를 정제할 때도 outcome의 status는 바꾸지 않습니다. result 안의 업무 데이터와 인용 원문 및 성공한 부분 결과는 그대로 보존합니다. 이 규칙은 HTTP 응답 사본에 적용합니다. 공용 ToolCall이나 Router의 내부 오류 저장 방식과 fixture 원본을 바꾸지 않습니다. 비밀값이나 원래 예외 메시지를 공개 응답이나 로그로 내보내지 않습니다.

Spring은 분해 상태와 clarification 및 sub_queries의 조합을 확인합니다. 이어 routing.schema_version과 공용 모델의 필드 및 일관성 규칙을 검사합니다. 분해와 Routing의 id와 intent 및 순서 대응도 확인합니다. routing.status와 execution_status는 위 매핑과 일치해야 합니다. JSON Schema로 표현하지 못하는 ID 고유성과 객체 사이의 대응은 실행 시 검사합니다. JSON 필드가 존재한다는 사실만으로 정상 응답으로 받아들이지 않습니다. 모순된 upstream 응답은 502로 처리합니다.

## HTTP 오류

오류 응답 형식은 `{request_id: UUID 또는 null, error: {code: 문자열, message: 문자열, retryable: 불리언}}`입니다. 요청 본문과 비밀값 및 상세 스택을 응답이나 로그에 출력하지 않습니다.

| 상태 코드 | code | 의미 |
| --- | --- | --- |
| 400 | VALIDATION_ERROR | 요청 형식 오류. request_id를 해석하지 못하면 null |
| 401 | UNAUTHORIZED | Python 내부 API 인증 실패 |
| 503 | UPSTREAM_UNAVAILABLE | 모델 설정 또는 모델 연결 실패. Spring에서는 Python 연결 실패 |
| 504 | UPSTREAM_TIMEOUT | 처리 제한 시간 초과 |
| 502 | UPSTREAM_INVALID_RESPONSE | Spring에서만 사용. Python의 응답 형식이나 요청 ID 불일치. 내부 인증 실패도 공개 응답에서는 이 오류로 매핑 |
| 500 | INTERNAL_ERROR | 예상하지 못한 서버 오류 |

검증 오류와 인증 오류 및 응답 형식 오류는 retryable=false입니다. 시간 초과와 연결 실패는 retryable=true입니다. 이는 자동 재시도 허용을 뜻하지 않습니다.

## 설정

후속 Python HTTP 어댑터의 연동 주소는 `127.0.0.1:9200`입니다. Spring은 `127.0.0.1:8080`을 기본 주소로 사용합니다. 양쪽 HTTP 서비스는 `GET /health`에서 `{status: "ok"}`를 반환하는 계약입니다. health는 외부 의존 서비스의 준비 완료를 보장하지 않습니다. 이번 PR에 Python 서버의 기동 진입점은 포함하지 않습니다.

Python의 `AGENT_INTERNAL_API_KEY`와 Spring의 `AGENT_INTERNAL_API_KEY`에 같은 값을 설정합니다. Spring은 `X-Internal-Api-Key` 헤더로만 이 값을 전달합니다. Python 내부 요청에는 키 검증을 적용합니다. 키가 없는 상태에서는 실행 API를 사용할 수 없습니다. health는 인증 없이 확인할 수 있습니다. 예제에 실제 키를 넣지 않습니다.

Spring의 `AGENT_BASE_URL` 기본값은 `http://127.0.0.1:9200`입니다. 연결 제한 시간 기본값은 2초입니다. 응답 제한 시간 기본값은 35초입니다. Python의 `AGENT_REQUEST_TIMEOUT_SECONDS` 기본값은 30초입니다. 설정으로 변경할 수 있게 합니다.

## 후속 어댑터의 실행 모드와 검증 조건

이번 PR의 백엔드 검증은 가짜 Python HTTP 응답으로 요청 전달과 응답 검증을 확인합니다. 실제 모델과 MCP 및 DB를 호출하지 않습니다. 아래 모드는 후속 Python HTTP 어댑터가 제공할 연동 계약입니다. 이 문서에 모드가 정의돼 있다는 사실은 Python 서버 구현이나 두 프로세스 통합 검증이 이 PR에 포함됐다는 의미가 아닙니다.

`AGENT_EXECUTION_MODE=stub`에서는 모델과 MCP에 접속하지 않아야 합니다. 아래 기존 분해 fixture와 테스트용 ToolCaller를 사용할 때도 Router 자체는 최신 공용 계약을 따르는 실제 코드로 연결합니다. 법령 결과는 테스트용 데이터라고 표시합니다.

| 질문 | fixture | 기대 상태 |
| --- | --- | --- |
| 하도급법 제4조 보여줘 | 01_law_article.json | COMPLETED |
| 그 업체 괜찮아? | 09_clarify.json | NEEDS_INPUT |
| ㅁㄴㅇㄹ 그거 저번처럼 해줘 | 11_failed.json | FAILED |

stub에서 다른 질문은 400 VALIDATION_ERROR로 거절하고 지원하는 테스트 질문을 문서로 안내합니다. 테스트용 질문을 일반 질문 처리 능력으로 설명하지 않습니다.

`AGENT_EXECUTION_MODE=live`는 기존 QueryDecomposer와 Router 및 McpToolCaller를 연결할 모드입니다. 기존 Claude 또는 Ollama 설정을 재사용하며 이 envelope에서는 Generation을 실행하지 않습니다. 기본 모드는 live로 두고 stub 사용은 명시적으로 켜게 합니다. 실제 모델 연결이나 전체 그래프의 HTTP 노출은 이 PR의 검증 범위가 아닙니다.

HTTP 명세의 Routing 호환성은 `shared/fixtures/routing/`의 최신 예제를 읽어 검증합니다. 저장된 예제를 검증하는 과정은 모델이나 MCP를 다시 호출하지 않습니다. 공용 Routing 모델과 fixture 자체를 HTTP 명세에 맞춰 수정하지 않습니다.

이번 PR은 로그인과 승인 및 DB 저장과 메일 발송을 구현하지 않습니다. 공개 API라는 표현은 클라이언트가 호출할 경계를 뜻하며 사용자 인증이 구현됐다는 의미가 아닙니다.
