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

### 설정 (`.env.example`)

| 환경 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `NEGOPILOT_DECOMPOSER_BACKEND` | `anthropic` | `anthropic`, `ollama`, `openai` |
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

`Answer.evidence`와 `Answer.verification`은 화면에 쓰지 않고 평가·디버깅용으로 남깁니다.

테스트는 [`shared/fixtures/routing`](../shared/fixtures/routing/) 11개(실제 MCP 서버로 Routing한 결과)를 입력으로 씁니다. 모델은 가짜 백엔드로 바꿔서 비용이 없고 MCP 서버도 필요 없습니다.

### 설정

| 환경 변수 | 기본값 |
| --- | --- |
| `NEGOPILOT_GENERATOR_BACKEND` | `anthropic` (또는 `ollama`, `openai`) |
| `NEGOPILOT_GENERATOR_MODEL` | anthropic `claude-opus-5-5`, ollama `llama3.1:8b`, openai `gpt-4.1-mini` |
| `NEGOPILOT_GENERATOR_EFFORT` | `medium` |
| `NEGOPILOT_GENERATOR_FALLBACKS` | `1` |

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
```

평가는 `shared/fixtures/decomposition`의 질문을 넣고 상태, 하위 질문 개수, intent, 엔티티, 의존 관계를 기대 결과와 비교합니다.
원본 결과는 `eval_results/`(git 제외)에 저장되고, 공유할 결과는 [`eval_reports/`](eval_reports/)에 정리합니다.
프롬프트·모델·effort를 바꾸면 다시 돌려 전후를 비교하세요.

| 날짜 | 모델 | 완전 일치 | 보고서 |
| --- | --- | --- | --- |
| 2026-10-20 | llama3.1:8b (로컬) | 64% (7/11) | [2026-10-20_llama3.1-8b.md](eval_reports/2026-10-20_llama3.1-8b.md) |
프롬프트 예시는 평가용 fixtures와 겹치지 않게 따로 두었습니다 (테스트로 확인).
