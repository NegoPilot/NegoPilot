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
| 모델 호출 | Anthropic SDK 구조화 출력(`output_config.format`). 스키마는 `DecompositionResult`에서 만든다 |
| 프롬프트 | intent 등록표(`intents.yaml`)로 자동 생성. 용어 사전, 근거·되묻기 규칙, 예시 포함 ([`prompt.py`](src/negopilot_agent/decomposition/prompt.py)) |
| 프롬프트 캐시 | 시스템 프롬프트는 요청마다 같게 두고 캐시한다. 오늘 날짜와 최근 대화는 사용자 메시지에 넣는다 |
| 형식 오류 | 필드 사이 규칙 위반을 오류 내용과 함께 알려 주고 1회 재시도. 그래도 실패하면 `status: "failed"` |
| 거절·잘림 | `stop_reason`이 `refusal`이나 `max_tokens`면 `failed` |
| 안전 분류기 거절 | 서버 측 대체 모델(`fallbacks: "default"`)로 다시 실행. `NEGOPILOT_DECOMPOSER_FALLBACKS=0`으로 끈다 |
| API 장애 | SDK 재시도 후에도 실패하면 예외를 그대로 올린다 (Harness의 Recovery가 처리) |

### 설정 (`.env.example`)

| 환경 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `NEGOPILOT_DECOMPOSER_MODEL` | `claude-opus-5-5` | 분해 모델 |
| `NEGOPILOT_DECOMPOSER_EFFORT` | `low` | 분류에 가까운 작업이라 low로 시작. 평가 결과를 보고 조정 |
| `NEGOPILOT_DECOMPOSER_FALLBACKS` | `1` | 거절 시 대체 모델 사용 |

API 키는 `ANTHROPIC_API_KEY` 또는 `ant auth login` 프로필을 씁니다.

## 실행

```bash
uv sync
uv run python -m pytest -q                       # 단위 테스트 (가짜 HTTP 전송, 비용 없음)
uv run python scripts/eval_decomposition.py      # 실제 모델 평가 (비용 발생)
```

평가는 `shared/fixtures/decomposition`의 질문을 넣고 상태, 하위 질문 개수, intent, 엔티티, 의존 관계를 기대 결과와 비교합니다.
결과는 `eval_results/`에 저장되니, 프롬프트·모델·effort를 바꾸면 다시 돌려 전후를 비교하세요.
프롬프트 예시는 평가용 fixtures와 겹치지 않게 따로 두었습니다 (테스트로 확인).
