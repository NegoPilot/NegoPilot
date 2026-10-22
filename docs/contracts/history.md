# 개인 대화 히스토리 API v0.4

Spring의 기본 실행 프로필은 로그인한 회원의 대화와 응답을 PostgreSQL에 저장한다. 대화 목록과 스냅샷 및 메시지 페이지의 `schema_version`은 `"0.4"`다. 저장된 Agent 응답과 Python 내부 요청 계약은 [Generation v0.3](agent-generation.md)을 유지한다.

## 인증과 소유권

모든 경로는 `ACTIVE` 회원의 로그인 세션이 필요하다. 소유자는 세션의 회원 UUID로 결정한다. 요청 본문에 `owner_id`나 `user_id`를 지정할 수 없다. 관리자도 다른 회원의 대화를 읽거나 변경할 수 없다. 없는 대화와 다른 회원의 대화는 모두 `404 CONVERSATION_NOT_FOUND`를 반환한다.

POST와 PATCH 및 DELETE에는 CSRF 토큰이 필요하다. `GET /api/auth/csrf`가 반환하는 `header_name`과 `token`을 사용한다. 로그인 세션 쿠키와 토큰을 함께 전달한다. 로그인 후에는 토큰을 다시 조회한다.

답변 생성 직전과 저장 직전에 회원의 상태를 확인한다. 생성 도중 비활성화된 회원의 결과는 저장하지 않는다. 회원 비활성화는 기존 대화의 삭제를 의미하지 않는다.

## 경로

| 메서드와 경로 | 성공 상태 | 응답 |
| --- | --- | --- |
| `POST /api/conversations` | 201 | 새 대화 스냅샷 |
| `GET /api/conversations` | 200 | 대화 목록 페이지 |
| `GET /api/conversations/{conversation_id}` | 200 | 최신 메시지를 포함한 스냅샷 |
| `PATCH /api/conversations/{conversation_id}` | 200 | 제목 변경 후 스냅샷 |
| `DELETE /api/conversations/{conversation_id}` | 204 | 본문 없음 |
| `GET /api/conversations/{conversation_id}/messages` | 200 | 메시지 페이지 |
| `POST /api/conversations/{conversation_id}/messages` | 200 | 전체 Generation v0.3 응답 |
| `GET /api/conversations/{conversation_id}/responses/{response_id}` | 200 | 저장 질문과 전체 생성 응답 |

`POST /api/agent/queries`는 인증된 회원의 상태 없는 요청으로 유지한다. 이 경로는 개인 히스토리를 만들지 않는다. 대화 이어하기에는 conversations 경로를 사용한다.

## 공통 형식

- ID는 하이픈이 포함된 표준 UUID 문자열이다. 응답은 소문자로 직렬화한다.
- 시간은 UTC의 ISO 8601 문자열이다. 소수 초가 포함될 수 있다.
- `context.selected_supplier_ids`는 빈 배열 또는 기업 ID 하나를 담는 배열이다. ID는 `^[A-Za-z0-9_-]{1,100}$`을 따른다. 기업 선택은 필수가 아니다.
- `seq`는 대화 안에서 1부터 증가한다. 저장된 질문과 답변은 연속된 두 번호를 사용한다.
- `message_count`는 저장된 메시지 수다. 질문 한 번의 정상 저장으로 2가 증가한다.
- 정의되지 않은 본문 필드와 쿼리 파라미터를 거절한다. 같은 쿼리 파라미터를 반복해도 400이다.
- 페이지의 `next_cursor`가 `null`이면 이전 항목이 더 없다. 빈 결과는 빈 배열과 `null`을 반환한다.

다음 타입의 `GenerationResponse03`은 Generation v0.3 전체 실행 응답에 `conversation_id`와 `response_id`가 추가된 객체다. `answer`의 근거와 인용 및 검증 결과를 포함한 원본 응답을 보존한다.

```ts
type Context = { selected_supplier_ids: string[] }

type ConversationSummary = {
  conversation_id: string
  title: string
  created_at: string
  updated_at: string
  message_count: number
  context: Context
}

type StoredMessage = {
  seq: number
  message_id: string
  role: "user" | "assistant"
  content: string
  created_at: string
  response: GenerationResponse03 | null
}

type ConversationSnapshot = ConversationSummary & {
  schema_version: "0.4"
  storage: "database"
  messages: StoredMessage[]
  next_cursor: string | null
}

type ConversationList = {
  schema_version: "0.4"
  storage: "database"
  items: ConversationSummary[]
  next_cursor: string | null
}

type MessagePage = {
  schema_version: "0.4"
  storage: "database"
  conversation_id: string
  messages: StoredMessage[]
  next_cursor: string | null
}

type SavedResponse = {
  query: string
  response: GenerationResponse03
}
```

user 메시지의 `response`는 `null`이다. assistant 메시지의 `response`는 전체 생성 응답이다. assistant의 `message_id`는 응답의 `response_id`와 같다. 저장 메시지의 `created_at`은 DB 저장 시각이다.

## 대화 생성과 제목

기업을 선택하지 않은 새 대화는 다음과 같이 생성한다. `context` 필드는 필수다.

```http
POST /api/conversations
Content-Type: application/json
```

```json
{
  "context": {
    "selected_supplier_ids": []
  }
}
```

201 응답 예시:

```json
{
  "conversation_id": "56f51af3-50dc-42e2-a603-f7cf637df417",
  "title": "새 대화",
  "created_at": "2026-10-21T05:00:00Z",
  "updated_at": "2026-10-21T05:00:00Z",
  "message_count": 0,
  "context": { "selected_supplier_ids": [] },
  "schema_version": "0.4",
  "storage": "database",
  "messages": [],
  "next_cursor": null
}
```

처음 저장에 성공한 질문의 앞 50 code point로 제목을 자동 생성한다. 생성 전 제목을 수동 변경했다면 수동 제목을 유지한다. 이후 메시지는 제목을 자동 변경하지 않는다.

```http
PATCH /api/conversations/56f51af3-50dc-42e2-a603-f7cf637df417
Content-Type: application/json
```

```json
{ "title": "협력사 납기 비교" }
```

제목은 앞뒤 공백을 제거한 뒤 1..100 code point여야 한다. 공백만 있는 제목은 거절한다. 제목 변경은 `updated_at`을 갱신한다. 답변 생성 중에도 제목을 변경할 수 있다.

## 질문과 후속 질문

```http
POST /api/conversations/56f51af3-50dc-42e2-a603-f7cf637df417/messages
Content-Type: application/json
Idempotency-Key: 4341d122-4632-4df1-bacf-5ee6c67f4fd2
```

```json
{
  "query": "납기 조건에 맞는 협력사를 찾아줘",
  "context": { "selected_supplier_ids": [] }
}
```

`query`는 앞뒤 공백을 제거한 뒤 1..4000 code point여야 한다. 공백만 있는 질문은 거절한다. 클라이언트는 `request_id`나 `history`를 보내지 않는다. Spring이 요청 ID와 저장 이력을 전달한다.

`context`를 생략하면 직전에 저장된 문맥을 사용한다. 명시한 문맥은 이번 질문에 즉시 사용하며 저장 성공 시 대화 문맥으로 확정한다. `context: null`은 거절한다. 기업을 바꿀 때 새 대화를 만들 필요가 없다. 같은 대화에 새 기업 ID를 전달한다. 선택을 해제할 때는 빈 배열을 명시한다.

후속 질문 예시:

```json
{ "query": "그중 두 번째 기업의 재무 상태는 어때?" }
```

응답 본문의 `schema_version`은 `"0.3"`이다. 스냅샷의 버전과 구분한다. 생성 응답 전체 형식은 [Generation 계약](agent-generation.md#실행-응답)을 따른다. 다음 두 필드가 추가된다.

| 필드 | 의미 |
| --- | --- |
| `conversation_id` | 요청한 대화 ID |
| `response_id` | Spring이 발급한 응답 UUID |

### 저장과 실패

`COMPLETED`와 `NEEDS_INPUT`은 질문과 답변 및 문맥과 제목을 하나의 트랜잭션으로 저장한다. 추가 정보가 필요한 질문도 후속 질문의 문맥이 된다.

HTTP 200이어도 `execution_status="FAILED"`이면 저장하지 않는다. HTTP 오류나 잘못된 Agent 응답도 메시지와 문맥 및 제목을 변경하지 않는다. 클라이언트는 실패 질문을 저장된 성공 메시지처럼 취급하지 않아야 한다. 실패 응답의 `response_id`는 저장 응답 조회에서 찾을 수 없다.

같은 대화에는 동시에 하나의 생성 작업만 실행한다. 다른 요청이 처리 중이면 `409 CONVERSATION_BUSY`다. 처리 중 대화 삭제도 같은 오류를 반환한다. 대화 읽기는 모델 응답을 기다리지 않고 이미 확정된 기록을 반환한다.

작업 점유 기간은 Spring Agent 응답 제한 시간에 30초를 더한 값이다. 모델 호출 중 DB 트랜잭션을 유지하지 않는다. 점유가 만료됐거나 다른 작업으로 교체됐다면 이전 작업은 결과를 저장하지 않는다. 클라이언트는 `409 CONVERSATION_LEASE_EXPIRED`를 받은 뒤 기록을 다시 조회할 수 있다.

### Idempotency-Key

이 헤더는 선택 사항이다. 네트워크 재시도 때 질문이 중복 저장되는 일을 막으려면 질문마다 새 UUID를 발급하고 같은 요청의 재시도에만 재사용한다. 헤더를 두 번 보내거나 UUID 형식이 아니면 400이다.

키의 범위는 대화 하나다. 서버는 검증과 공백 정규화가 끝난 `query`와 전달된 `context`의 해시를 비교한다. 문맥 생략과 명시적 문맥 전달은 서로 다른 요청으로 취급한다.

| 재요청 조건 | 결과 |
| --- | --- |
| 같은 키와 같은 본문이며 이전 저장 성공 | 저장된 응답 그대로 반환. 모델 재호출과 메시지 추가 없음 |
| 같은 키와 다른 본문 | `409 IDEMPOTENCY_CONFLICT` |
| 같은 키와 같은 본문이며 생성 진행 중 | `409 CONVERSATION_BUSY` |
| 같은 키와 같은 본문이며 이전 생성 실패 | 다시 실행 가능 |

성공 응답 재사용 시 `request_id`와 `response_id`도 최초 응답과 같다. 실패 후에도 같은 키를 다른 질문에 쓰면 충돌한다. 키를 생략한 요청은 자동 중복 방지를 보장하지 않는다.

## 대화 목록

```http
GET /api/conversations?size=20
```

`size` 기본값은 20이며 1..100이다. 최신 `updated_at` 순으로 반환한다. 시각이 같으면 대화 UUID 내림차순으로 정렬한다. 목록에는 메시지 본문을 넣지 않는다.

다음 페이지는 반환된 `next_cursor`를 `cursor`에 그대로 전달한다. 목록 커서는 해석하거나 직접 만들지 않는 불투명 문자열이다. 메시지 페이지 커서와 서로 바꿔 쓸 수 없다.

```json
{
  "schema_version": "0.4",
  "storage": "database",
  "items": [
    {
      "conversation_id": "56f51af3-50dc-42e2-a603-f7cf637df417",
      "title": "협력사 납기 비교",
      "created_at": "2026-10-21T05:00:00Z",
      "updated_at": "2026-10-21T05:02:00Z",
      "message_count": 2,
      "context": { "selected_supplier_ids": [] }
    }
  ],
  "next_cursor": null
}
```

목록 페이지 전체를 하나의 고정 시점으로 묶지는 않는다. 페이지를 읽는 사이 제목 변경이나 새 답변 저장으로 정렬 위치가 바뀔 수 있다. 최신 목록을 확인하려면 첫 페이지부터 다시 읽는다.

## 메시지 조회와 커서

대화 스냅샷은 최신 50개 메시지를 반환한다. 결과 안의 메시지는 항상 `seq` 오름차순이다. 50개 제한은 응답 크기 제한이며 저장 기록을 삭제하지 않는다.

```http
GET /api/conversations/56f51af3-50dc-42e2-a603-f7cf637df417/messages?before_seq=63&size=100
```

메시지 페이지의 `size` 기본값은 50이며 1..100이다. `before_seq`는 양의 64비트 정수다. 지정한 번호는 결과에서 제외한다. 생략하면 최신 메시지부터 조회한다.

`next_cursor`는 다음 요청의 `before_seq`에 넣을 10진수 문자열이다. 예를 들어 메시지가 112개면 스냅샷은 63..112를 반환하고 `next_cursor`는 `"63"`이다. 위 요청은 1..62를 오름차순으로 반환하며 `next_cursor`는 `null`이다. 클라이언트는 이 페이지를 현재 메시지 앞에 붙인다.

페이지 크기는 메시지 수다. 홀수 크기를 지정하면 질문과 답변이 서로 다른 페이지에 포함될 수 있다. 서버는 둘을 함께 저장하지만 조회 페이지 경계를 강제로 맞추지는 않는다.

## 저장 응답 조회

```http
GET /api/conversations/56f51af3-50dc-42e2-a603-f7cf637df417/responses/52a3f43d-cc53-4e9f-a17d-0da24fa2af3a
```

응답은 `SavedResponse` 형식이다. `query`는 해당 응답과 함께 저장한 질문이다. `response`는 메시지 전송 시 반환한 전체 Generation v0.3 객체다. 별도의 최상위 `schema_version`이나 `storage` 필드를 추가하지 않는다. 응답 ID는 해당 대화에 저장된 assistant 메시지를 가리켜야 한다. 없으면 `404 RESPONSE_NOT_FOUND`다.

## 삭제와 보존

DELETE는 대화와 모든 메시지 및 저장된 중복 방지 응답을 영구 삭제한다. 성공하면 204이며 본문이 없다. 삭제한 대화를 다시 조회하거나 삭제하면 404다. 진행 중인 생성 작업의 점유가 유효하면 409로 거절한다.

대화는 서버 재시작 이후에도 유지한다. 100개 메시지 자동 삭제 제한은 없다. 질문과 답변 본문 및 전체 응답 JSON을 보존한다. 별도의 보존 만료 정책은 적용하지 않는다.

Python에 전달하는 이력은 최근 메시지 20개다. assistant 본문은 전송 시에만 최대 4000 code point로 제한한다. 번호로 후속 선택을 할 수 있도록 응답의 선택지를 번호와 함께 전송용 이력에 넣는다. 선택지를 포함한 전체 길이 제한을 지키며 DB 원문을 변경하지 않는다.

## 오류

HTTP 오류는 다음 공통 형식을 사용한다. 인증 단계나 검증 위치에 따라 `request_id`가 `null`일 수 있다.

```json
{
  "request_id": "01d079b4-3b6c-4228-a458-4ff08a02c78e",
  "error": {
    "code": "CONVERSATION_BUSY",
    "message": "Conversation is processing another request",
    "retryable": true
  }
}
```

| HTTP | code | 의미 |
| --- | --- | --- |
| 400 | `VALIDATION_ERROR` | 본문과 ID 또는 페이지 인자가 잘못됨 |
| 401 | `UNAUTHENTICATED` | 유효한 로그인 세션이 없음 |
| 403 | `FORBIDDEN` | 승인된 회원 권한이 없음 |
| 403 | `CSRF_INVALID` | CSRF 토큰이 없거나 잘못됨 |
| 403 | `ACCOUNT_NOT_ACTIVE` | 저장 전 확인에서 활성 회원 상태를 충족하지 못함 |
| 404 | `CONVERSATION_NOT_FOUND` | 대화가 없거나 소유자가 아님 |
| 404 | `RESPONSE_NOT_FOUND` | 대화 안에 저장된 응답이 없음 |
| 409 | `CONVERSATION_BUSY` | 같은 대화에 생성 작업이 진행 중 |
| 409 | `CONVERSATION_LEASE_EXPIRED` | 생성 작업의 저장 권한이 만료되거나 교체됨 |
| 409 | `IDEMPOTENCY_CONFLICT` | 같은 키를 다른 본문에 사용함 |
| 502 | `UPSTREAM_INVALID_RESPONSE` | Agent 응답이 계약을 충족하지 못함 |
| 503 | `UPSTREAM_UNAVAILABLE` | Agent 연결이나 실행 환경을 사용할 수 없음 |
| 504 | `UPSTREAM_TIMEOUT` | Agent 응답 제한 시간 초과 |
| 500 | `INTERNAL_ERROR` | 저장 등 내부 처리 실패 |

## 검증

PostgreSQL 연결 환경변수인 `TEST_DATABASE_URL`과 `TEST_DATABASE_USER` 및 `TEST_DATABASE_PASSWORD`를 설정한 뒤 다음 검사를 실행한다. 테스트는 별도 임시 스키마를 만들고 종료 시 제거한다.

```sh
mvn -f backend/pom.xml -B -ntp \
  -Dtest=PersistentConversationServiceTest,PersistentConversationControllerTest test
```

서비스 검사는 실제 PostgreSQL에서 소유권과 재시작 후 조회 및 페이지 경계와 중복 방지와 동시 생성 및 실패 원자성을 검증한다. 생성 중 회원 비활성화와 점유 교체 및 만료도 검증한다. Agent gateway는 테스트 응답을 사용한다. 이 검사는 실제 모델 호출이나 브라우저 로그인 연결의 검증을 대신하지 않는다.
