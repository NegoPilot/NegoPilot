# Agent 진행 상황 스트리밍 계약 v0.1

질문 하나를 처리하는 동안 Agent가 어느 단계에 있는지를 화면에 보여 주기 위한 계약이다. 기존 [Generation HTTP 계약 v0.3](agent-generation.md)의 요청과 최종 응답은 바꾸지 않고, 같은 실행을 **Server-Sent Events(SSE)** 로 흘려보내는 경로를 추가한다.

실제 처리 시간은 그대로다. 측정(#75) 기준으로 질문 하나에 약 3.7초가 걸리고 그중 분해가 약 2.2초다. 이 시간 동안 빈 화면 대신 "질문 분석 중 → 조회 중 → 답변 작성 중"을 보여 체감 대기를 줄이는 것이 목적이다.

## 1. 결정 사항

| # | 안건 | 결정 |
| --- | --- | --- |
| 1 | 전송 방식 | SSE (`text/event-stream`). 서버 → 클라이언트 한 방향이면 충분하고 HTTP 그대로 쓸 수 있다 |
| 2 | 경로 | 기존 경로에서 분기하지 않고 **`/stream` 경로를 따로 추가**한다 (2절). 기존 v0.3 경로는 당분간 그대로 유지한다 |
| 3 | 보낼 단위 | **노드 단계**(분해, Routing, 생성)의 시작과 끝. 답변 글자를 한 자씩 보내는 토큰 스트리밍은 하지 않는다 (8절) |
| 4 | 최종 결과 | 마지막 `result` 이벤트에 v0.3 실행 응답 전체를 그대로 담는다. 스트리밍 경로와 기존 경로의 최종 결과는 같다 |
| 5 | 단계 문구 | 서버는 단계 이름(`stage`)만 보내고, 화면 문구는 **프론트가 정한다** (6절) |
| 6 | 노출 정보 | 단계 이름, 상태, 시간, **개수만** 보낸다(3.1절 summary). intent 목록, 질문 원문, 도구 결과, 모델 출력, 오류 원문은 `stage` 이벤트에 넣지 않는다 |
| 7 | 연결 끊김 | 클라이언트가 끊으면 실행을 취소하고 **대화에 저장하지 않는다** (5절) |
| 8 | Spring 구현 | 지금 구조 그대로 **Spring MVC `SseEmitter`**로 내려주고 Python 응답은 JDK `HttpClient`로 줄 단위로 읽는다. WebFlux는 들이지 않는다 |
| 9 | 실패 시 프론트 | 기존 경로로 다시 요청하지 않는다. 오류를 표시하고 `retryable`이면 다시 시도 버튼을 둔다 (6절) |
| 10 | 시간 제한·연결 유지 | 기존 처리 제한 시간(Python 30초, Spring 35초)을 스트림 전체에 적용한다. 15초 동안 이벤트가 없으면 `: ping` (3.4절) |
| 11 | 형식 버전 | 이벤트마다 `schema_version: "stream-0.1"`. 형식 변경은 PR + Agent·Backend·Frontend 담당 승인 |

결정 1~10은 2026-10-21 팀 논의에서 정했다.

## 2. 경로

| 경로 | 역할 | 응답 |
| --- | --- | --- |
| POST /internal/query-executions/stream | Python 전체 Agent 실행 (스트리밍) | SSE |
| POST /api/agent/queries/stream | Spring 상태 없는 생성 답변 (스트리밍) | SSE |
| POST /api/conversations/{conversation_id}/messages/stream | Spring 대화 메시지 (스트리밍) | SSE |

- 요청 본문은 각 기존 경로(`/internal/query-executions`, `/api/agent/queries`, `/api/conversations/{id}/messages`)와 **같다.** 검증 규칙도 같다
- 요청 검증 실패(400), 내부 인증 실패(401), 없는 대화(404)처럼 **실행을 시작하기 전에 정해지는 오류는 SSE가 아니라 기존 JSON 오류 envelope**로 응답한다
- 실행을 시작하면 HTTP 200과 `Content-Type: text/event-stream`으로 응답하고, 이후의 오류는 `error` 이벤트로 보낸다
- 응답 헤더: `Cache-Control: no-cache`, `X-Accel-Buffering: no` (프록시가 이벤트를 모아 두지 않게)
- 브라우저의 `EventSource`는 GET만 지원하므로, 프론트는 `fetch`의 `ReadableStream`으로 POST 응답을 읽는다 (6절)

## 3. 이벤트

SSE 한 건은 `event:` 줄과 JSON 한 줄의 `data:`로 이루어지고 빈 줄로 끝난다.

```
event: stage
data: {"schema_version":"stream-0.1","request_id":"…","stage":"decompose","status":"started","elapsed_ms":0}

```

### 3.1 `stage`: 단계 시작과 끝

| 필드 | 타입 | 규칙 |
| --- | --- | --- |
| schema_version | `"stream-0.1"` | |
| request_id | UUID | 요청의 request_id (Spring 공개 경로는 Spring이 만든 값) |
| stage | `decompose` / `route` / `generate` | 그래프 노드 `decompose_query`, `route`, `generate`에 대응 |
| status | `started` / `completed` / `skipped` | `skipped`는 분해가 clarify·failed라 Routing을 건너뛸 때만 |
| elapsed_ms | int ≥ 0 | 실행 시작부터 이 이벤트까지 걸린 시간 |
| duration_ms | int ≥ 0 | `completed`에만. 그 단계에 걸린 시간 |
| summary | object | `completed`에만. 아래 표의 개수 정보만 |

| stage | summary |
| --- | --- |
| decompose | `{"status": "ok"\|"clarify"\|"failed", "sub_queries": n}` |
| route | `{"tool_calls": n, "outcomes": {"ok": n, "missing_args": n, …}}` (status별 개수) |
| generate | `{"kind": "answer"\|"clarify"\|"refusal"\|"no_result"\|"failed", "retried": bool}` |

### 3.2 `result`: 최종 결과 (종료)

- `data`는 v0.3 실행 응답(`/internal/query-executions`의 200 본문) **전체**. Spring 대화 경로는 기존 메시지 응답(`conversation_id`, `response_id` 포함) 전체
- `execution_status`가 FAILED여도 `result`로 보낸다 (v0.3과 같다)
- 보낸 뒤 서버는 연결을 닫는다

### 3.3 `error`: 실행 중 오류 (종료)

- `data`는 기존 오류 envelope와 같은 모양: `{"request_id", "error": {"code", "message", "retryable"}}`
- code는 v0.3과 같다: `UPSTREAM_TIMEOUT`(retryable), `UPSTREAM_UNAVAILABLE`(retryable), `INTERNAL_ERROR`, Spring의 `UPSTREAM_INVALID_RESPONSE`
- 보낸 뒤 서버는 연결을 닫는다

### 3.4 연결 유지

- 15초 동안 보낼 이벤트가 없으면 SSE 주석 줄 `: ping`을 보낸다. 클라이언트는 무시한다

## 4. 순서 규칙

1. 이벤트는 발생 순서대로 보내며 `elapsed_ms`는 줄어들지 않는다
2. 각 단계는 `started` 다음에 `completed`가 온다 (또는 `skipped` 하나만)
3. **종료 이벤트(`result` 또는 `error`)는 정확히 한 번**, 항상 마지막이다
4. 정상 흐름

```
decompose started → decompose completed
→ route started → route completed          (분해가 clarify·failed면 route skipped)
→ generate started → generate completed
→ result
```

5. 단계 도중 오류가 나면 그 단계의 `completed` 없이 바로 `error`로 끝난다

## 5. Spring 중계 규칙

- `SseEmitter`로 응답하고, Python 스트리밍 경로는 JDK `HttpClient`로 호출해 줄 단위로 읽는다
- `stage` 이벤트는 **그대로** 클라이언트에 전달한다 (내용을 고치지 않는다)
- `result`는 기존 `GenerationResponseValidator`로 검증한 뒤 전달한다. 검증 실패면 `result` 대신 `error`(`UPSTREAM_INVALID_RESPONSE`)를 보낸다
- 대화 경로의 저장 규칙은 v0.3 "대화 저장"과 같다: `result`가 COMPLETED 또는 NEEDS_INPUT일 때만 user·assistant를 함께 저장하고, `error`나 FAILED면 저장하지 않는다. 같은 대화는 기존처럼 직렬로 처리한다
- 클라이언트가 연결을 끊으면 Python 호출을 취소하고 **저장하지 않는다**
- 처리 제한 시간은 기존 응답 제한(`AGENT_RESPONSE_TIMEOUT_SECONDS`)을 전체 스트림에 적용한다. 넘으면 `error`(`UPSTREAM_TIMEOUT`)

## 6. 프론트엔드 표시

| 이벤트 | 화면 문구 (예) |
| --- | --- |
| decompose started | 질문을 분석하고 있어요 |
| route started | 협력사·법령 자료를 찾고 있어요 |
| route skipped | (표시 생략, 다음 단계로) |
| generate started | 답변을 작성하고 있어요 |
| result | 기존처럼 답변 표시 |
| error | 기존 오류 표시 (retryable이면 다시 시도 버튼) |

- `elapsed_ms`로 경과 시간을 함께 보여 줄 수 있다 (예: "답변 작성 중 · 2.4초")
- 읽는 방법: `fetch(path, {method: "POST", body, signal})` → `response.body.getReader()` → 빈 줄 단위로 나눠 `event:`·`data:` 파싱
- 응답 `Content-Type`이 `application/json`이면 실행 전 오류이므로 기존 오류 처리로 넘긴다
- 화면을 떠나면 `AbortController`로 연결을 끊는다
- 스트림이 실패해도 기존 v0.3 경로로 다시 요청하지 않는다. 같은 질문이 두 번 실행되거나 대화가 두 번 저장될 수 있기 때문이다

## 7. Python 구현 메모 (참고, 계약 아님)

- 같은 `GenerationExecutor` 실행을 쓰고, 노드 시작·끝을 이벤트 큐로 내보낸다
- #75의 `timing.timed_node`가 노드 경계를 이미 감싸고 있으므로, 여기에 이벤트 콜백(contextvar)을 추가하는 방식이 가장 적게 바꾼다. LangGraph `astream(stream_mode="updates")`는 노드 **끝**만 알려 주므로 `started`를 내려면 별도 처리가 필요하다
- 실행은 백그라운드 task, 응답은 큐를 읽는 `StreamingResponse`. 클라이언트가 끊으면 task를 취소한다
- 기존 처리 제한 시간(`AGENT_REQUEST_TIMEOUT_SECONDS`)을 그대로 적용한다

## 8. 이번 범위 밖

- **토큰 스트리밍**(답변 글자를 생성되는 대로 보여 주기): 생성은 구조화 출력(JSON)이고, 끝난 뒤 근거 검사로 재생성하거나 `[확인 필요]`로 가릴 수 있다. 미리 보여 준 문장이 나중에 바뀌면 안 되므로 이번에는 하지 않는다
- 끊긴 스트림 이어 받기(`Last-Event-ID`): 질문 하나가 수 초라 다시 요청한다
- 하위 질문별·도구 호출별 세부 이벤트: 필요하면 v0.2에서 `stage`의 `summary`를 늘린다

## 9. 예시 이벤트 (구현 전 개발용)

Python 구현을 기다리지 않고 Spring 중계와 Vue 화면을 만들 수 있도록, 실제 SSE 응답 그대로의 예시를 둔다. `result`의 `data`는 stub 모드 Agent가 만든 실제 v0.3 응답이고, `elapsed_ms`는 #75 측정 평균에 맞춘 값이다.

| 파일 | 흐름 |
| --- | --- |
| [`fixtures/agent-streaming/01_completed.sse`](fixtures/agent-streaming/01_completed.sse) | "하도급법상 대금 지급 기한은 며칠이야?" → 분해·Routing·생성 → result(COMPLETED). 중간에 `: ping` 포함 |
| [`fixtures/agent-streaming/02_needs_input.sse`](fixtures/agent-streaming/02_needs_input.sse) | "그 업체 괜찮아?" → 분해 clarify → route skipped → result(NEEDS_INPUT, 선택지) |
| [`fixtures/agent-streaming/03_timeout_error.sse`](fixtures/agent-streaming/03_timeout_error.sse) | 생성 중 처리 제한 시간 초과 → error(UPSTREAM_TIMEOUT), completed 없이 종료 |

- Frontend: 이 파일을 이벤트 사이에 지연을 두고 흘려보내는 목 서버나 Vite 미들웨어로 단계 표시를 만든다. `elapsed_ms` 차이만큼 기다리면 실제 속도와 비슷하다
- Backend: Python 경로 대신 이 파일을 내려주는 목 서버로 SSE 읽기·전달·`result` 검증·저장 규칙을 테스트한다

## 10. 확인 방법

```bash
# Python (stub 모드)
curl -N -X POST http://127.0.0.1:9200/internal/query-executions/stream \
  -H "X-Internal-Api-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"request_id":"<uuid>","query":"하도급법 제4조 보여줘","history":[],"context":{"selected_supplier_ids":[]}}'
```

| 확인할 것 | 기대 |
| --- | --- |
| 정상 질문 | stage 6개(분해·Routing·생성 시작/끝) 뒤 result 1개 |
| clarify 질문 (fixture 09) | decompose started/completed → route skipped → generate started/completed → result(NEEDS_INPUT) |
| 같은 요청의 기존 경로 결과 | 스트리밍 result의 data와 필드가 같다 (request_id 제외) |
| 처리 제한 시간 초과 | error(UPSTREAM_TIMEOUT) 1개로 끝난다 |
| 실행 전 검증 실패 | SSE가 아니라 JSON 400 |
| 클라이언트가 중간에 끊음 | 서버 task 취소, 대화에 저장되지 않음 |

## 11. 진행 순서

| 순서 | 할 일 | 담당 | 시작 조건 |
| --- | --- | --- | --- |
| 1 | 이 계약 문서 머지 | Agent·Backend·Frontend 승인 | - |
| 2 | Python `/internal/query-executions/stream` | Agent | 1 |
| 3 | Spring 중계 경로 2개 | Backend | 1. 9절 예시 파일로 Python 없이 시작 |
| 4 | Vue 단계 표시 | Frontend | 1. 9절 예시 파일로 Python·Spring 없이 시작 |
| 5 | 세 개를 이어 통합 확인 (10절) | 전원 | 2~4 |
