# 협력사 조회와 선택 기업 챗봇 API v0.2

협력사 조회와 선택 기업 질문을 연결하는 HTTP 계약이다. 조회는 stub과 live 모드를 모두 지원하고 선택 기업 질문은 stub 전용이다. 기준 main은 `8d48d7b2b200c88f0ccfe33c3177057811e38cae`다. Spring backend와 Python HTTP 어댑터 및 fixture가 함께 구현돼 있다. Vue 화면은 별도 작업이다. 실제 Agent 생성 답변은 [Generation HTTP 계약 v0.3](agent-generation.md)의 v2 경로를 사용한다. v0.2는 agent-http.md v0.1의 query-executions와 agent/queries 경로를 대체하지 않으며 별도 경로로 정의한다.

## 범위와 책임

연결 흐름은 Vue → Spring → Python FastAPI 순서다. 아래 동작은 각 구현이 준수할 계약이다. 조회 API 세 개는 AGENT_EXECUTION_MODE=stub이면 fixtures/supplier-chat.json을 읽고 live이면 business-mcp를 통해 협력사 DB를 읽는다. 대화 API와 Python 챗봇 경로는 stub에서만 작동한다. live에서는 503 DEMO_MODE_REQUIRED와 retryable=false를 반환한다. 어느 모드에서도 모델을 호출하지 않는다. stub 모드에서는 fixtures/supplier-chat.json이 가상 데이터의 단일 원본이다. 모든 성공 응답은 schema_version="0.2"와 실행 모드를 나타내는 mode를 포함한다. 조회 응답의 mode는 "stub" 또는 "live"이고 대화 응답의 mode는 "stub"이다. Python 어댑터는 조회와 정해진 질문의 템플릿 응답을 담당한다. Spring은 내부 HTTP 호출과 응답 검증 및 메모리 대화 저장을 담당한다. 후속 Vue 화면은 Spring 공개 API만 호출한다.

기준 main에는 Generation과 LangGraph 실행 코드가 이미 있다. 이 HTTP 계약은 fixture 조회와 템플릿 응답을 정의한다. `generation_status: NOT_IMPLEMENTED`는 이 HTTP 범위에서 코어 Generation을 호출하지 않는다는 뜻이며 코어의 구현 여부와 별개다. 기존 Routing과 Generation 계약 및 공용 스키마는 변경하지 않는다.

이번에는 단일 기업 문맥만 지원한다. RFQ와 협상안 검사 및 법령 카드와 일반 자연어 생성은 후속 범위다. 사용자가 입력한 질문이 지원 범위 밖이면 추가 입력 응답으로 안내한다. 테스트 데이터를 실제 기업 정보나 실제 MCP 실행 결과로 표시하지 않는다.

Spring과 Python은 같은 AGENT_EXECUTION_MODE 값을 사용한다. Spring은 대화 경로 전체에 모드 검사를 적용한다. 기업 미선택 대화 생성이나 메모리 대화 조회처럼 Python을 호출하지 않는 경로도 포함한다. 설정 생략 시 live로 간주한다. 기존 v0.1 동작에는 이 새 가드를 적용하지 않는다.

## 조회 API

| Spring 공개 경로 | Python 내부 경로 | 응답 data |
| --- | --- | --- |
| GET /api/v1/business/catalog | GET /internal/v1/business/catalog | processes와 regions |
| GET /api/v1/suppliers | GET /internal/v1/suppliers | items와 returned_count 및 limit |
| GET /api/v1/suppliers/{supplier_id} | GET /internal/v1/suppliers/{supplier_id} | SupplierDetail |

조회 성공 응답은 {schema_version:"0.2",mode:"stub"|"live",data:{...}}다. 카탈로그 processes는 {process:string,materials:string[]}[]다. regions는 string[]다. stub은 fixture에서, live는 business-mcp의 business://catalog에서 도출하며 중복을 제거한다. live 카탈로그는 협력사가 없는 공정을 제외한다.

검색 query parameter는 process 필수다. material과 region은 선택이다. 각 문자열은 앞뒤 공백 제거 후 1..100자다. iatf_required와 iso14001_required는 true 또는 false이며 기본값 false다. false는 인증 여부 전체다. true면 각각 IATF 16949와 ISO 14001 인증이 있는 기업만 반환한다. min_monthly_capacity는 선택이며 정수 1..10000000이다. 월 생산능력이 이 값 이상인 capability만 matched_capabilities에 포함한다. limit은 정수 1..100이며 기본값 20이다. 알 수 없는 query parameter와 중복 parameter는 400으로 거절한다. 빈 선택값은 프런트가 생략한다. 화면의 인증 체크박스는 체크했을 때만 iatf_required=true와 iso14001_required=true를 보낸다. 지역 드롭다운 값은 카탈로그 regions에서 가져온다. 존재하지 않는 유효 검색 조건은 200과 빈 items를 반환한다. 필터를 만족하는 기업을 supplier_id 순서로 정렬하고 기업별로 묶은 뒤 limit을 적용한다. returned_count는 반환 기업 수다. total과 offset은 제공하지 않는다. 상세 ID가 없으면 404 SUPPLIER_NOT_FOUND다.

SupplierSummary는 supplier_id와 name 및 region과 iatf16949와 iso14001 및 matched_capabilities와 provenance를 갖는다. iatf16949와 iso14001은 인증 보유 여부를 나타내는 boolean이다. matched_capabilities는 검색 조건에 맞는 {process,material,monthly_capacity}[]다. provenance는 {profile,financials,operations}이며 각 값은 데이터 출처다. stub은 모두 "STUB"이다. live의 profile과 financials는 협력사 DB의 suppliers.source 값인 "DART" 또는 "MOCK"이다. live의 operations는 생산능력과 품질 및 계약과 견적이 모두 가상 데이터이므로 항상 "MOCK"이다.

SupplierDetail은 supplier_id와 name 및 region과 iatf16949와 iso14001 및 capabilities와 quality와 financials 및 recent_quotes와 existing_contract 및 provenance를 갖는다. capabilities의 구조는 matched_capabilities와 같다. quality는 {defect_ppm:number|null,on_time_delivery_rate:number|null}다. financials는 {year:integer,revenue_krw:number|null,operating_profit_krw:number|null,debt_ratio:number|null,source:"STUB"|"DART"|"MOCK"}[]다. recent_quotes는 {process,material,unit_price_krw,quoted_at}[]이며 quoted_at은 YYYY-MM-DD다. existing_contract는 null 또는 {payment_days:integer,price_linkage:boolean}다. 숫자는 fixture 그대로 사용한다. on_time_delivery_rate는 0..100의 백분율이다. debt_ratio도 이미 백분율로 환산한 값이며 100을 넘을 수 있다. 화면에서 두 값에 100을 곱하지 않는다. _krw는 원 단위다. null과 빈 financials는 0으로 바꾸지 않는다.

### live 조회

live 조회는 Python이 business-mcp의 search_suppliers와 find_suppliers 및 get_supplier 도구와 business://catalog 리소스를 호출해 만든다. 응답 형식과 검색 규칙은 stub과 같다.

- 검색 조건은 카탈로그 값과 정확히 일치해야 한다. business-mcp의 공정과 소재 및 지역 별칭은 적용하지 않는다. 카탈로그에 없는 공정과 소재 및 지역은 도구를 호출하지 않고 200과 빈 items를 반환한다.
- 검색은 공정의 소재마다 search_suppliers를 호출한 뒤 기업별로 묶는다. 소재 하나의 결과가 도구의 최대 행 수 100에 도달하면 누락 가능성이 있으므로 500 INTERNAL_ERROR를 반환한다.
- 상세 ID는 SUP-0001처럼 정확한 형식만 조회한다. 숫자만 있는 ID와 회사명 및 소문자 접두어는 404 SUPPLIER_NOT_FOUND다.
- on_time_delivery_rate는 DB의 0..1 비율에 100을 곱해 소수 첫째 자리로 반올림한다. debt_ratio는 부채총계를 자본총계로 나눈 백분율이며 자본총계가 0 이하이거나 값이 없으면 null이다. existing_contract는 기존 계약이 없으면 null이다.
- business-mcp나 협력사 DB에 연결할 수 없으면 503 UPSTREAM_UNAVAILABLE과 retryable=true를 반환한다. 원인 메시지는 노출하지 않는다.

## 대화 API

Spring만 대화를 저장한다. 메모리 저장이며 재시작하면 초기화된다. 로그인과 영구 저장은 이번 범위가 아니다. 모든 ID는 대소문자를 구분한다. UUID는 표준 UUID 문자열이다.

Context = {selected_supplier_ids:string[]}이며 0..1개의 기업 ID를 받는다. 기업 ID는 URL 경로에 사용할 영문과 숫자 및 밑줄과 하이픈으로 구성하며 길이는 1..100자다. 정규식은 ^[A-Za-z0-9_-]{1,100}$다. 공백과 슬래시 등의 다른 문자는 400 VALIDATION_ERROR로 거절한다. 상세 조회 경로에도 같은 ID 규칙을 적용한다. 중복과 빈 값은 거절한다. 기업 선택을 바꿀 때는 새 대화를 만드는 것을 UI 기본 동작으로 한다. 문맥의 기업이 존재하는지 Python 상세 조회로 검증한 뒤 저장한다.

- POST /api/v1/conversations: 본문 {context:Context}. 201 ConversationSnapshot 반환.
- GET /api/v1/conversations/{conversation_id}: 200 ConversationSnapshot 반환.
- POST /api/v1/conversations/{conversation_id}/messages: 본문 {query:string,context?:Context}. 200 ChatResponse 반환. context를 생략하면 저장된 문맥을 사용한다. 명시하면 해당 값으로 변경한다. 외부에서 history와 request_id를 지정하지 않는다.

ConversationSnapshot = {schema_version:"0.2",mode:"stub",conversation_id:UUID,context:Context,storage:"memory",messages:StoredMessage[]}.

StoredMessage = {message_id:UUID,role:"user"|"assistant",content:string,response:ChatResponse|null}. 사용자 메시지 response는 null이다. assistant.message_id는 response.response_id와 같다. 순서는 user와 assistant가 번갈아 나온다. query는 앞뒤 공백 제거 후 1..4000자다. 알 수 없는 JSON 필드를 거절한다. 없는 대화는 404 CONVERSATION_NOT_FOUND다. 잘못된 UUID와 JSON 및 본문은 400 VALIDATION_ERROR다.

Spring은 요청마다 request_id를 생성한다. 저장된 메시지 중 최신 20개를 {role,content} history로 전달한다. 이번 query는 history에 중복 포함하지 않는다. 동일 대화의 요청은 직렬로 처리해 이력 순서와 문맥이 섞이지 않게 한다. 다른 대화의 요청은 독립적이다. HTTP 오류나 잘못된 upstream 응답이면 메시지와 문맥을 저장하지 않는다. 정상 COMPLETED와 NEEDS_INPUT 응답은 사용자와 assistant를 함께 저장한다. 이력은 대화별 최대 100개 메시지로 유지한다. 101개 이상이 되면 가장 오래된 user/assistant 쌍부터 제거한다.

## Python 챗봇 경로

POST /internal/v1/chat/responses

요청 = {request_id:UUID,conversation_id:UUID,query:string,history:HistoryEntry[],context:Context}. 모든 필드를 요구한다. history는 0..20개다. 각 role은 user 또는 assistant다. content는 1..4000자이며 공백만 있는 값은 거절한다. 알 수 없는 필드를 거절한다. 이 계약에서 history는 전달과 저장 대상으로만 사용한다. 일반적인 후속 질문 해석은 범위에 포함하지 않는다.

ChatResponse의 모든 필드는 필수다.

| 필드 | 값 |
| --- | --- |
| schema_version | "0.2" |
| mode | "stub" |
| request_id | 입력과 같은 UUID |
| conversation_id | 입력과 같은 UUID |
| response_id | Python이 생성한 UUID |
| execution_status | COMPLETED 또는 NEEDS_INPUT |
| generation_status | NOT_IMPLEMENTED |
| resolved_context | 입력과 같은 Context |
| presentation | {status:"READY" 또는 "NEEDS_INPUT",renderer:"template",text:string,cards:SupplierCard[]} |
| citations | 빈 배열 |
| evidence | StubEvidence[] |
| clarification | null 또는 {missing_fields:string[],question:string,options:string[]} |

SupplierCard = {type:"supplier_detail",supplier:SupplierDetail,evidence_ids:string[]}.
StubEvidence = {evidence_id:string,server:"business-mcp",tool:"get_supplier",supplier_id:string,source:"stub",executed:false}.

COMPLETED는 이 HTTP 계약의 템플릿 응답 준비가 끝났다는 뜻이다. 이때 presentation.status는 READY다. clarification은 null이다. 선택 기업과 같은 기업의 카드 한 개와 근거 한 개를 반환한다. 카드 evidence_ids는 실제 evidence_id를 참조한다. 설명과 숫자는 같은 fixture 데이터와 일치해야 한다.

NEEDS_INPUT일 때 presentation.status는 NEEDS_INPUT이다. cards와 evidence는 빈 배열이다. clarification은 null이 아니고 question은 presentation.text와 같다. missing_fields는 기업 미선택이면 ["context.selected_supplier_ids"]다. 지원하지 않는 질문이면 ["query"]다. options는 사용 가능한 질문 세 개다.

지원하는 정규 질문은 아래 세 개다. 앞뒤 공백은 제거한다. 명시한 별칭 외의 자연어 질문은 NEEDS_INPUT으로 반환한다.

1. 이 회사에 대해 알려줘
2. 이 회사의 재무 상태를 알려줘
3. 납기와 품질은 어때?

추가 별칭 "이 회사의 재무 상태와 납기 지표를 알려줘"는 개요 응답과 같은 방식으로 처리한다. 기업을 선택하지 않았다면 query보다 기업 선택 안내가 우선한다. 존재하지 않는 기업 ID는 404 SUPPLIER_NOT_FOUND다. NEEDS_INPUT을 임의의 기업 선택으로 대체하지 않는다.

## HTTP와 오류

Python 모든 새 내부 경로에 X-Internal-Api-Key 인증을 적용한다. Spring은 브라우저가 전달한 이 헤더를 신뢰하거나 전달하지 않고 서버 설정 값을 사용한다. 기존 접속 제한과 응답 제한 및 오류 봉투를 재사용한다. 비밀값과 예외 원문을 노출하지 않는다. 내부 키와 모델 비밀값은 프런트에 넣지 않는다.

오류 = {request_id:UUID|null,error:{code:string,message:string,retryable:boolean}}.

- 400 VALIDATION_ERROR: 형식과 범위 오류.
- 401 UNAUTHORIZED: 내부 키 오류. Spring 공개 응답에서는 502 UPSTREAM_INVALID_RESPONSE.
- 404 SUPPLIER_NOT_FOUND 또는 CONVERSATION_NOT_FOUND: 대상 없음.
- 503 DEMO_MODE_REQUIRED: 대화 경로에서 stub 모드가 아님. retryable=false.
- 503 UPSTREAM_UNAVAILABLE: Python 연결 실패 또는 live 조회의 business-mcp 연결 실패. retryable=true.
- 504 UPSTREAM_TIMEOUT: 처리 시간 초과. retryable=true.
- 502 UPSTREAM_INVALID_RESPONSE: Spring이 응답 스키마와 상태 또는 ID 및 기업 문맥 불일치를 발견함. retryable=false.
- 500 INTERNAL_ERROR: 예상하지 못한 오류. retryable=false.

Spring은 성공 응답의 필드와 자료형 및 schema_version과 mode를 검증한다. stub 조회 응답은 fixture와 같은 값인지 확인한다. live 조회 응답은 DB 값을 알 수 없으므로 형식과 출처 값을 검증하고 검색 결과가 요청한 조건과 limit을 만족하며 supplier_id 순서인지 확인한다. 상세는 요청한 기업 ID와 같은지 확인한다. ChatResponse는 request_id와 conversation_id 및 resolved_context가 요청과 일치하는지 확인한다. 카드와 근거의 기업 ID 및 evidence 연결과 상태별 제약을 검증한다. 예상하지 못한 내부 오류 본문은 그대로 공개하지 않는다. 자동 재시도하지 않는다.

## 후속 화면 연동과 검증 기준

후속 Vue 개발 서버는 /api와 /health를 Spring으로 프록시해야 한다. 화면은 필터 선택 → 조회 → 기업 상세 → AI에게 이 회사 질문하기 → 대화 생성 → 지원 질문 전송 → 답변과 기업 카드 표시 흐름을 제공해야 한다. 모든 화면에서 테스트 데이터와 템플릿 응답임을 표시한다. 기업을 바꾸면 새 대화로 시작한다. 질문 처리 중 중복 전송을 막는다. 실패와 빈 결과 및 기업 미선택 상태를 표시한다. 숫자가 null이면 정보 없음으로 표시한다.

실제 두 프로세스의 통합 검증은 [통합 검사 가이드](../../scripts/integration/README.md)를 따라 수행한다. 위 흐름과 문맥 유지 및 다른 기업으로 새 대화 생성과 오류 응답을 확인한다. 기존 v0.1 검사도 유지한다. Spring 최종 JAR 빌드 완료 뒤에 실행하며 실행 중 JAR을 다시 빌드하지 않는다. 검증 결과에는 가짜 Python HTTP 응답을 사용한 backend 검사와 실제 두 프로세스 검사를 구분해 기록한다. 실제 모델이나 MCP와 DB를 호출하지 않았는지도 명시한다. 검증 결과는 사용한 커밋과 실행 모드 및 실제 호출한 의존성을 함께 기록한다.
