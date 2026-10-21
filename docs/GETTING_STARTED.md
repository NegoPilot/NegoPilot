# 개발 시작 가이드

NegoPilot 팀원이 자기 담당 부분을 개발하기 전에 무엇을 읽고, 무엇부터 만들면 되는지 정리한 문서입니다.
협업 규칙(이슈 → 브랜치 → PR)은 [CONTRIBUTING.md](../CONTRIBUTING.md)를 따릅니다.

## 1. Agent 처리 흐름

```
사용자 질문
  → decompose_query   하위 질문으로 나누고 intent·엔티티를 붙임        [완료]
  → route             intent에 맞는 MCP 도구 호출 (병렬·순차)          [완료]
  → generate          도구 결과로 답변, 근거 인용, 되묻기·거절        [완료]
  (세 노드를 잇는 그래프: agent/src/negopilot_agent/graph.py, 실행: agent/scripts/ask.py)
```

단계 사이에서 주고받는 형식은 **계약 문서**로 정해져 있습니다. 각 단계는 다른 단계의 완성을 기다리지 않고, 계약에 있는 예시로 먼저 개발합니다.

## 2. 모두가 먼저 읽을 것

| 순서 | 문서 | 내용 |
| --- | --- | --- |
| 1 | [README.md](../README.md) | 서비스, 아키텍처, MVP 범위 |
| 2 | [docs/contracts/query-decomposition.md](contracts/query-decomposition.md) | **분해 결과 형식.** intent, 엔티티, 근거·되묻기 규칙, 선택어, 역할 분담 |
| 3 | [shared/fixtures/decomposition/](../shared/fixtures/decomposition/) | 질문별 분해 결과 예시 11개. **Routing·Generation 개발의 입력 데이터** |
| 4 | [shared/src/negopilot_shared/intents.yaml](../shared/src/negopilot_shared/intents.yaml) | intent → MCP 서버·도구 등록표 |
| 5 | [docs/contracts/routing.md](contracts/routing.md) | **Routing 결과 형식.** status별 Generation 처리, 근거 인용 규칙 |
| 6 | [shared/fixtures/routing/](../shared/fixtures/routing/) | fixtures를 실제 MCP 서버로 Routing한 결과. **Generation 개발의 입력 데이터** |

## 3. 담당별 시작점

### Routing

| 볼 것 | 계약 3절(intent 등록표), 6절(선택어), `intents.yaml`, `DecompositionResult.parallel_groups()` |
| --- | --- |
| 입력 | `DecompositionResult` (fixtures의 `expected`) |
| 출력 | `RouteResult` ([Routing 결과 계약](contracts/routing.md)) |

할 일
1. fixtures 11개를 입력으로 받아, `intents.yaml`에서 서버·도구를 찾아 호출하는 `route` 노드
2. `parallel_groups()` 순서로 실행: 같은 묶음은 병렬, 다음 묶음은 앞 묶음이 끝난 뒤
3. **결과 대응표**: intent별로 앞 결과에서 값을 꺼내는 코드 (예: `rank_suppliers` → `ranked[*].supplier_id`, `list_rfqs` → `[*].rfq_id`). `inputs.select`(`top1`, `top(n)`, `all`, `by_name("이름")`)를 이 표로 해석
4. `status: planned` intent(`draft_email`)와 도구 없는 intent(`general_knowledge`, `out_of_scope`)는 도구를 부르지 않고 Generation으로 넘기기
5. 꺼낼 값이 없으면(앞 결과 0건) 멈추고 Generation에 알리기

### Generation

| 볼 것 | 계약 5절(근거와 되묻기 규칙), 8절(역할), [Routing 결과 계약](contracts/routing.md) 3·4절 |
| --- | --- |
| 입력 | `DecompositionResult` + `RouteResult` (`shared/fixtures/routing`) |
| 출력 | 사용자에게 보여 줄 답변 |

할 일 (fixtures에서 각 경우가 하나씩 있음)

| 경우 | fixture | 답변 |
| --- | --- | --- |
| 도구 결과로 답함 | 01~05, 07 | 도구 결과의 근거 인용 (law-mcp의 `citation` 그대로) |
| `general_knowledge` | 06 | 도구 없이 답하고 **"근거 자료 없음"** 표시. 조문 번호·숫자·회사 정보가 들어가면 지우거나 "확인 필요" |
| `assumptions` 있음 | 08 | 가정을 답변에 밝힘 ("마감이 가장 가까운 RFQ 기준으로 답했습니다") |
| `status: clarify` | 09 | `clarification.question`과 `options`를 선택지로 보여 줌 |
| `out_of_scope` | 10 | 정중히 거절하고 할 수 있는 일 안내 |
| `status: failed` | 11 | 질문을 다시 해 달라고 안내 |

### MCP 서버 (business-mcp, law-mcp, mail-mcp)

| 볼 것 | 계약 4절(엔티티 사전), `intents.yaml` |
| --- | --- |

할 일
1. 도구 인자 이름이 엔티티 사전과 같은지 확인 (예: `supplier`, `rfq_id`, `unit_price`)
2. 도구를 추가·변경하면 **같은 PR에서** `intents.yaml`, 계약 문서, fixtures를 함께 고치고 관련자 승인
3. mail-mcp가 생기면 `draft_email`의 `status`를 `active`로, `tools`를 채우기

### 쿼리 분해 (agent)

| 볼 것 | [agent/README.md](../agent/README.md), [평가 보고서](../agent/eval_reports/) |
| --- | --- |

검증 세트(`shared/fixtures/validation/`, 24개)로 점수를 확인합니다: `uv run python scripts/eval_decomposition.py --set validation --repeat 3`
다음 할 일: Claude 평가, 모델·effort 기본값 결정

## 4. 공용 스키마 연결하기

각 Python 컴포넌트에서 `shared/`를 경로 의존성으로 씁니다.

```bash
cd <내 컴포넌트 폴더>
uv add --editable ../shared          # 폴더 깊이에 맞게 경로 조정 (mcp-servers/xxx 이면 ../../shared)
```

```python
import json
from pathlib import Path

from negopilot_shared import DecompositionResult, load_intents

data = json.loads(Path("shared/fixtures/decomposition/03_rank_then_law.json").read_text())
result = DecompositionResult.model_validate(data["expected"])   # 형식이 틀리면 ValidationError

intents = load_intents()
for group in result.parallel_groups():          # [["q1", "q2"]] → q1, q2 병렬
    for qid in group:
        sq = next(s for s in result.sub_queries if s.id == qid)
        spec = intents[sq.intent]                # spec.server, spec.tools, spec.grounding
        ...
```

**macOS 주의:** `uv` 편집 설치의 `.pth` 파일에 숨김 속성이 붙어 `ModuleNotFoundError`가 나는 경우가 있습니다.
테스트는 `pyproject.toml`의 `[tool.pytest.ini_options] pythonpath`로 경로를 지정하면 피할 수 있습니다 (`agent/`, `shared/` 참고).

## 5. 로컬에서 돌려 보기

```bash
cd shared && uv sync && uv run python -m pytest -q            # 계약 테스트
cd agent  && uv sync && uv run python -m pytest -q            # 분해 단위 테스트 (비용 없음)
cd agent  && uv run python scripts/eval_decomposition.py --backend ollama   # 로컬 Llama로 분해 평가
```

MCP 서버 실행은 [README 빠른 시작](../README.md#빠른-시작)을 보세요.

## 6. 계약을 바꾸고 싶을 때

1. 이슈를 만들고, 바꿀 내용과 이유를 적습니다.
2. `shared/`(스키마, 등록표, fixtures)와 `docs/contracts/`를 **한 PR에서** 고칩니다.
3. `shared/` 테스트가 통과해야 합니다.
4. 분해·Routing·Generation 담당 **전원 승인** 후 머지합니다.
