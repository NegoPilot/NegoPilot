# agent

NegoPilot의 LangGraph Agent입니다. 지금은 **쿼리 분해** 노드가 들어 있습니다.

```
[사용자 질문] → decompose_query → route → (MCP 병렬·순차 호출) → generate
```

## 쿼리 분해 (`negopilot_agent.decomposition`)

사용자 질문을 [계약](../docs/contracts/query-decomposition.md)에 맞는 `DecompositionResult`로 나눕니다.

```python
from negopilot_agent.decomposition import QueryDecomposer, make_decompose_node

result = await QueryDecomposer().decompose("RFQ-2026-001 협력사 추천해줘")

graph.add_node("decompose_query", make_decompose_node())   # 상태: query, history → decomposition
```

| 동작 | 내용 |
| --- | --- |
| 백엔드 | `anthropic`(기본, Claude 구조화 출력) 또는 `ollama`(로컬 모델, 비용 없음. `format`에 JSON 스키마 전달). 스키마는 둘 다 `DecompositionResult`에서 만든다 ([`backends.py`](src/negopilot_agent/decomposition/backends.py)) |
| 프롬프트 | intent 등록표(`intents.yaml`)로 자동 생성. 용어 사전, 근거·되묻기 규칙, 예시 포함 ([`prompt.py`](src/negopilot_agent/decomposition/prompt.py)) |
| 프롬프트 캐시 | 시스템 프롬프트는 요청마다 같게 두고 캐시한다. 오늘 날짜와 최근 대화는 사용자 메시지에 넣는다 |
| 형식 오류 | 필드 사이 규칙 위반을 오류 내용과 함께 알려 주고 1회 재시도. 그래도 실패하면 `status: "failed"` |
| 의존 관계 정리 | `inputs`로 값을 받지 않는 `depends_on`은 코드로 지운다 (병렬로 돌 질문이 순서대로 묶이지 않게) |
| 거절·잘림 | Claude는 `stop_reason`이 `refusal`·`max_tokens`, Ollama는 `done_reason`이 `length`(출력 상한 2,048토큰)면 `failed` |
| 안전 분류기 거절 | 서버 측 대체 모델(`fallbacks: "default"`)로 다시 실행. `NEGOPILOT_DECOMPOSER_FALLBACKS=0`으로 끈다 |
| API 장애 | SDK 재시도 후에도 실패하면 예외를 그대로 올린다 (Harness의 Recovery가 처리) |

### 설정 (`.env.example`)

| 환경 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `NEGOPILOT_DECOMPOSER_BACKEND` | `anthropic` | `anthropic` 또는 `ollama` |
| `NEGOPILOT_DECOMPOSER_MODEL` | `claude-opus-5-5` / `llama3.1:8b` | 백엔드별 기본 모델 |
| `NEGOPILOT_DECOMPOSER_EFFORT` | `low` | Claude 전용. 분류에 가까운 작업이라 low로 시작. 평가 결과를 보고 조정 |
| `NEGOPILOT_DECOMPOSER_FALLBACKS` | `1` | Claude 전용. 거절 시 대체 모델 사용 |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama 주소 |

Claude는 `ANTHROPIC_API_KEY`(또는 `ant auth login` 프로필)가 필요합니다. 평가 스크립트는 `agent/.env`가 있으면 읽습니다 (git에 올라가지 않음).
Ollama는 `ollama pull llama3.1:8b`로 모델을 받아 두면 키 없이 씁니다.

## 실행

```bash
uv sync
uv run python -m pytest -q                       # 단위 테스트 (가짜 HTTP 전송, 비용 없음)
uv run python scripts/eval_decomposition.py --backend ollama   # 로컬 모델 평가 (비용 없음)
uv run python scripts/eval_decomposition.py --limit 3          # Claude 평가 (비용 발생)
```

평가는 `shared/fixtures/decomposition`의 질문을 넣고 상태, 하위 질문 개수, intent, 엔티티, 의존 관계를 기대 결과와 비교합니다.
원본 결과는 `eval_results/`(git 제외)에 저장되고, 공유할 결과는 [`eval_reports/`](eval_reports/)에 정리합니다.
프롬프트·모델·effort를 바꾸면 다시 돌려 전후를 비교하세요.

| 날짜 | 모델 | 완전 일치 | 보고서 |
| --- | --- | --- | --- |
| 2026-10-20 | llama3.1:8b (로컬) | 64% (7/11) | [2026-10-20_llama3.1-8b.md](eval_reports/2026-10-20_llama3.1-8b.md) |
프롬프트 예시는 평가용 fixtures와 겹치지 않게 따로 두었습니다 (테스트로 확인).
