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
| 백엔드 | `openai`(기본, Chat Completions, `response_format`에 strict JSON 스키마 전달) 또는 `anthropic`(Claude 구조화 출력) 또는 `ollama`(native `/api/chat`). 스키마는 모두 `DecompositionResult`에서 만든다 ([`backends.py`](src/negopilot_agent/decomposition/backends.py)) |
| 프롬프트 | intent 등록표(`intents.yaml`)로 자동 생성. 용어 사전, 근거·되묻기 규칙, 예시 포함 ([`prompt.py`](src/negopilot_agent/decomposition/prompt.py)) |
| 프롬프트 캐시 | 시스템 프롬프트는 요청마다 같게 두고 캐시한다. 오늘 날짜와 최근 대화는 사용자 메시지에 넣는다 |
| 형식 오류 | 필드 사이 규칙 위반을 오류 내용과 함께 알려 주고 1회 재시도. 그래도 실패하면 `status: "failed"` |
| 의존 관계 정리 | `inputs`로 값을 받지 않는 `depends_on`은 코드로 지운다 (병렬로 돌 질문이 순서대로 묶이지 않게) |
| 거절·잘림 | Claude는 `stop_reason`이 `refusal`·`max_tokens`, OpenAI는 `refusal`이 있거나 `finish_reason`이 `length`면 `failed` |
| 안전 분류기 거절 | 서버 측 대체 모델(`fallbacks: "default"`)로 다시 실행. `NEGOPILOT_DECOMPOSER_FALLBACKS=0`으로 끈다 |
| API 장애 | SDK 재시도 후에도 실패하면 예외를 그대로 올린다 (Harness의 Recovery가 처리) |

### 설정 (`.env.example`)

| 환경 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `NEGOPILOT_DECOMPOSER_BACKEND` | `openai` | `openai` 또는 `anthropic` 또는 `ollama` |
| `NEGOPILOT_DECOMPOSER_MODEL` | `claude-opus-5-5` / `gpt-4.1-mini` / `qwen3:8b` | 백엔드별 기본 모델 |
| `NEGOPILOT_DECOMPOSER_EFFORT` | `low` | Claude 전용. 분류에 가까운 작업이라 low로 시작. 평가 결과를 보고 조정 |
| `NEGOPILOT_DECOMPOSER_FALLBACKS` | `1` | Claude 전용. 거절 시 대체 모델 사용 |

OpenAI(기본)는 `OPENAI_API_KEY`가 필요합니다. 셸에 같은 이름의 변수가 있으면 `.env`보다 먼저 쓰입니다.
Claude는 `ANTHROPIC_API_KEY`(또는 `ant auth login` 프로필)가 필요합니다. 평가 스크립트는 `agent/.env`가 있으면 읽습니다 (git에 올라가지 않음).

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
   | `law_check`의 `get_article` 조문 전문 | 전체 조문의 `citation` | 출처 URL과 인용문 및 본문이 확인되면 O. 출처가 불완전하면 X |
   | `law_article`의 `get_article` | `citation` | O |
   | `referenced_articles` (flags·`check_offer`의 `law_refs`) | `citation` | O. 조회 실패한 조문은 검사 항목의 "관련 조문"에 번호만 남음 |

   조문 전문이 3,000자(`law_article`은 6,000자)를 넘으면 앞에서 자르지 않고 **관련 있는 항을 골라** 넣습니다 (`select_paragraphs`). 하위 질문, 같은 조문의 검색 결과, 그 조문을 건 검사 항목 문구와 겹치는 글자 쌍이 많은 항부터 넣고, 생략한 항 번호를 본문 끝에 적습니다. 예: `check_offer`가 건 제2조(4,162자)에서 "주요 원재료" 정의(16번째 항)가 빠지지 않습니다.
2. **초안**: 모델이 근거 id를 붙여 씁니다 (`{"answer", "used_evidence"}` 구조화 출력). 따로 실행된 하위 질문 결과(예: 1위 업체 + 5% 인하 법 검토)는 한 흐름으로 잇습니다
3. **근거 검사** ([`verify.py`](src/negopilot_agent/generation/verify.py)): 없거나 인용할 수 없는 근거 id, 근거·질문에 없는 숫자(단위가 붙은 것), 근거에 없는 조문 번호를 찾습니다. 있으면 오류를 알려 주고 1회 다시 쓰게 합니다
4. 그래도 남으면 그 값을 `[확인 필요]`로 가립니다 (`verification.masked`)
5. `[E3]` → `[1]` 번호로 바꾸고 `citations`에 출처 목록을 담습니다

`Answer.evidence`와 `Answer.verification`은 평가와 디버깅에 사용할 근거 및 검증 정보입니다. 아래 v2 HTTP 응답에서도 이 정보를 보존하므로 클라이언트가 확인할 수 있습니다.

테스트는 [`shared/fixtures/routing`](../shared/fixtures/routing/) 11개(실제 MCP 서버로 Routing한 결과)를 입력으로 씁니다. 모델은 가짜 백엔드로 바꿔서 비용이 없고 MCP 서버도 필요 없습니다.

### 설정

| 환경 변수 | 기본값 |
| --- | --- |
| `NEGOPILOT_GENERATOR_BACKEND` | `openai`. `anthropic`과 `ollama`도 선택 가능 |
| `NEGOPILOT_GENERATOR_MODEL` | anthropic은 `claude-opus-5-5`. openai는 `gpt-4.1-mini`. ollama는 `qwen3:8b` |
| `NEGOPILOT_GENERATOR_EFFORT` | `medium` |
| `NEGOPILOT_GENERATOR_FALLBACKS` | `1` |

OpenAI를 선택한 Generation도 `OPENAI_API_KEY`를 사용합니다. 분해와 Generation은 서로 다른 백엔드를 선택할 수 있습니다. EFFORT와 FALLBACKS는 Claude 전용 설정입니다.

## LangSmith 추적 (`negopilot_agent.tracing`)

질문 하나가 분해 → MCP 도구 호출 → 생성을 거치는 과정을 LangSmith에서 한 실행으로 볼 수 있습니다. **기본은 꺼져 있고**, `agent/.env`에 아래 두 값이 모두 있을 때만 켜집니다.

```bash
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=...          # 각자 발급. 커밋하지 않는다
LANGSMITH_PROJECT=negopilot    # 선택
```

| 기록되는 것 | 어떻게 |
| --- | --- |
| 그래프 실행과 노드(`decompose_query`, `route`, `generate`) | LangGraph가 자동으로 기록 |
| LLM 호출의 입출력과 토큰 | OpenAI·Anthropic 클라이언트를 `langsmith.wrappers`로 감싼다 (`OpenAIBackend`, `AnthropicBackend`). Ollama는 기록하지 않는다 |
| MCP 도구 호출 (`business-mcp.check_offer` 등) | `Router._call`이 tool 실행으로 기록. 실패한 호출은 오류로 남는다 |
| HTTP 실행의 `request_id`와 실행 모드 | 실행 메타데이터 (`negopilot.query`) |

- **외부 전송:** 추적을 켜면 질문, 협력사 데이터, 법령 근거가 LangSmith로 전송됩니다. 내용은 빼고 실행 구조·시간·토큰만 남기려면 `LANGSMITH_HIDE_INPUTS=true`, `LANGSMITH_HIDE_OUTPUTS=true`를 함께 씁니다.
- **꺼져 있을 때:** 클라이언트를 감싸지 않고 기록도 만들지 않으므로, 동작과 성능이 추적 도입 전과 같습니다.
- **테스트와 CI:** `tests/conftest.py`가 항상 끕니다. Docker Compose 기본 실행도 켜지 않습니다.
- **평가 스크립트:** `ask.py`와 평가 스크립트도 `agent/.env`를 읽습니다. 그래서 추적을 켜 두면 평가 실행도 전부 기록됩니다. 대량 평가 때는 끄는 것을 권장합니다.

### LangSmith 데이터셋과 실험 (`scripts/eval_langsmith.py`)

Generation 평가 세트(dev, validation)를 LangSmith 데이터셋으로 올리고, 실제 그래프를 실험으로 돌려 개선 전후를 문항 단위로 비교합니다. `LANGSMITH_API_KEY`가 필요하고, `run`은 모델 키와 MCP 서버 두 개도 필요합니다 (`eval_pipeline.py`와 같음).

```bash
uv run python scripts/eval_langsmith.py upload --set validation      # 데이터셋 negopilot-generation-validation 만들기·동기화
uv run python scripts/eval_langsmith.py run --set validation --description "mask 수정 후"
uv run python scripts/eval_langsmith.py run --set validation --repeat 3 --no-judge   # 채점 없이 3회 반복
```

| 항목 | 내용 |
| --- | --- |
| 데이터셋 | 예제 하나 = 정답 파일 항목 하나. 입력은 질문만, 기대 출력은 `kind`, `points`, `must_evidence`, 정답 분해의 intent. `upload`를 다시 실행하면 바뀐 예제만 고치고, 로컬에 없는 예제는 지우지 않고 경고만 합니다 |
| 실험 이름 | `<세트>-<커밋 7자리>[-dirty]` (`--prefix`로 앞부분 변경). 메타데이터에 분해·생성·채점 모델을 남깁니다 |
| 실행 기록 | `run` 동안만 추적을 켜서 예제마다 노드·LLM·MCP 호출이 함께 남습니다 |
| 평가자 | `eval_pipeline.py`와 같은 기준: `kind_ok`, `intent_match`, `grounded_when_needed`, `must_evidence`, `retried`, `masked`, `tool_errors`, `execution_error`. `--no-judge`가 아니면 LLM 채점으로 `point_coverage`, `points_covered`, `points_total`, `unsupported_claims` |
| 채점 분리 | 대상은 그래프만 실행하고 채점은 평가자에서 따로 합니다. 실험의 시간·토큰은 Agent 실행만입니다 |
| 끝에 출력하는 요약 | 정답 포인트 커버율은 `eval_pipeline.py` 보고서처럼 포인트 수로 가중합니다 (LangSmith 화면의 `point_coverage` 평균은 문항 평균이라 다를 수 있음) |

- 개선 전후 비교는 LangSmith에서 두 실험을 골라 비교 화면으로 봅니다. 모델, 협력사 DB, 법령 색인을 같게 두고 비교합니다.
- 검증 세트 실험의 오답을 보고 프롬프트를 고치지 않습니다. 프롬프트 수정은 dev 세트로 합니다.
- 질문, 협력사 데이터, 법령 근거, 답변이 LangSmith로 전송됩니다.

## 실행

```bash
uv sync
uv run python -m pytest -q                       # 단위 테스트 (가짜 HTTP 전송·가짜 MCP, 비용 없음)
uv run python scripts/ask.py "RFQ-2026-001 협력사 추천해줘"            # 전체 그래프 (서버 2개를 먼저 띄움, OpenAI 비용 발생)
uv run python scripts/ask.py --backend openai "하도급법 제4조 보여줘"   # 분해·생성 모두 gpt-4.1-mini
uv run python scripts/route_fixtures.py          # fixtures를 실제 MCP 서버로 Routing (서버 2개를 먼저 띄움)
uv run python scripts/route_fixtures.py --save   # 그 결과를 shared/fixtures/routing/에 저장 (계약을 바꿨을 때)
uv run python scripts/eval_decomposition.py --backend openai   # gpt-4.1-mini 평가 (비용 있음)
uv run python scripts/eval_decomposition.py --backend anthropic --limit 3   # Claude 평가 (ANTHROPIC_API_KEY, 비용 발생)
uv run python scripts/eval_decomposition.py --backend openai --set validation --repeat 3   # 검증 세트 3회 평균
uv run python scripts/eval_generation.py --offline                  # Generation 근거 지표만 (모델 호출 없음)
uv run python scripts/eval_generation.py --backend openai --repeat 3   # Generation 생성 + 채점 (비용 있음)
uv run python scripts/eval_generation.py --backend openai --repeat 3 --set validation   # Generation 검증 세트
uv run python scripts/route_generation_set.py    # Generation 검증 세트 입력을 실제 MCP 서버로 다시 Routing (서버 2개를 먼저 띄움)
uv run python scripts/bench_latency.py --set validation             # 전체 그래프 단계별 시간 (서버 2개, 비용 있음)
```

`ask.py`는 답변 끝에, `bench_latency.py`는 질문마다 단계별 시간(분해·Routing·생성 노드, LLM 호출 수와 토큰, MCP 도구 호출 수)을 출력합니다.
측정은 `negopilot_agent.timing.collect()` 안에서만 기록되고, v2 HTTP API는 요청마다 같은 요약을 `negopilot_agent.api.generation_execution` 로거(INFO)에 남깁니다.

Generation 평가는 `shared/fixtures/routing` 11개를 입력으로 쓰고, 정답은 [`eval_data/generation_gold.json`](eval_data/generation_gold.json)에 있습니다 (기대 답변 종류, 근거에 꼭 있어야 할 문구, 답변에 담길 정답 포인트).
`--set validation`은 [`eval_data/generation_validation/`](eval_data/generation_validation/) 15개(질문, 정답 분해 결과, 실제 MCP Routing 결과를 한 파일에)를 입력으로, [`eval_data/generation_validation_gold.json`](eval_data/generation_validation_gold.json)을 정답으로 씁니다. 분해 검증 세트와 같이 점수만 보고, 오답을 보고 프롬프트를 고치지 않습니다.
`--offline`은 모델 없이 근거 포함률과 프롬프트 크기만 재고, 기본 모드는 답변 종류, 첫 시도 근거 검사 통과율, 재생성·가림 비율, 정답 포인트 커버율과 근거 없는 주장 수(gpt-4.1-mini 채점), 생성 시간을 잽니다.
`--set dev_law`는 법령의 예외·단서·간주 규정을 묻는 개발용 질문 3개입니다. 생성 프롬프트를 고칠 때 검증 세트 대신 봅니다. `--repeat`을 2 이상 주면 회차별 정답 포인트와 회차 간 표준편차, 평균의 표준오차를 함께 출력합니다. 같은 코드도 회차마다 2~6pp 흔들리므로, 변경 전후 비교는 같은 날 같은 입력으로 6회 이상 돌리고 차이가 표준오차보다 충분히 클 때만 개선으로 봅니다.

평가는 fixture의 질문을 넣고 상태, 하위 질문 개수, intent, 엔티티, 의존 관계를 기대 결과와 비교합니다.
`--set dev`(기본, `shared/fixtures/decomposition` 11개)는 프롬프트를 고칠 때 보는 개발용이고, `--set validation`(`shared/fixtures/validation` 53개)은 점수만 확인하는 검증용입니다. 검증 세트의 오답을 보고 프롬프트를 고치지 않습니다.
temperature 0이어도 결과가 흔들리므로 변경 전후를 비교할 때는 `--repeat 3` 이상을 씁니다.
원본 결과는 `eval_results/`(git 제외)에 저장되고, 공유할 결과는 [`eval_reports/`](eval_reports/)에 정리합니다.
프롬프트·모델·effort를 바꾸면 다시 돌려 전후를 비교하세요.

| 날짜 | 모델 | 완전 일치 | 보고서 |
| --- | --- | --- | --- |
| 2026-10-20 | llama3.1:8b (로컬, 백엔드는 #49에서 제거) | 개발용 64% (7/11) | [2026-10-20_llama3.1-8b.md](eval_reports/2026-10-20_llama3.1-8b.md) |
| 2026-10-21 | gpt-4.1-mini | 개발용 55% (1회, #40 반영 전) | [2026-10-21_gpt-4.1-mini.md](eval_reports/2026-10-21_gpt-4.1-mini.md) |
| 2026-10-21 | gpt-4.1-mini / llama3.1:8b | **검증용 88% / 33%** (3회 평균) | [2026-10-21_validation-set.md](eval_reports/2026-10-21_validation-set.md) |
| 2026-10-21 | gpt-4.1-mini (#72 프롬프트 보강) | 검증용 79% → **94~97%**, 개발용 21개 67% → 90% (3회 평균) | PR 본문 |
| 2026-10-21 | gpt-4.1-mini (#98 간결한 출력 형식) | 정확도 유지, 분해 시간 2.58초 → 1.66초, 출력 토큰 518 → 344 | PR 본문 |
| 2026-10-21 | gpt-4.1-mini (#114 검증 세트 50개로 확대) | 검증용 50개 92% | PR 본문 |
| 2026-10-22 | gpt-4.1-mini (#151 check_offer v2 사례 3개 추가) | 검증용 53개 94% (1회), 새 사례 3/3 | [2026-10-22_check-offer-v2-pipeline.md](eval_reports/2026-10-22_check-offer-v2-pipeline.md) |

프롬프트 예시는 개발용·검증용 fixture와 겹치지 않게 따로 두었습니다 (테스트로 확인).

## Python 내부 HTTP API (`negopilot_agent.api`)

전체 서비스를 Docker로 실행하려면 [로컬 Docker 안내](../docs/LOCAL_DOCKER.md)를 따른다. 루트 `compose.yaml`은 두 단계 모두 OpenAI `gpt-4.1-mini`를 기본으로 쓰는 live 구성이다. 기본 OpenAI 실행에는 루트 `.env`에 모델 키를 넣어야 한다. `compose.test.yaml`을 추가한 별도 테스트 구성은 모델 키 없이 stub 채팅을 실행한다. 아래 uvicorn 명령은 Docker 시작기를 거치지 않는 독립 실행 방법이다.

FastAPI 진입점은 `negopilot_agent.api.app:app`입니다. [Generation HTTP 계약 v0.3](../docs/contracts/agent-generation.md)과 [협력사 live 조회 계약](../docs/contracts/supplier-lookup.md) 및 [관리자 법령 계약](../docs/contracts/admin-laws.md)의 Python 내부 경로를 제공합니다. 브라우저는 Spring의 공개 API를 호출하고 Spring이 내부 키를 붙여 Python에 요청합니다.

| Python 내부 경로 | 동작 |
| --- | --- |
| `POST /internal/query-executions` | 실제 `build_graph`와 `AnswerGenerator`를 실행하고 `schema_version: "0.3"` 응답에 최종 `Answer`를 포함 |
| `GET /internal/business/catalog`, `/internal/suppliers`, `/internal/suppliers/{supplier_id}` | 실행 모드와 관계없이 business-mcp의 협력사 데이터 조회 |
| `/internal/admin/laws/**` | 관리자 법령 조회와 최신화 |

과거 fixture 기반 v0.1 질의 실행(`/internal/v1/query-executions`)과 v0.2 협력사 조회·템플릿 대화(`/internal/v1/business`, `/internal/v1/suppliers`, `/internal/v1/chat/responses`)는 #141에서 제거했습니다. 생성 답변을 사용할 클라이언트는 Spring의 `/api/agent/queries` 또는 `/api/conversations` 경로를 사용합니다.

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
curl --fail-with-body http://127.0.0.1:9200/internal/query-executions \
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

위 표는 독립 실행의 기본값입니다. 루트 Docker의 live 구성은 Python 제한을 300초로 지정하고 Spring 제한을 310초로 지정합니다. 테스트 override는 각각 30초와 35초를 사용합니다.

Spring을 함께 실행하려면 별도 터미널의 저장소 루트에서 다음 명령을 사용합니다. Java 21과 Maven 3.6.3 이상이 필요합니다. 상세 설정은 [backend 안내](../backend/README.md)를 참고합니다.

```bash
export AGENT_EXECUTION_MODE=stub
export AGENT_INTERNAL_API_KEY='replace-with-a-local-test-key'
export AGENT_BASE_URL='http://127.0.0.1:9200'
mvn -f backend/pom.xml -B -ntp spring-boot:run
```

내부 키는 두 서버의 환경 변수에 둡니다. 프런트엔드에 넣지 않습니다. 처리 제한을 변경할 때는 Python의 제한 시간과 Spring의 응답 대기 시간을 함께 확인합니다. HTTP 요청을 자동으로 재시도하지 않습니다.

### 협력사 조회

`/internal/business/catalog`, `/internal/suppliers`, `/internal/suppliers/{supplier_id}`는 실행 모드와 관계없이 `BUSINESS_MCP_URL`의 business-mcp를 호출합니다. 모델은 호출하지 않습니다. 검색은 `process`가 필수이고 알 수 없는 parameter와 중복 parameter는 400 `VALIDATION_ERROR`입니다. 기업 ID는 `SUP-[0-9]{4,18}` 형식입니다. 상세 규칙은 [협력사 live 조회 계약](../docs/contracts/supplier-lookup.md)을 따릅니다.

```bash
curl --fail-with-body --get http://127.0.0.1:9200/internal/suppliers \
  -H "X-Internal-Api-Key: ${AGENT_INTERNAL_API_KEY}" \
  --data-urlencode 'process=다이캐스팅' \
  --data-urlencode 'material=ADC12'
```

### live 설정과 검증 범위

`AGENT_EXECUTION_MODE=live`에서는 `/internal/query-executions`가 QueryDecomposer와 실제 McpToolCaller를 연결합니다. 위의 분해 및 Routing 환경 변수를 재사용합니다. 현재 기본 모델은 [DecomposerSettings](src/negopilot_agent/decomposition/decomposer.py)의 `DEFAULT_MODELS`에 정의돼 있습니다. `anthropic`은 `claude-opus-5-5`이며 `openai`는 `gpt-4.1-mini`입니다. [모델 백엔드](src/negopilot_agent/decomposition/backends.py)는 이 설정을 받아 호출합니다.

직접 uvicorn을 실행하면서 기본 모델을 사용하려면 `NEGOPILOT_DECOMPOSER_MODEL`을 설정하지 않아야 합니다. `agent/.env.example`의 `NEGOPILOT_DECOMPOSER_MODEL=`을 그대로 불러오면 빈 문자열이 선택됩니다. 이 경우 HTTP 경로는 503 `UPSTREAM_UNAVAILABLE`을 반환합니다. 기본 모델을 선택할 때는 환경 파일에서 해당 줄을 제거하거나 환경 파일을 불러온 뒤 아래 명령을 실행합니다.

```bash
unset NEGOPILOT_DECOMPOSER_MODEL
```

Claude에는 유효한 인증이 필요합니다. OpenAI에는 Python 서버 환경의 `OPENAI_API_KEY`가 필요합니다. 이 키는 Spring이나 프런트엔드에 전달하지 않습니다. Routing에는 `BUSINESS_MCP_URL`과 `LAW_MCP_URL`에 지정한 서버가 필요합니다.

HTTP 경로는 SDK의 전송 오류 재시도를 끕니다. OpenAI 클라이언트도 `AsyncOpenAI(max_retries=0)`으로 만들고 요청 종료 시 제한 시간 안에서 `await client.close()`를 수행합니다. SDK의 재시도와 비동기 자원 해제 방식은 [공식 Python SDK 문서](https://developers.openai.com/api/reference/python)를 참고합니다. 분해 코어의 출력 형식 오류 재시도는 기존 규칙을 유지합니다. v2 전체 그래프 경로는 Generation 설정도 사용합니다.

비용 없는 단위 테스트는 저장소 루트에서 다음 명령으로 실행합니다. 테스트는 가짜 모델과 ToolCaller 및 ASGI 전송을 사용합니다.

```bash
cd agent
uv run --locked python -m pytest tests/test_api.py tests/test_supplier_lookup_live.py tests/test_http_openapi_contract.py -q
```

위 명령은 앱 공통 동작과 협력사 조회 및 고정 OpenAPI 명세를 검사합니다. 실제 LLM 연결과 실제 MCP 서버 및 메일 발송과 DB seed를 검증하지 않습니다. 전체 그래프 검사는 아래 안내를 따릅니다. 로그인과 승인 및 영구 대화 저장은 이 HTTP 범위에 포함하지 않습니다.

실제 Spring과 Python을 임시 포트에서 함께 실행하는 검사는 [HTTP 통합 검증 안내](../scripts/integration/README.md)를 따릅니다. 이 검사도 stub 응답을 사용합니다.

### v2 전체 그래프 실행과 schema v0.3

`POST /internal/query-executions`는 최신 `build_graph`의 분해와 Routing 및 Generation을 한 번 실행합니다. 최종 답변은 실제 `AnswerGenerator`가 만듭니다. 기본 모드는 `live`이며 모델이나 MCP 연결 실패를 숨기거나 `stub` 응답으로 자동 전환하지 않습니다. 내부 키와 `AGENT_REQUEST_TIMEOUT_SECONDS` 설정을 재사용하며 전체 그래프에 하나의 처리 제한 시간을 적용합니다.

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
curl --fail-with-body http://127.0.0.1:9200/internal/query-executions \
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

대화 저장이 필요하면 Spring의 `/api/conversations`를 사용합니다. Python의 v2 실행 자체는 상태를 저장하지 않습니다. Spring은 대화를 PostgreSQL에 저장하고 최신 20개 메시지를 전달합니다. 같은 대화에 생성 중인 요청이 있으면 `409 CONVERSATION_BUSY`로 거절합니다. 저장된 긴 assistant 본문은 유지하며 모델에 전달할 때만 4000 code point로 제한합니다. HTTP 오류나 `execution_status: FAILED`에서는 기존 메시지와 문맥을 유지합니다. 상세 규칙은 [이력 v0.4 계약](../docs/contracts/history.md)을 따릅니다.

### v2 live 모델 선택과 비용 없는 검사

분해와 Generation의 백엔드는 독립적으로 설정합니다. 각 단계에서 `anthropic` 또는 `openai` 또는 `ollama`를 선택할 수 있습니다. 백엔드를 지정하지 않으면 기본값인 `openai`를 사용합니다.

| 환경 변수 | 기본값 |
| --- | --- |
| `NEGOPILOT_DECOMPOSER_BACKEND` | `openai` |
| `NEGOPILOT_DECOMPOSER_MODEL` | anthropic은 `claude-opus-5-5`. openai는 `gpt-4.1-mini`. ollama는 `qwen3:8b` |
| `NEGOPILOT_GENERATOR_BACKEND` | `openai` |
| `NEGOPILOT_GENERATOR_MODEL` | anthropic은 `claude-opus-5-5`. openai는 `gpt-4.1-mini`. ollama는 `qwen3:8b` |
| `NEGOPILOT_DECOMPOSER_EFFORT` | `low` |
| `NEGOPILOT_GENERATOR_EFFORT` | `medium` |
| `NEGOPILOT_DECOMPOSER_FALLBACKS` | `1` |
| `NEGOPILOT_GENERATOR_FALLBACKS` | `1` |

직접 uvicorn을 실행할 때 기본 모델을 사용하려면 두 MODEL 환경 변수를 빈 문자열로 지정하지 않고 미설정으로 둡니다. 환경 파일을 불러온 뒤 아래 명령으로 제거할 수 있습니다. FALLBACKS는 기존 Claude 서버 측 대체 모델 설정이며 HTTP 실행 모드를 stub으로 전환하는 설정이 아닙니다.

```bash
unset NEGOPILOT_DECOMPOSER_MODEL NEGOPILOT_GENERATOR_MODEL
```

Docker 시작기 `deploy/local/agent_start.py`는 MODEL이 미설정이거나 빈 문자열이면 공급자 기본값을 적용합니다. 공백만 있는 모델 값은 거절합니다. 서버를 열기 전에 선택한 공급자와 모델 및 필수 키 설정을 검사하며 키가 없으면 시작을 중단합니다. 네트워크 호출을 하지 않으므로 키의 유효성이나 모델 사용 권한 및 실제 연결 성공은 확인하지 않습니다. 이 시작 전 검사는 Docker 전용이며 Agent 코어와 직접 uvicorn 실행의 동작은 바꾸지 않습니다.

두 단계 모두 기본값인 OpenAI(gpt-4.1-mini)를 쓰려면 Python 서버를 시작할 셸에서 아래 값을 설정합니다. BACKEND 변수는 생략해도 openai이며, 다른 값이 남아 있을 수 있어 예시에서는 명시합니다. `OPENAI_API_KEY`에는 유효한 키를 지정해야 합니다. 위의 서버 시작 예시에서 실행 모드를 `live`로 바꾸고 서버를 시작합니다.

```bash
export AGENT_EXECUTION_MODE=live
export OPENAI_API_KEY='replace-with-your-openai-api-key'
export NEGOPILOT_DECOMPOSER_BACKEND=openai
export NEGOPILOT_GENERATOR_BACKEND=openai
unset NEGOPILOT_DECOMPOSER_MODEL NEGOPILOT_GENERATOR_MODEL
```

한 단계만 Claude로 바꾸려면 해당 BACKEND 변수만 `anthropic`으로 지정하고 해당 MODEL 변수만 제거합니다. 다른 단계의 설정은 그대로 사용합니다. 예를 들어 분해는 OpenAI를 쓰고 Generation은 Claude를 쓸 수 있습니다. 실제 유료 모델 호출은 이 문서 갱신 과정에서 검사하지 않았습니다.

모델 실행 시간이 기본 제한보다 길면 `AGENT_REQUEST_TIMEOUT_SECONDS`로 Python 제한을 조정합니다. 같은 연결을 쓰는 Spring은 `AGENT_EXECUTION_MODE=live`와 같은 내부 키를 설정하고 `AGENT_RESPONSE_TIMEOUT_SECONDS=310`처럼 Python보다 긴 제한을 사용합니다. MCP 주소는 실제 실행 중인 서버 주소로 지정합니다. business-mcp에는 PostgreSQL 연결이 필요하고 law-mcp에는 저장된 법령 인덱스가 필요합니다.

모델 설정이나 연결 실패는 503 `UPSTREAM_UNAVAILABLE`입니다. 전체 처리 제한 시간 초과는 504 `UPSTREAM_TIMEOUT`입니다. live 실패에 테스트 답변을 반환하지 않습니다.

가짜 의존성을 사용하는 전체 단위 검사는 저장소 루트에서 다음 명령으로 실행합니다. v2 stub 검사는 실제 그래프와 AnswerGenerator의 검증 흐름을 확인합니다. 실제 LLM과 MCP 연결 결과는 별도로 확인해야 합니다.

```bash
cd agent
uv sync --locked
uv run --locked python -m pytest -q
```

### 채팅에서 바로 질문하기

기업을 미리 선택하지 않아도 Spring 대화 API로 질문할 수 있습니다. 지역만으로 기업을 검색하거나 이름으로 기업을 찾고 관련 법령을 조회합니다. 기업 후보가 여러 곳이거나 필수 거래 조건이 없을 때만 추가로 묻습니다. 조회 0건과 연결 실패는 추가 입력 부족과 구분합니다.

[직접 질문 계약](../docs/contracts/direct-chat.md)에 질문별 처리 기준과 stub 예시 및 추가 질문 이후의 대화 규칙을 정리했습니다. 기업명 조회에는 최신 business-mcp의 `find_suppliers`가 필요합니다. Agent와 business-mcp를 함께 갱신해 실행하세요.


## 회원 및 데이터 관리 연결

회원 인증과 승인 및 세션과 개인 채팅 이력 저장은 Spring이 담당한다. Python의 Generation 요청과 응답은 v0.3을 유지한다. [회원 계약](../docs/contracts/membership.md)과 [이력 계약](../docs/contracts/history.md)을 참고한다.

`/internal/admin/laws` 아래의 버전 조회와 최신화 API는 `AGENT_INTERNAL_API_KEY`로 인증한다. 조회는 law-mcp의 읽기 도구를 사용한다. 최신화는 별도의 HTTP 제어 경로를 사용하며 Agent 도구로 노출하지 않는다. stub 모드에서도 관리자 법령 조회와 최신화는 실제 law-mcp에 연결한다.

- `LAW_MCP_URL`은 법령 서버의 `/mcp` 주소다. 기본값은 `http://localhost:9103/mcp`다.
- `LAW_ADMIN_API_KEY`는 Python과 법령 서버 사이의 별도 관리 키다. 프런트로 전달하지 않는다.
- law-mcp는 `LAW_RUNTIME_DIR`에 검증된 데이터와 작업 상태를 저장한다. 단일 서버 프로세스로 실행한다.
- 법령 최신화의 실제 수집은 law-mcp에서 수행한다. GitHub API 제한 회피가 필요하면 법령 서버에 `GITHUB_TOKEN`을 설정한다.

수동 법령 등록과 편집 API는 제공하지 않는다. 경로와 오류 및 응답 형식은 [법령 관리 계약](../docs/contracts/admin-laws.md)에 있다.


### Ollama native 공급자

`ollama`는 기존 분해와 Routing 및 Generation 그래프를 그대로 실행하는 live 공급자입니다. [Ollama 공식 `/api/chat`](https://docs.ollama.com/api/chat)에 `stream=false`와 `think=false`를 보내고 `temperature=0`을 사용합니다. [구조화 출력 문서](https://docs.ollama.com/capabilities/structured-outputs)에 따라 `format`에는 분해의 `DecompositionResult` 또는 생성의 `GenerationDraft` Pydantic JSON Schema를 전달합니다. 출력 JSON의 계약 검사와 근거 검증은 기존 코어가 수행합니다.

현재 Ollama 문법 변환기의 [숫자 정규식 shorthand 문제](https://github.com/ggml-org/llama.cpp/issues/27580)에 맞춰 전송할 스키마 사본의 `pattern`에서 `\d`만 `[0-9]`로 확장합니다. 원본 Pydantic 모델과 후검증은 변경하지 않습니다. `anyOf`와 참조 및 열거값과 숫자 범위 제약도 그대로 전달합니다.

로컬 Ollama에 모델을 준비한 후 Python 서버를 실행하는 셸에서 설정합니다. 모델 다운로드와 서버 시작은 Agent가 자동으로 수행하지 않습니다.

```bash
export AGENT_EXECUTION_MODE=live
export NEGOPILOT_DECOMPOSER_BACKEND=ollama
export NEGOPILOT_GENERATOR_BACKEND=ollama
export OLLAMA_BASE_URL=http://127.0.0.1:11434
unset NEGOPILOT_DECOMPOSER_MODEL NEGOPILOT_GENERATOR_MODEL
```

| 환경 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `NEGOPILOT_DECOMPOSER_MODEL` | `qwen3:8b` | 분해 공급자가 ollama일 때의 모델 이름 |
| `NEGOPILOT_GENERATOR_MODEL` | `qwen3:8b` | 생성 공급자가 ollama일 때의 모델 이름 |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | HTTP 또는 HTTPS origin. 인증정보와 경로 및 쿼리와 fragment는 허용하지 않음 |
| `OLLAMA_TIMEOUT_SECONDS` | `300` | 각 모델 HTTP 호출의 제한 시간. 0 초과 3600 이하. 연결은 최대 5초 |
| `OLLAMA_NUM_CTX` | `32768` | 입력과 출력이 공유하는 문맥 크기. 1024부터 131072까지. 설치 모델이 지원하는 범위 안에서 설정 |
| `OLLAMA_NUM_PREDICT` | `4096` | 모델 출력 토큰 상한. 양수이며 NUM_CTX보다 작아야 함. 코어 max_tokens가 더 작으면 그 값을 사용 |

Ollama 호출에는 OpenAI 또는 Anthropic 키를 전달하지 않습니다. 환경변수의 HTTP 프록시와 리다이렉트도 사용하지 않습니다. 공급자를 선택하지 않으면 기존 기본값 `openai`가 유지됩니다. 두 단계의 공급자는 각각 선택할 수 있습니다.

연결 오류와 HTTP 오류 및 잘못된 Ollama 응답 envelope는 HTTP 경로에서 `503 UPSTREAM_UNAVAILABLE`로 처리합니다. 시간초과는 `504 UPSTREAM_TIMEOUT`입니다. 전송 오류를 자동 재시도하거나 stub으로 전환하지 않습니다. `done_reason=length`는 기존 코어의 실패 결과로 처리합니다. JSON 계약 위반은 기존 코어의 최대 두 번 시도 규칙을 유지합니다. 각 HTTP 요청에서 처음 사용한 모델 클라이언트를 만들고 요청 종료 시 닫습니다.

`OLLAMA_TIMEOUT_SECONDS`는 전체 Agent 실행 제한을 늘리지 않습니다. 로컬 모델의 속도에 맞춰 `AGENT_REQUEST_TIMEOUT_SECONDS`와 Spring 및 프런트의 제한 시간을 별도로 정렬해야 합니다. 이 설정은 프롬프트나 모델 가중치를 바꾸지 않으며 답변 품질을 보장하지 않습니다.

`tests/test_ollama_backend.py`는 실제 모델 호출 없이 native 요청 스키마와 옵션 및 v1/v2 선택과 클라이언트 종료 및 503/504 처리와 자동 재시도 부재를 검사합니다.

## 전체 그래프 품질·토큰·시간 평가

`eval_generation.py`는 저장된 Routing으로 생성만 평가합니다. `eval_pipeline.py`는
Generation 개발 11개/검증 12개의 질문과 정답을 재사용하지만, 실제 분해부터 MCP 조회와
생성까지 새로 실행하고 최종 답변을 채점합니다. DB·MCP 서버와 모델 키가 필요하며
모델 및 채점 호출 비용이 발생합니다. 검증 세트는 점수 확인에만 사용합니다.

```bash
# agent 폴더에서 실행. 서버 준비는 위 실행 안내 참고
uv run python scripts/eval_pipeline.py --set dev --limit 3 --repeat 3
uv run python scripts/eval_pipeline.py --set validation --repeat 3
# 추가 채점 비용 없이 답변 종류·필수 근거·토큰·시간만 기록
uv run python scripts/eval_pipeline.py --set dev --limit 3 --no-judge
```

결과는 `eval_results/pipeline-*.json`에 질문마다 저장됩니다. `rows`에는 실제 분해·Routing·
전체 답변(인용·검증 포함), 필수 근거 포함, 재생성·가림, 토큰과 시간 기록이 있습니다.
`summary.agent`와 `summary.judge`는 토큰·호출 수·시간을 분리합니다. Agent 시간에는
채점 시간을 포함하지 않으며, 금액 비용은 산출하지 않습니다. 실패 호출의 기록도 보존합니다.

요약에는 다음 지표도 있습니다 (#116).

| 키 | 뜻 |
| --- | --- |
| `intent_match` | 실제 분해의 intent 목록이 정답 분해와 같은 비율 |
| `grounded_when_needed` | 근거가 필요한 답변(개념 설명·범위 밖 제외)에 근거 표시가 붙은 비율 |
| `cross_judge` | `--cross-judge MODEL`을 주면 같은 답변을 다른 모델로 한 번 더 채점해 포인트 커버, 근거 없는 주장 수, 포인트 판정 일치율을 비교 |

```bash
uv run python scripts/eval_pipeline.py --set validation --cross-judge gpt-5.4-mini
```

`point_coverage`는 모든 정답 포인트를 채점한 경우에만 값이 나옵니다. 채점 생략/오류가
있으면 null이며, `scored_points`와 `expected_points`로 평가 범위를 확인합니다.
`point_coverage_lower_bound`는 미채점과 실행 실패를 0점으로 둔 하한입니다.
답변 종류가 틀린 경우 포인트는 0점입니다. 생성 모델과 같은 모델로 채점할 수 있으므로
중요한 법령 답변은 사람이 별도로 확인해야 합니다.

현재 평가는 단일 질문 기준입니다. 대화 이력과 동시 사용자 부하는 포함하지 않습니다.
기업 데이터가 바뀌면 기업별 정답과 저장된 기준 데이터도 별도로 검토해야 합니다.

실행 전 business DB의 관리 스키마도 준비해야 합니다. 기존 DB에서 `status` 또는
`certification_source` 열이 없다는 오류가 나면 평가를 멈추고 DB를 백업한 뒤
[`migrate_management.py`](../mcp-servers/business-mcp/scripts/migrate_management.py)를
스키마 변경 계정의 명시적인 `DATABASE_URL`로 실행합니다. 기존 DB에서는 seed를 다시
실행하지 않습니다. 자세한 절차는 [business-mcp SETUP](../mcp-servers/business-mcp/SETUP.md)을
따릅니다. 적용 후 MCP의 `get_supplier`, `rank_suppliers`가 성공하는지 확인하고 평가합니다.
`summary.tool_errors`는 그래프가 오류를 처리해 응답을 돌려준 경우에도 도구 오류를 셉니다.
