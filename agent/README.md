# agent

NegoPilot의 LangGraph Agent입니다. **쿼리 분해**, **Routing**, **Generation** 노드와 세 노드를 잇는 그래프가 들어 있습니다.

```
[사용자 질문] → decompose_query → route → (MCP 병렬·순차 호출) → generate
```

## 전체 그래프 (`negopilot_agent.graph`)

```python
from negopilot_agent.graph import build_graph

app = build_graph()                                   # 부품을 생략하면 환경 변수 설정을 따른다
state = await app.ainvoke({"query": "RFQ-2026-001 협력사 추천해줘", "history": []})
state["answer"]                                       # Answer (kind, text, citations, options ...)
```

| 동작 | 내용 |
| --- | --- |
| 상태 | `query`, `history` → `decomposition` → `routing` → `answer` ([`graph.py`](src/negopilot_agent/graph.py)의 `AgentState`) |
| 분기 | 분해가 `clarify`·`failed`면 `route`를 건너뛰고 `generate`로 간다 (되묻기·실패 안내) |
| 부품 교체 | `build_graph(decomposer=..., caller=..., generator=...)`. 테스트는 가짜 분해·가짜 MCP·가짜 생성으로 세 단계를 한 번에 돈다 ([`tests/test_graph.py`](tests/test_graph.py)) |

질문 하나를 처음부터 끝까지 돌려 보려면 MCP 서버 두 개를 띄우고 `scripts/ask.py`를 씁니다 (아래 실행).

## 쿼리 분해 (`negopilot_agent.decomposition`)

사용자 질문을 [계약](../docs/contracts/query-decomposition.md)에 맞는 `DecompositionResult`로 나눕니다.

```python
from negopilot_agent.decomposition import QueryDecomposer, make_decompose_node

result = await QueryDecomposer().decompose("RFQ-2026-001 협력사 추천해줘")

graph.add_node("decompose_query", make_decompose_node())   # 상태: query, history → decomposition
```

| 동작 | 내용 |
| --- | --- |
| 백엔드 | `anthropic`(기본, Claude 구조화 출력), `ollama`(로컬 모델, 비용 없음. `format`에 JSON 스키마 전달), `openai`(Chat Completions, `response_format`에 strict JSON 스키마 전달). 스키마는 모두 `DecompositionResult`에서 만든다 ([`backends.py`](src/negopilot_agent/decomposition/backends.py)) |
| 프롬프트 | intent 등록표(`intents.yaml`)로 자동 생성. 용어 사전, 근거·되묻기 규칙, 예시 포함 ([`prompt.py`](src/negopilot_agent/decomposition/prompt.py)) |
| 프롬프트 캐시 | 시스템 프롬프트는 요청마다 같게 두고 캐시한다. 오늘 날짜와 최근 대화는 사용자 메시지에 넣는다 |
| 형식 오류 | 필드 사이 규칙 위반을 오류 내용과 함께 알려 주고 1회 재시도. 그래도 실패하면 `status: "failed"` |
| 의존 관계 정리 | `inputs`로 값을 받지 않는 `depends_on`은 코드로 지운다 (병렬로 돌 질문이 순서대로 묶이지 않게) |
| 거절·잘림 | Claude는 `stop_reason`이 `refusal`·`max_tokens`, Ollama는 `done_reason`이 `length`(출력 상한 2,048토큰), OpenAI는 `refusal`이 있거나 `finish_reason`이 `length`면 `failed` |
| 안전 분류기 거절 | 서버 측 대체 모델(`fallbacks: "default"`)로 다시 실행. `NEGOPILOT_DECOMPOSER_FALLBACKS=0`으로 끈다 |
| API 장애 | SDK 재시도 후에도 실패하면 예외를 그대로 올린다 (Harness의 Recovery가 처리) |

분해와 Generation의 Ollama 백엔드는 `prepare_ollama_schema`를 공유합니다. Ollama에 보내는 스키마 사본에서 subquery의 `id`와 `rfq_id` 및 `article_no`와 `as_of`에 쓰이는 네 숫자 pattern의 `\d`만 `[0-9]`로 바꿔 이 네 필드에 생성하는 숫자를 ASCII `0`부터 `9`로 제한합니다. `pattern` 제약과 `format`의 JSON 스키마 전달은 유지합니다. 공용 Pydantic 모델과 후검증 및 Anthropic과 OpenAI용 스키마는 변경하지 않습니다.

### 설정 (`.env.example`)

| 환경 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `NEGOPILOT_DECOMPOSER_BACKEND` | `anthropic` | `anthropic` 또는 `ollama` 또는 `openai` |
| `NEGOPILOT_DECOMPOSER_MODEL` | `claude-opus-5-5` / `llama3.1:8b` / `gpt-4.1-mini` | 백엔드별 기본 모델 |
| `NEGOPILOT_DECOMPOSER_EFFORT` | `low` | Claude 전용. 분류에 가까운 작업이라 low로 시작. 평가 결과를 보고 조정 |
| `NEGOPILOT_DECOMPOSER_FALLBACKS` | `1` | Claude 전용. 거절 시 대체 모델 사용 |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama 주소 |

Claude는 `ANTHROPIC_API_KEY`(또는 `ant auth login` 프로필)가 필요합니다. 평가 스크립트는 `agent/.env`가 있으면 읽습니다 (git에 올라가지 않음).
Ollama는 `ollama pull llama3.1:8b`로 모델을 받아 두면 키 없이 씁니다.
OpenAI는 `OPENAI_API_KEY`가 필요합니다. 셸에 같은 이름의 변수가 있으면 `.env`보다 먼저 쓰입니다.

## Routing (`negopilot_agent.routing`)

`DecompositionResult`를 받아 `intents.yaml`에서 서버·도구를 찾아 부르고, 하위 질문별 결과를 `RouteResult`로 돌려줍니다.

```python
from negopilot_agent.routing import Router, McpToolCaller, make_route_node

result = await Router(McpToolCaller()).route(decomposition)

graph.add_node("route", make_route_node())   # 상태: decomposition → routing
```

| 동작 | 내용 |
| --- | --- |
| 실행 순서 | `parallel_groups()` 순서. 같은 묶음은 병렬(`asyncio.gather`), 다음 묶음은 앞 묶음이 끝난 뒤 |
| 인자 | 엔티티 이름 = 도구 인자명. 다른 것만 바꾼다 (`search_suppliers`의 `top_n` → `limit`). `price_change_pct`는 넘기지 않는다 ([`router.py`](src/negopilot_agent/routing/router.py)) |
| `law_check` | `search_regulation(query=질문, laws=[law])` → 찾은 조문 최대 3개를 `get_article`로 전문 조회 |
| 결과 대응표 | intent별로 결과에서 목록과 값을 꺼내 `top1`, `top(n)`, `all`, `by_name("이름")`을 해석 ([`extract.py`](src/negopilot_agent/routing/extract.py)). 값이 여러 개면 하나씩 부른다 (최대 10번) |
| 도구 없는 intent | `general_knowledge`, `out_of_scope` → 같은 이름의 status, `status: planned`(`draft_email`) → `unsupported`. 도구를 부르지 않는다 |
| 필수 인자 없음 | 도구를 부르지 않고 `missing_args`와 빠진 엔티티 이름 (Generation이 되묻기) |
| 앞 결과 0건 | 그 하위 질문은 `empty`, 이후 묶음은 `skipped`, `halted: true` |
| 도구 오류 | `error`와 오류 내용. 그 결과에 의존하는 하위 질문만 `skipped` |
| 분해 `clarify`·`failed` | 아무것도 부르지 않고 `status: "not_routed"` |

출력 형식은 [Routing 결과 계약](../docs/contracts/routing.md)(`negopilot_shared.RouteResult`)입니다. Generation이 status별로 할 일, 근거 인용 규칙도 계약 문서에 있습니다.
결과에 붙은 `law_refs`(`rank_suppliers` flags, `check_offer`)는 `get_article`로 미리 조회해 `referenced_articles`에 넣습니다.

### 설정

| 환경 변수 | 기본값 |
| --- | --- |
| `BUSINESS_MCP_URL` | `http://localhost:9102/mcp` |
| `LAW_MCP_URL` | `http://localhost:9103/mcp` |

## Generation (`negopilot_agent.generation`)

`DecompositionResult`와 `RouteResult`를 받아 사용자에게 보여 줄 `Answer`를 만듭니다. status별 처리와 인용 규칙은 [Routing 결과 계약](../docs/contracts/routing.md) 1·3절을 따릅니다.

```python
from negopilot_agent.generation import AnswerGenerator, make_generate_node

answer = await AnswerGenerator().generate(decomposition, routing)

graph.add_node("generate", make_generate_node())   # 상태: decomposition, routing → answer
```

| 경우 | 처리 | `Answer.kind` |
| --- | --- | --- |
| Routing `not_routed` + 분해 `clarify` | `clarification.question`과 선택지 (모델 안 부름) | `clarify` |
| Routing `not_routed` + 분해 `failed` | 다시 질문해 달라는 안내와 예시 (모델 안 부름) | `failed` |
| `out_of_scope`만 있음 | 거절하고 할 수 있는 일 안내 (모델 안 부름) | `refusal` |
| 답할 결과 없음, `missing_args` 있음 | `missing`을 사람 말로 바꿔 되물음 (`payment_days` → "결제 기간(일)") | `clarify` |
| 답할 결과 없음 (`empty`, `error`, `unsupported`) | 하위 질문별 안내 | `no_result` |
| `ok`가 하나라도 있음 | 근거로 답변, [1] 형식 인용. 나머지 status는 답변 끝 안내 | `answer` |
| `general_knowledge` | 도구 없이 답변, `grounded: false` (화면에 "근거 자료 없음") | `answer` |

하위 질문별 안내 문구 (답변 끝에 `※`로 모음)

| status | 문구 |
| --- | --- |
| `unsupported` | '메일 써줘': 메일 작성은 아직 지원하지 않습니다. |
| `empty` | '이번 주 마감 RFQ': 조건에 맞는 결과가 없습니다. |
| `error` | 'SUP-0002' 부분은 자료를 가져오지 못했습니다. |
| `skipped` | 원인(`empty`, `error`)을 이미 안내했으면 말하지 않고, 아니면 짧게 한 줄 |
| `assumptions` | 가정: … |

`message`, `halt_reason`, 도구 `error` 원문은 화면에 내보내지 않고 로그로만 남깁니다.

모델로 답할 때의 흐름

1. **근거 펼치기** ([`evidence.py`](src/negopilot_agent/generation/evidence.py)): 도구 결과를 근거 단위(`E1`, `E2` ...)로 나눕니다. `check_offer`는 판정과 검사 항목별로, `rank_suppliers`는 순위별로 나눕니다. 법령 근거는 아래처럼 인용 여부(`citable`)를 나눕니다

   | 근거 | 출처 이름 | 인용 |
   | --- | --- | --- |
   | `search_regulation` 결과 | 항 단위 `citation` 그대로 | O |
   | `law_check`의 `get_article` 조문 전문 | `citation` | X (설명할 때 참고만) |
   | `law_article`의 `get_article` | `citation` | O |
   | `referenced_articles` (flags·`check_offer`의 `law_refs`) | `citation` | O. 조회 실패한 조문은 검사 항목의 "관련 조문"에 번호만 남음 |
2. **초안**: 모델이 근거 id를 붙여 씁니다 (`{"answer", "used_evidence"}` 구조화 출력). 따로 실행된 하위 질문 결과(예: 1위 업체 + 5% 인하 법 검토)는 한 흐름으로 잇습니다
3. **근거 검사** ([`verify.py`](src/negopilot_agent/generation/verify.py)): 없거나 인용할 수 없는 근거 id, 근거·질문에 없는 숫자(단위가 붙은 것), 근거에 없는 조문 번호를 찾습니다. 있으면 오류를 알려 주고 1회 다시 쓰게 합니다
4. 그래도 남으면 그 값을 `[확인 필요]`로 가립니다 (`verification.masked`)
5. `[E3]` → `[1]` 번호로 바꾸고 `citations`에 출처 목록을 담습니다

`Answer.evidence`와 `Answer.verification`은 평가와 디버깅에 사용할 근거 및 검증 정보입니다. 아래 v2 HTTP 응답에서도 이 정보를 보존하므로 클라이언트가 확인할 수 있습니다.

테스트는 [`shared/fixtures/routing`](../shared/fixtures/routing/) 11개(실제 MCP 서버로 Routing한 결과)를 입력으로 씁니다. 모델은 가짜 백엔드로 바꿔서 비용이 없고 MCP 서버도 필요 없습니다.

### 설정

| 환경 변수 | 기본값 |
| --- | --- |
| `NEGOPILOT_GENERATOR_BACKEND` | `anthropic`. `ollama`와 `openai`도 선택 가능 |
| `NEGOPILOT_GENERATOR_MODEL` | anthropic은 `claude-opus-5-5`. ollama는 `llama3.1:8b`. openai는 `gpt-4.1-mini` |
| `NEGOPILOT_GENERATOR_EFFORT` | `medium` |
| `NEGOPILOT_GENERATOR_FALLBACKS` | `1` |

OpenAI를 선택한 Generation도 `OPENAI_API_KEY`를 사용합니다. 분해와 Generation은 서로 다른 백엔드를 선택할 수 있습니다. EFFORT와 FALLBACKS는 Claude 전용 설정입니다.

## 실행

```bash
uv sync
uv run python -m pytest -q                       # 단위 테스트 (가짜 HTTP 전송·가짜 MCP, 비용 없음)
uv run python scripts/ask.py "RFQ-2026-001 협력사 추천해줘"            # 전체 그래프 (서버 2개를 먼저 띄움, Claude 비용 발생)
uv run python scripts/ask.py --backend ollama "하도급법 제4조 보여줘"   # 분해·생성 모두 로컬 모델 (비용 없음)
uv run python scripts/ask.py --backend openai "하도급법 제4조 보여줘"   # 분해·생성 모두 gpt-4.1-mini
uv run python scripts/route_fixtures.py          # fixtures를 실제 MCP 서버로 Routing (서버 2개를 먼저 띄움)
uv run python scripts/route_fixtures.py --save   # 그 결과를 shared/fixtures/routing/에 저장 (계약을 바꿨을 때)
uv run python scripts/eval_decomposition.py --backend ollama   # 로컬 모델 평가 (비용 없음)
uv run python scripts/eval_decomposition.py --backend openai   # gpt-4.1-mini 평가 (비용 있음)
uv run python scripts/eval_decomposition.py --limit 3          # Claude 평가 (비용 발생)
uv run python scripts/eval_decomposition.py --backend openai --set validation --repeat 3   # 검증 세트 3회 평균
```

평가는 fixture의 질문을 넣고 상태, 하위 질문 개수, intent, 엔티티, 의존 관계를 기대 결과와 비교합니다.
`--set dev`(기본, `shared/fixtures/decomposition` 11개)는 프롬프트를 고칠 때 보는 개발용이고, `--set validation`(`shared/fixtures/validation` 24개)은 점수만 확인하는 검증용입니다. 검증 세트의 오답을 보고 프롬프트를 고치지 않습니다.
temperature 0이어도 결과가 흔들리므로 변경 전후를 비교할 때는 `--repeat 3` 이상을 씁니다.
원본 결과는 `eval_results/`(git 제외)에 저장되고, 공유할 결과는 [`eval_reports/`](eval_reports/)에 정리합니다.
프롬프트·모델·effort를 바꾸면 다시 돌려 전후를 비교하세요.

| 날짜 | 모델 | 완전 일치 | 보고서 |
| --- | --- | --- | --- |
| 2026-10-20 | llama3.1:8b (로컬) | 개발용 64% (7/11) | [2026-10-20_llama3.1-8b.md](eval_reports/2026-10-20_llama3.1-8b.md) |
| 2026-10-21 | gpt-4.1-mini | 개발용 55% (1회, #40 반영 전) | [2026-10-21_gpt-4.1-mini.md](eval_reports/2026-10-21_gpt-4.1-mini.md) |
| 2026-10-21 | gpt-4.1-mini / llama3.1:8b | **검증용 88% / 33%** (3회 평균) | [2026-10-21_validation-set.md](eval_reports/2026-10-21_validation-set.md) |

프롬프트 예시는 개발용·검증용 fixture와 겹치지 않게 따로 두었습니다 (테스트로 확인).

## Python 내부 HTTP API (`negopilot_agent.api`)

FastAPI 진입점은 `negopilot_agent.api.app:app`입니다. [쿼리 실행 HTTP 계약 v0.1](../docs/contracts/agent-http.md)과 [협력사 대화 HTTP 계약 v0.2](../docs/contracts/supplier-chat.md) 및 [Generation HTTP 계약 v0.3](../docs/contracts/agent-generation.md)의 Python 내부 경로를 제공합니다. 브라우저는 Spring의 공개 API를 호출하고 Spring이 내부 키를 붙여 Python에 요청합니다.

기존 v0.1의 `/internal/v1/query-executions`는 `QueryDecomposer`와 `Router`를 실행해 구조화된 결과를 반환합니다. 기존 v0.2의 `/internal/v1/chat/responses`는 fixture를 조회해 템플릿 응답을 반환합니다. 이 두 기존 실행 경로는 `AnswerGenerator`와 전체 그래프를 호출하지 않으므로 `generation_status: NOT_IMPLEMENTED`를 유지합니다. 이 값은 코어 Generation의 구현 여부와 별개입니다.

새 `/internal/v2/query-executions`는 실제 `build_graph`와 `AnswerGenerator`를 실행하고 `schema_version: "0.3"` 응답에 최종 `Answer`를 포함합니다. 생성 답변을 사용할 클라이언트는 Spring의 `/api/v2/agent/queries` 또는 `/api/v2/conversations` 경로를 사용합니다. 기존 v0.1과 v0.2 경로 및 응답 형식은 호환성을 위해 유지합니다.

### stub 서버 시작

Python 3.12 이상과 uv가 필요합니다. 저장소 루트에서 다음 명령을 실행합니다. `AGENT_INTERNAL_API_KEY`의 예시 문자열은 로컬 키로 교체합니다. Spring에도 같은 값을 설정합니다.

```bash
cd agent
uv sync --locked
export AGENT_EXECUTION_MODE=stub
export AGENT_INTERNAL_API_KEY='replace-with-a-local-test-key'
export AGENT_REQUEST_TIMEOUT_SECONDS=30
uv run uvicorn negopilot_agent.api.app:app --host 127.0.0.1 --port 9200
```

이 명령은 `.env`를 자동으로 읽지 않습니다. 환경 변수를 내보낸 셸에서 서버를 시작해야 합니다. fixture는 저장소의 `shared/fixtures/decomposition/`과 `docs/contracts/fixtures/supplier-chat.json`을 읽으므로 이 디렉터리를 함께 유지합니다.

다른 터미널에 같은 내부 키를 설정한 뒤 상태와 실행 응답을 확인합니다. health에는 인증이 필요하지 않습니다. 다른 내부 경로는 키가 없거나 다르면 401 `UNAUTHORIZED`를 반환합니다.

```bash
export AGENT_INTERNAL_API_KEY='replace-with-a-local-test-key'
curl --fail http://127.0.0.1:9200/health
curl --fail-with-body http://127.0.0.1:9200/internal/v1/query-executions \
  -H "X-Internal-Api-Key: ${AGENT_INTERNAL_API_KEY}" \
  -H 'Content-Type: application/json' \
  --data '{"request_id":"11111111-1111-4111-8111-111111111111","query":"하도급법 제4조 보여줘","history":[]}'
```

`GET /health`의 `{"status":"ok"}`는 HTTP 프로세스의 응답을 확인하는 값입니다. 모델이나 MCP의 연결 상태까지 검사하지 않습니다.

### Python과 Spring 설정

| 적용 대상 | 환경 변수 | 기본값 | 설명 |
| --- | --- | --- | --- |
| 양쪽 | `AGENT_EXECUTION_MODE` | `live` | fixture 검증은 양쪽 모두 `stub`을 명시 |
| 양쪽 | `AGENT_INTERNAL_API_KEY` | 빈 값 | Python 내부 경로에서 검증할 공통 키 |
| Python | `AGENT_REQUEST_TIMEOUT_SECONDS` | `30` | 처리 제한 시간. 양의 유한한 초 단위 값 |
| Python | `OPENAI_API_KEY` | 미설정 | live 분해나 Generation에서 `openai`를 선택하면 필요한 API 키 |
| Spring | `AGENT_BASE_URL` | `http://127.0.0.1:9200` | Python origin. URL 경로를 붙이지 않음 |
| Spring | `AGENT_CONNECT_TIMEOUT_SECONDS` | `2` | 연결 및 연결 풀 대기 제한 시간 |
| Spring | `AGENT_RESPONSE_TIMEOUT_SECONDS` | `35` | 응답 및 소켓 읽기 대기 제한 시간 |
| Spring | `SERVER_ADDRESS` | `127.0.0.1` | Spring 수신 주소 |
| Spring | `SERVER_PORT` | `8080` | Spring 수신 포트 |

Spring을 함께 실행하려면 별도 터미널의 저장소 루트에서 다음 명령을 사용합니다. Java 21과 Maven 3.6.3 이상이 필요합니다. 상세 설정은 [backend 안내](../backend/README.md)를 참고합니다.

```bash
export AGENT_EXECUTION_MODE=stub
export AGENT_INTERNAL_API_KEY='replace-with-a-local-test-key'
export AGENT_BASE_URL='http://127.0.0.1:9200'
mvn -f backend/pom.xml -B -ntp spring-boot:run
```

내부 키는 두 서버의 환경 변수에 둡니다. 프런트엔드에 넣지 않습니다. 처리 제한을 변경할 때는 Python의 제한 시간과 Spring의 응답 대기 시간을 함께 확인합니다. HTTP 요청을 자동으로 재시도하지 않습니다.

### v0.1 쿼리 실행

`POST /internal/v1/query-executions`의 `request_id`는 필수 UUID입니다. `query`는 공백만 있는 값을 제외한 1..4000자입니다. `history`는 생략할 수 있으며 최대 20개입니다. 각 항목은 `role`과 `content`만 받습니다. role은 `user` 또는 `assistant`입니다. 요청에 정의되지 않은 필드는 400 `VALIDATION_ERROR`로 거절합니다.

stub 모드에서 지원하는 질문은 다음 세 개입니다. 실제 Router에 fixture 분해 결과와 가짜 ToolCaller를 연결합니다. 모델과 MCP 및 DB를 호출하지 않습니다. 반환하는 법령 내용은 테스트용 데이터입니다.

| 질문 | 분해 fixture | `execution_status` |
| --- | --- | --- |
| 하도급법 제4조 보여줘 | `01_law_article.json` | `COMPLETED` |
| 그 업체 괜찮아? | `09_clarify.json` | `NEEDS_INPUT` |
| ㅁㄴㅇㄹ 그거 저번처럼 해줘 | `11_failed.json` | `FAILED` |

다른 stub 질문은 400 `VALIDATION_ERROR`입니다. `COMPLETED`는 분해와 Routing 처리가 끝났다는 뜻입니다. 생성 답변이나 도구 성공을 일괄 보장하는 값은 아닙니다. 상세 결과는 공용 `RouteResult`의 상태와 `referenced_articles`를 포함해 반환합니다. 오류 설명은 HTTP 응답 사본에서 정제합니다. 참조 조문 조회 오류만으로 본래 성공한 outcome을 실패로 바꾸지 않습니다.

### v0.2 협력사 조회와 템플릿 대화

다음 경로는 `stub`에서만 사용할 수 있습니다. `live`에서는 503 `DEMO_MODE_REQUIRED`와 `retryable: false`를 반환합니다. 데이터 원본은 [supplier-chat.json](../docs/contracts/fixtures/supplier-chat.json)입니다. 모든 기업과 수치는 가상 데이터입니다.

| Python 내부 경로 | 동작 |
| --- | --- |
| `GET /internal/v1/business/catalog` | 공정별 소재와 지역 목록 |
| `GET /internal/v1/suppliers` | 필터 조건에 맞는 기업 검색 |
| `GET /internal/v1/suppliers/{supplier_id}` | 선택 기업 상세 조회 |
| `POST /internal/v1/chat/responses` | 선택 기업의 템플릿 응답 |

기업 검색은 `process`가 필수입니다. `material`과 `region`은 선택입니다. `iatf_required`의 기본값은 `false`이며 인증 여부 전체를 뜻합니다. `limit`은 1..100이며 기본값은 20입니다. 알 수 없는 query parameter와 중복 parameter는 거절합니다. 기업 ID는 대소문자를 구분하며 `^[A-Za-z0-9_-]{1,100}$` 형식입니다. 존재하지 않는 ID는 404 `SUPPLIER_NOT_FOUND`입니다.

```bash
curl --fail-with-body --get http://127.0.0.1:9200/internal/v1/suppliers \
  -H "X-Internal-Api-Key: ${AGENT_INTERNAL_API_KEY}" \
  --data-urlencode 'process=다이캐스팅' \
  --data-urlencode 'material=ADC12'
curl --fail-with-body http://127.0.0.1:9200/internal/v1/chat/responses \
  -H "X-Internal-Api-Key: ${AGENT_INTERNAL_API_KEY}" \
  -H 'Content-Type: application/json' \
  --data '{"request_id":"22222222-2222-4222-8222-222222222222","conversation_id":"33333333-3333-4333-8333-333333333333","query":"이 회사에 대해 알려줘","history":[],"context":{"selected_supplier_ids":["SUP-0034"]}}'
```

Python 직접 호출에서는 `request_id`와 `conversation_id` 및 `query`와 `history` 및 `context`를 모두 보냅니다. 선택 기업은 최대 한 곳입니다. 지원하는 질문은 아래 세 개입니다.

1. 이 회사에 대해 알려줘
2. 이 회사의 재무 상태를 알려줘
3. 납기와 품질은 어때?

별칭 `이 회사의 재무 상태와 납기 지표를 알려줘`는 개요 응답으로 처리합니다. 기업 미선택 시 기업 선택 안내가 질문 분류보다 우선합니다. 지원하지 않는 질문은 `NEEDS_INPUT`과 지원 질문 목록으로 안내합니다. Python은 대화 이력을 저장하지 않으며 일반적인 후속 질문을 해석하지 않습니다.

응답의 `presentation.renderer`는 `template`입니다. 완료 응답은 선택 기업의 카드 한 개와 이에 연결된 stub 근거 한 개를 포함합니다. 근거의 `executed: false`는 실제 MCP를 호출하지 않았다는 뜻입니다. `on_time_delivery_rate`와 `debt_ratio`는 fixture의 백분율을 그대로 사용합니다. 화면에서 100을 곱하지 않습니다. null과 빈 재무 목록은 정보 없음으로 표시합니다.

대화 생성과 조회 및 저장은 Spring의 `/api/v1/conversations` 경로가 담당합니다. Spring은 메모리에 최대 100개 메시지를 유지하고 최신 20개를 Python에 전달합니다. 재시작하면 대화가 초기화됩니다. Python 직접 호출 예시는 Spring의 대화 생성이나 저장을 수행하지 않습니다.

### live 설정과 검증 범위

`AGENT_EXECUTION_MODE=live`에서는 v0.1 경로가 기존 QueryDecomposer와 실제 McpToolCaller를 연결합니다. 위의 분해 및 Routing 환경 변수를 재사용합니다. 현재 기본 모델은 [DecomposerSettings](src/negopilot_agent/decomposition/decomposer.py)의 `DEFAULT_MODELS`에 정의돼 있습니다. `anthropic`은 `claude-opus-5-5`입니다. `ollama`는 `llama3.1:8b`이며 `openai`는 `gpt-4.1-mini`입니다. [모델 백엔드](src/negopilot_agent/decomposition/backends.py)는 이 설정을 받아 호출합니다.

기본 모델을 사용하려면 `NEGOPILOT_DECOMPOSER_MODEL`을 설정하지 않아야 합니다. `.env.example`의 `NEGOPILOT_DECOMPOSER_MODEL=`을 그대로 불러오면 빈 문자열이 선택됩니다. 이 경우 HTTP 경로는 503 `UPSTREAM_UNAVAILABLE`을 반환합니다. 기본 모델을 선택할 때는 환경 파일에서 해당 줄을 제거하거나 환경 파일을 불러온 뒤 아래 명령을 실행합니다.

```bash
unset NEGOPILOT_DECOMPOSER_MODEL
```

Claude에는 유효한 인증이 필요합니다. Ollama에는 해당 모델과 실행 중인 Ollama 서버가 필요합니다. OpenAI에는 Python 서버 환경의 `OPENAI_API_KEY`가 필요합니다. 이 키는 Spring이나 프런트엔드에 전달하지 않습니다. Routing에는 `BUSINESS_MCP_URL`과 `LAW_MCP_URL`에 지정한 서버가 필요합니다.

HTTP 경로는 SDK의 전송 오류 재시도를 끕니다. OpenAI 클라이언트도 `AsyncOpenAI(max_retries=0)`으로 만들고 요청 종료 시 제한 시간 안에서 `await client.close()`를 수행합니다. SDK의 재시도와 비동기 자원 해제 방식은 [공식 Python SDK 문서](https://developers.openai.com/api/reference/python)를 참고합니다. 분해 코어의 출력 형식 오류 재시도는 기존 규칙을 유지합니다. Generation 환경 변수는 기존 v0.1 실행과 v0.2 템플릿 경로에 사용하지 않습니다. 새 v2 전체 그래프 경로에서는 Generation 설정도 사용합니다.

비용 없는 단위 테스트는 저장소 루트에서 다음 명령으로 실행합니다. 테스트는 가짜 모델과 ToolCaller 및 ASGI 전송을 사용합니다.

```bash
cd agent
uv run --locked python -m pytest tests/test_api.py tests/test_supplier_chat.py tests/test_http_openapi_contract.py -q
```

위 명령은 기존 v0.1과 v0.2 HTTP 계약을 검사합니다. 실제 LLM 연결과 실제 MCP 서버 및 메일 발송과 DB seed를 검증하지 않습니다. v2 전체 그래프 검사는 아래 안내를 따릅니다. 로그인과 승인 및 영구 대화 저장은 이 HTTP 범위에 포함하지 않습니다.

실제 Spring과 Python을 임시 포트에서 함께 실행하는 검사는 [HTTP 통합 검증 안내](../scripts/integration/README.md)를 따릅니다. 이 검사도 stub 응답을 사용합니다.

### v2 전체 그래프 실행과 schema v0.3

`POST /internal/v2/query-executions`는 최신 `build_graph`의 분해와 Routing 및 Generation을 한 번 실행합니다. 최종 답변은 실제 `AnswerGenerator`가 만듭니다. 기본 모드는 `live`이며 모델이나 MCP 연결 실패를 숨기거나 `stub` 응답으로 자동 전환하지 않습니다. 내부 키와 `AGENT_REQUEST_TIMEOUT_SECONDS` 설정을 재사용하며 전체 그래프에 하나의 처리 제한 시간을 적용합니다.

요청의 `request_id`는 필수 UUID입니다. `query`는 공백만 있는 값을 제외한 1..4000 code point이며 입력 원문을 보존합니다. `history`는 생략하면 빈 배열이며 최대 20개입니다. `context`는 생략하면 `{"selected_supplier_ids":[]}`입니다. 정의되지 않은 필드와 명시적인 null은 거절합니다.

`context.selected_supplier_ids`는 기업 ID를 최대 한 개 받습니다. ID 형식은 `^[A-Za-z0-9_-]{1,100}$`입니다. 기업의 재무 수치나 상세 정보를 context에 넣지 않습니다. context는 분해 프롬프트의 별도 JSON 데이터 영역으로 전달됩니다. 원래 질문과 이력을 재작성하지 않습니다. 질문에 다른 기업을 명시하면 선택값으로 덮어쓰지 않습니다. live 모드의 기업 사실은 실제 도구 결과로 확인합니다.

stub 검증은 위의 서버 시작 명령에서 `AGENT_EXECUTION_MODE=stub`을 지정합니다. 모델 백엔드와 ToolCaller의 입출력만 가짜로 바꿉니다. 실제 `build_graph`와 `QueryDecomposer` 및 `Router`와 `AnswerGenerator`를 연결하므로 근거 펼치기와 인용 연결 및 답변 검증도 실행됩니다. fixture 응답을 사용한 검증은 실제 모델이나 MCP의 연결 검증과 구분합니다.

| stub 질문 | 선택 문맥 | 기대 `execution_status` |
| --- | --- | --- |
| 하도급법 제4조 보여줘 | 생략 가능 | `COMPLETED` |
| 그 업체 괜찮아? | 생략 가능 | `NEEDS_INPUT` |
| ㅁㄴㅇㄹ 그거 저번처럼 해줘 | 생략 가능 | `FAILED` |
| 이 회사에 대해 알려줘 | `SUP-0034` | `COMPLETED` |
| 이 회사의 재무 상태를 알려줘 | `SUP-0034` | `COMPLETED` |
| 납기와 품질은 어때? | `SUP-0034` | `COMPLETED` |
| 이 회사의 재무 상태와 납기 지표를 알려줘 | `SUP-0034` | `COMPLETED` |

첫 세 질문은 기존 분해 fixture 01과 09 및 11을 사용합니다. 기업 질문과 별칭은 supplier-chat fixture의 가상 기업 데이터를 사용합니다. 지원하는 stub 질문의 범위가 live 모델의 일반 질문 처리 범위를 뜻하지는 않습니다.

Python 서버가 stub으로 실행 중일 때 다음 요청으로 기업 문맥을 포함한 v2 응답을 확인합니다.

```bash
export AGENT_INTERNAL_API_KEY='replace-with-a-local-test-key'
curl --fail-with-body http://127.0.0.1:9200/internal/v2/query-executions \
  -H "X-Internal-Api-Key: ${AGENT_INTERNAL_API_KEY}" \
  -H 'Content-Type: application/json' \
  --data '{"request_id":"44444444-4444-4444-8444-444444444444","query":"이 회사에 대해 알려줘","history":[],"context":{"selected_supplier_ids":["SUP-0034"]}}'
```

응답은 `schema_version: "0.3"`과 요청 ID 및 `mode`와 실행 상태를 포함합니다. 확정된 `context`와 공용 `decomposition` 및 `routing`도 반환합니다. `answer`에는 코어 Answer의 `kind`와 `text` 및 `citations`와 `options` 및 `grounded`와 `notices` 및 `evidence`와 `verification`을 보존합니다. Python property인 `verification.ok`를 별도 JSON 필드로 추가하지 않습니다.

| `answer.kind` | `execution_status` | `generation_status` | 최상위 error |
| --- | --- | --- | --- |
| `answer` | `COMPLETED` | `COMPLETED` | null |
| `clarify` | `NEEDS_INPUT` | `COMPLETED` | null |
| `refusal` 또는 `no_result` | `COMPLETED` | `COMPLETED` | null |
| 분해가 failed인 `failed` | `FAILED` | `FAILED` | `DECOMPOSITION_FAILED` |
| 그 밖의 `failed` | `FAILED` | `FAILED` | `GENERATION_FAILED` |

일부 Routing 오류가 있어도 코어가 답변을 만들면 완료로 처리합니다. 부분 실패는 `routing`과 `answer.notices`에 남깁니다. 고정 안내도 Generation이 처리한 결과이므로 `COMPLETED`가 항상 모델 호출이나 모든 도구의 성공을 뜻하지는 않습니다. `verification.masked: true`와 검증 오류 목록이 함께 있는 보정 결과도 보존합니다.

대화 저장이 필요하면 Spring의 `/api/v2/conversations`를 사용합니다. Python의 v2 실행 자체는 상태를 저장하지 않습니다. Spring은 같은 대화의 요청을 직렬로 처리하고 최신 20개 메시지를 전달합니다. 저장된 긴 assistant 본문은 유지하며 모델에 전달할 때만 4000 code point로 제한합니다. HTTP 오류나 `execution_status: FAILED`에서는 기존 메시지와 문맥을 유지합니다. 메모리 저장은 재시작하면 초기화됩니다.

### v2 live 모델 선택과 비용 없는 검사

분해와 Generation의 백엔드는 독립적으로 설정합니다. 각 단계에서 `anthropic` 또는 `ollama` 또는 `openai`를 선택할 수 있습니다. 백엔드를 지정하지 않으면 기존 기본값인 `anthropic`을 사용합니다.

| 환경 변수 | 기본값 |
| --- | --- |
| `NEGOPILOT_DECOMPOSER_BACKEND` | `anthropic` |
| `NEGOPILOT_DECOMPOSER_MODEL` | anthropic은 `claude-opus-5-5`. ollama는 `llama3.1:8b`. openai는 `gpt-4.1-mini` |
| `NEGOPILOT_GENERATOR_BACKEND` | `anthropic` |
| `NEGOPILOT_GENERATOR_MODEL` | anthropic은 `claude-opus-5-5`. ollama는 `llama3.1:8b`. openai는 `gpt-4.1-mini` |
| `NEGOPILOT_DECOMPOSER_EFFORT` | `low` |
| `NEGOPILOT_GENERATOR_EFFORT` | `medium` |
| `NEGOPILOT_DECOMPOSER_FALLBACKS` | `1` |
| `NEGOPILOT_GENERATOR_FALLBACKS` | `1` |

기본 모델을 사용하려면 두 MODEL 환경 변수를 빈 문자열로 지정하지 않고 미설정으로 둡니다. 환경 파일을 불러온 뒤 아래 명령으로 제거할 수 있습니다. FALLBACKS는 기존 Claude 서버 측 대체 모델 설정이며 HTTP 실행 모드를 stub으로 전환하는 설정이 아닙니다.

```bash
unset NEGOPILOT_DECOMPOSER_MODEL NEGOPILOT_GENERATOR_MODEL
```

두 단계 모두 OpenAI 기본 모델을 선택하려면 Python 서버를 시작할 셸에서 아래 값을 설정합니다. `OPENAI_API_KEY`에는 유효한 키를 지정해야 합니다. 위의 서버 시작 예시에서 실행 모드를 `live`로 바꾸고 서버를 시작합니다.

```bash
export AGENT_EXECUTION_MODE=live
export OPENAI_API_KEY='replace-with-your-openai-api-key'
export NEGOPILOT_DECOMPOSER_BACKEND=openai
export NEGOPILOT_GENERATOR_BACKEND=openai
unset NEGOPILOT_DECOMPOSER_MODEL NEGOPILOT_GENERATOR_MODEL
```

한 단계만 OpenAI로 바꾸려면 해당 BACKEND 변수만 `openai`로 지정하고 해당 MODEL 변수만 제거합니다. 다른 단계의 설정은 그대로 사용합니다. 예를 들어 분해는 OpenAI를 쓰고 Generation은 Ollama를 쓸 수 있습니다. 실제 유료 모델 호출은 이 문서 갱신 과정에서 검사하지 않았습니다.

Ollama의 `qwen3:8b`를 분해와 생성에 선택하려면 환경 변수로 명시합니다. 아래는 저장소 루트에서 시작하는 실행 예시입니다. 기본 모델은 변경하지 않습니다. 해당 모델을 설치한 Ollama 서버와 필요한 MCP 서버를 별도로 준비해야 합니다. 이 모델의 응답과 연결 상태는 별도로 검증해야 합니다.

```bash
cd agent
uv sync --locked
export AGENT_EXECUTION_MODE=live
export AGENT_INTERNAL_API_KEY='replace-with-a-local-test-key'
export NEGOPILOT_DECOMPOSER_BACKEND=ollama
export NEGOPILOT_DECOMPOSER_MODEL=qwen3:8b
export NEGOPILOT_GENERATOR_BACKEND=ollama
export NEGOPILOT_GENERATOR_MODEL=qwen3:8b
export OLLAMA_BASE_URL=http://localhost:11434
uv run uvicorn negopilot_agent.api.app:app --host 127.0.0.1 --port 9200
```

모델 설정이나 연결 실패는 503 `UPSTREAM_UNAVAILABLE`입니다. 전체 처리 제한 시간 초과는 504 `UPSTREAM_TIMEOUT`입니다. live 실패에 테스트 답변을 반환하지 않습니다.

가짜 의존성을 사용하는 전체 단위 검사는 저장소 루트에서 다음 명령으로 실행합니다. v2 stub 검사는 실제 그래프와 AnswerGenerator의 검증 흐름을 확인합니다. 실제 LLM과 MCP 연결 결과는 별도로 확인해야 합니다.

```bash
cd agent
uv sync --locked
uv run --locked python -m pytest -q
```
