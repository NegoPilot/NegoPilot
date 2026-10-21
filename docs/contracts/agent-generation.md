# 최신 Agent Generation HTTP 계약 v0.3

기준 main은 `4cad84287086f83f5a858ee9084f005557b81bff`이다. Spring과 Python 사이에서 최신 `build_graph`의 분해와 Routing 및 Generation을 한 번 실행한다. 기존 v0.1 질의 실행과 v0.2 fixture 대화 경로는 호환성 검사용으로 유지한다. 생성 답변을 사용할 클라이언트는 아래 v2 공개 경로를 호출한다. 기본 실행 모드는 live이며 실패할 때 stub으로 전환하지 않는다.

정적 스키마는 [OpenAPI v0.3](agent-generation.openapi.json)에 있다.

기업 선택 없이 채팅에서 질문하는 흐름과 필요한 정보만 추가로 묻는 기준은 [직접 질문 계약](direct-chat.md)에 있다. 기존 schema 0.3의 요청과 응답을 그대로 사용한다.

## 경로와 요청

| 경로 | 역할 |
| --- | --- |
| POST /api/v2/agent/queries | Spring의 상태 없는 생성 답변 API |
| POST /internal/v2/query-executions | Python의 전체 Agent 실행 |
| POST /api/v2/conversations | Spring 메모리 대화 생성 |
| GET /api/v2/conversations/{conversation_id} | 대화 문맥과 저장 메시지 조회 |
| POST /api/v2/conversations/{conversation_id}/messages | 저장 문맥과 이력으로 생성 답변 요청 |

질의 요청은 `request_id`와 `query` 및 `history`와 `context`다. 내부 API의 request_id는 필수 UUID다. 공개 질의 API에서는 생략하면 Spring이 만든다. query는 1..4000 code point이며 공백만 있는 문자열은 거절한다. 원문은 보존한다. history는 생략 시 빈 배열이며 최대 20개다. 항목은 user 또는 assistant role과 1..4000자의 content만 갖는다. context는 생략 시 `{selected_supplier_ids:[]}`다. 응답의 decomposition.original_query는 전달된 query와 일치해야 한다. 정의되지 않은 필드와 명시적인 null은 거절한다.

context의 selected_supplier_ids는 0..1개 문자열이며 `^[A-Za-z0-9_-]{1,100}$`를 따른다. 기업 사실을 context로 전달하지 않는다. 원래 질문이나 저장 이력을 재작성하지 않고 graph의 context에서 분해 프롬프트의 별도 JSON 데이터 영역으로 전달한다. 선택 기업은 대명사를 해석하는 보조 데이터다. 질문에 명시된 다른 기업을 선택값으로 덮어쓰지 않는다. 기업 존재 여부와 사실은 실제 도구 실행 결과로 확인한다. 선택 문맥을 접근 권한으로 해석하지 않는다.

내부 경로는 기존 X-Internal-Api-Key 인증을 사용한다. 전체 그래프에 하나의 처리 제한 시간을 적용한다. 모델과 MCP 연결 실패 및 timeout을 숨기거나 테스트 답변으로 대체하지 않는다.

## 실행 응답

HTTP 200은 다음 필드를 모두 포함한다.

| 필드 | 값 |
| --- | --- |
| schema_version | "0.3" |
| request_id | 요청의 UUID |
| mode | live 또는 stub |
| execution_status | COMPLETED 또는 NEEDS_INPUT 또는 FAILED |
| generation_status | COMPLETED 또는 FAILED |
| context | 요청에서 확정한 Context |
| decomposition | 공용 DecompositionResult의 전체 JSON |
| routing | 공용 RouteResult의 전체 JSON |
| answer | 최신 Generation Answer의 전체 JSON |
| error | null 또는 code와 message 및 retryable |

Answer는 kind와 text 및 citations와 options 및 grounded와 notices 및 evidence와 verification을 포함한다. 코어 Answer를 직렬화하며 verification.ok 같은 Python property를 추가하지 않는다. Answer의 근거와 인용 및 검증 정보를 화면이 사용할 수 있게 그대로 보존한다. HTTP 응답의 answer.text는 비어 있거나 공백만일 수 없다. 이런 결과는 내부 오류로 처리하고 대화에 저장하지 않는다. schema와 mode 및 Answer를 포함한 필드는 생략하지 않는다.

| answer.kind | execution_status | generation_status | error |
| --- | --- | --- | --- |
| answer | COMPLETED | COMPLETED | null |
| clarify | NEEDS_INPUT | COMPLETED | null |
| refusal 또는 no_result | COMPLETED | COMPLETED | null |
| failed이며 분해 status가 failed | FAILED | FAILED | DECOMPOSITION_FAILED |
| 그 밖의 failed | FAILED | FAILED | GENERATION_FAILED |

실행 상태는 최종 Answer의 의미를 따른다. 일부 Routing 오류나 누락 인자가 있어도 코어가 답변을 만들었다면 COMPLETED다. 부분 답변 뒤에 기업 선택이 필요한 경우 `answer` 종류에도 `options`가 포함될 수 있다. 부분 실패는 routing과 answer.notices에 남는다. 고정 안내도 Generation 처리가 완료된 결과다. COMPLETED가 항상 LLM 호출이나 모든 도구의 성공을 의미하지는 않는다.

분해 clarify와 failed에서는 그래프가 Routing을 생략하므로 HTTP층이 `RouteResult(status="not_routed")`의 기본 필드를 채운다. Answer.kind=failed의 최상위 error.retryable은 false다. 오류 문구는 각각 `Query decomposition failed.`와 `Answer generation failed.`다.

인용 번호와 evidence ID는 고유해야 한다. citation.evidence_id는 citable=true인 evidence를 가리키며 label과 source_url도 일치해야 한다. verification.masked=true이면서 오류 목록이 남는 것은 코어의 정상적인 보정 결과다. 이를 실패로 바꾸지 않는다. clarify 같은 고정 안내에는 인용 없이 grounded=true가 올 수 있다.

Routing 진단 필드는 v0.1의 고정 문구로 정제한 사본을 Generator에 전달한다. HTTP 응답의 Routing도 같은 규칙을 적용한다. 도구 result와 업무 데이터 및 인용은 보존한다. 모델 출력 문자열을 정해진 테스트 문장으로 교체하지 않는다.

## 대화 저장

대화 생성 요청은 `{context:Context}`다. 기업 정보는 fixture와 비교하지 않는다. snapshot은 schema_version="0.3"과 conversation_id 및 context와 storage="memory" 및 messages를 갖는다. 대화 ID는 기존 API와 같은 표준 UUID 형식이며 대소문자를 구분한다.

메시지 요청은 `{query:string,context?:Context}`다. query는 앞뒤 공백을 제거한 뒤 검사한다. context를 생략하면 저장된 문맥을 쓴다. request_id와 history는 클라이언트가 지정하지 않는다. Spring이 최신 저장 메시지 20개를 전달한다. 생성된 긴 assistant 본문은 저장할 때 보존하며 모델 이력으로 보낼 때만 최대 4000 code point로 제한한다. 앞부분을 자른 결과가 공백만 남으면 전송용 이력에서 앞 공백을 제외한 뒤 다시 제한한다. 추가 질문의 선택지는 다음 입력의 번호를 해석할 수 있도록 전송용 assistant 이력에 번호와 함께 포함한다. 저장된 본문은 바꾸지 않는다. 전체 전송 길이 제한을 지키며 선택지 블록을 일부만 잘라 잘못된 번호를 만들지 않는다.

메시지 응답은 실행 응답에 conversation_id와 response_id를 추가한다. response_id는 Spring이 생성한다. 저장 메시지는 message_id와 role 및 content와 response를 갖는다. user의 response는 null이며 assistant의 response에는 전체 메시지 응답을 저장한다. assistant.message_id는 response_id와 같다.

같은 대화는 직렬로 처리한다. COMPLETED와 NEEDS_INPUT일 때 user와 assistant를 함께 저장하고 새 context를 확정한다. HTTP 오류와 잘못된 응답 및 execution_status=FAILED에서는 메시지와 문맥을 보존한다. 대화별 최대 100개 메시지를 쌍 단위로 유지한다. 재시작하면 초기화된다.

## 오류와 검증 모드

400 VALIDATION_ERROR와 내부 인증 401 UNAUTHORIZED 및 503 UPSTREAM_UNAVAILABLE과 504 UPSTREAM_TIMEOUT 및 500 INTERNAL_ERROR의 안전한 오류 envelope를 유지한다. Spring에서 내부 인증 실패와 잘못된 응답은 502 UPSTREAM_INVALID_RESPONSE다. 없는 대화는 404 CONVERSATION_NOT_FOUND다. 모델 설정과 연결 실패는 503이며 처리 제한 시간 초과는 504다. 상세 예외와 내부 키를 응답이나 로그로 노출하지 않는다.

live는 최신 QueryDecomposer와 McpToolCaller 및 AnswerGenerator를 사용하는 전체 그래프다. Claude와 OpenAI 모델 설정을 지원한다. HTTP 어댑터는 SDK 자동 재시도를 끄고 요청에서 실제 만든 클라이언트만 종료한다. 자동 검증의 stub은 모델과 도구의 입출력만 fake로 바꾸며 실제 그래프와 AnswerGenerator의 근거 및 인용 검증을 실행한다. 실행 모드를 응답에 표시한다. 실제 모델을 사용한 검증과 fake 의존성을 사용한 검증 결과를 구분한다.
