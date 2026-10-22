# QueryDecomposer 교체 연결 실험

이 코드는 `qa/decomposer-swap` 브랜치의 독립적인 QA 도구다. 제품 반영과 실제 모델의 분류 성능 평가는 이번 작업 범위가 아니다. main 대상 PR을 만들거나 main에 병합하지 않는다.

## 작업 위치와 격리

- worktree: `/Users/rising_star/Downloads/skala_study/day79/NegoPilot-qa-decomposer`
- 시작 지점: `ddd2940`의 main 스냅샷
- 실행 진입점: `agent/scripts/qa_decomposer.py`
- 결과 위치: `agent/qa/decomposer/results/`

기존 작업 디렉터리와 `.env` 및 실행 중인 Docker를 사용하지 않는다. worktree 안에 별도 Python 환경을 만든다. QA 도구는 Spring이나 회원 DB에 요청하지 않는다. 공개 API와 회원 기능 및 대화 이력 저장 코드와 프런트 및 DB 스키마는 변경하지 않는다. 기존 Agent의 live 설정도 변경하지 않는다.

## 기존 계약을 재사용하는 범위

`shared/src/negopilot_shared/decomposition.py`의 `DecompositionResult`는 등록된 intent와 인자 및 앞선 단계 참조를 검증한다. `agent/src/negopilot_agent/routing/router.py`의 `Router`는 이 결과를 받아 MCP 호출을 수행한다.

현재 `general_knowledge`는 생성 모델이 설명하는 경로다. 할인 금액을 코드로 계산하는 실행 단계는 아니다. 이 intent를 내부 계산으로 이름만 바꾸지 않는다.

QA 분해기는 다음 두 출력 중 하나를 반환한다.

1. 기존 `DecompositionResult` 객체 또는 그 JSON. 기존 Router에 전달한다.
2. `qa-decomposition.v1` 계획. 기존 분해 결과를 담은 `routing` 단계와 QA 내부 함수의 `internal` 단계를 연결한다.

QA 계획은 공개 API의 새 버전이 아니다. QA 실행기만 읽는다. production graph와 HTTP 응답 계약은 이 패키지를 사용하지 않는다. 내부 계산을 제품 채팅에 연결하는 작업은 별도 검토가 필요하다.

## 모듈 선택

`agent/src/negopilot_agent/qa_decomposition/loader.py`가 분해 모듈을 하나만 선택한다. 기존 분해기 앞에 다른 분류 모델을 호출하지 않는다.

| 설정 | 값 | 의미 |
| --- | --- | --- |
| `NEGOPILOT_QA_DECOMPOSER` | `existing` | 기존 `QueryDecomposer`를 선택한다. 기본값이다. |
| `NEGOPILOT_QA_DECOMPOSER` | `qa` | 지정한 교체 모듈을 선택한다. |
| `NEGOPILOT_QA_DECOMPOSER_FACTORY` | `모듈경로:factory` | 교체 객체를 만드는 동기 함수다. |

CLI의 `--module`과 `--factory`는 해당 설정을 명시적으로 선택하는 방법이다. 존재하지 않는 설정이나 잘못된 모듈은 오류로 종료한다. 기존 분해기로 자동 대체하지 않는다.

현재 준비한 임시 factory는 `negopilot_agent.qa_decomposition.fixtures:create_decomposer`다. 정해진 질문에 정해진 계획을 돌려주는 테스트 대역이다. 자연어를 이해하거나 분류하는 모델이 아니다.

기존 분해기 또는 새 외부 모듈을 실행하려면 `--allow-model-calls`를 명시해야 한다. 이 옵션이 없으면 임시 factory 이외의 실행을 거절한다. 이번 검증에서는 이 옵션을 사용하지 않는다. 이 제한은 우발적인 모델 호출을 막기 위한 장치이며 임의 플러그인을 격리하는 보안 환경은 아니다. factory는 모델 출력이 아닌 개발자가 지정한 코드 경로다.

## 교체 모듈을 연결할 파일

새 파일의 예는 `agent/src/negopilot_agent/qa_decomposition/candidate.py`다. 다음 인터페이스를 구현한다.

```python
class CandidateDecomposer:
    async def decompose(self, query, history=None, *, context=None):
        # query: 현재 질문 원문
        # history: [{"role": "user" 또는 "assistant", "content": "..."}]
        # context: {"selected_supplier_ids": [...]} 또는 None
        # 기존 DecompositionResult 또는 QAPlan JSON을 반환한다.
        raise NotImplementedError("QA 분해 모듈 연결 지점")


def create_decomposer():
    return CandidateDecomposer()
```

연결할 때 `NEGOPILOT_QA_DECOMPOSER=qa`와 `NEGOPILOT_QA_DECOMPOSER_FACTORY=negopilot_agent.qa_decomposition.candidate:create_decomposer`를 지정한다. 기존 분해기와 비교할 때는 factory 설정을 해제하고 `NEGOPILOT_QA_DECOMPOSER=existing`을 선택한다. 이 변수들은 QA 도구에만 적용된다. `AGENT_EXECUTION_MODE`나 기존 모델 공급자 설정을 바꾸지 않는다.

## 내부 계산과 MCP 연결

내부 함수는 `executor.py`의 명시적인 허용 목록으로 제한한다. 현재 최소 함수는 `apply_discount`다. Decimal로 계산하며 불리언과 비유한 수 및 음수 금액과 범위를 벗어난 할인율을 거절한다. 허용 금액은 0부터 1e18까지이며 할인율은 0부터 100까지다. 입력은 유효숫자 28자리와 소수 8자리 이내로 제한한다. 결과는 원 단위 반올림 없이 십진 문자열로 남긴다. 임의 Python 표현식이나 함수 이름을 실행하지 않는다. `check_offer`는 기존 MCP 소관이며 Agent에 복제하지 않는다.

```json
{
  "schema_version": "qa-decomposition.v1",
  "status": "ok",
  "original_query": "12000원에서 5% 할인하면 얼마야?",
  "steps": [
    {
      "id": "discount",
      "kind": "internal",
      "operation": "apply_discount",
      "arguments": {"amount": "12000", "discount_pct": "5"}
    }
  ]
}
```

MCP 단계에는 기존 `DecompositionResult`를 그대로 넣는다. 기업명 조회는 기존 `supplier_detail` intent를 사용한다. Router가 `find_suppliers`로 이름을 확인한 뒤 유일한 후보의 `get_supplier`를 호출한다.

조회 후 계산에서는 `amount`를 다음 참조로 연결한다. `lookup`은 앞선 routing 단계 ID다. `q1`은 그 단계의 기존 하위 질문 ID다.

```json
{
  "from_step": "lookup",
  "from_sub_query": "q1",
  "selector": "latest_quote_unit_price"
}
```

원본 MCP 견적 필드는 `recent_quotes[].unit_price`와 `quoted_at`이다. 별도 공개 조회 API의 `unit_price_krw`와 혼동하지 않는다. 최신 견적은 반환된 최근 견적 중 날짜가 가장 늦은 항목으로 고른다. 견적이 없거나 응답 형식이 틀리거나 최신 날짜의 견적을 하나로 고를 수 없으면 계산을 중단한다. 반환 목록 밖의 견적을 조회했다는 의미는 아니다.

계획 전체를 검증한 뒤 실행한다. 알 수 없는 함수와 잘못된 참조 및 미래 참조와 순환 의존성은 호출 전에 차단한다. 필수 입력이 부족하면 추가 질문이나 확인 가능한 오류로 남긴다. 앞 단계가 실패하면 연결된 계산을 실행하지 않는다.

## 비교 기록

각 사례의 기대값은 `agent/qa/decomposer/cases.json`에 둔다. 실행 결과에는 입력 원문과 이전 대화 및 선택 문맥을 보존한다. 모듈의 원출력과 정규화된 계획도 구분해서 남긴다. NaN처럼 표준 JSON에 담을 수 없는 숫자는 원출력을 문자열로 기록하고 실행을 막는다. JSON으로 표현할 수 없는 Python 객체는 타입과 실패 사유를 기록한다.

- 기대 분류와 실제 실행 분류
- 예상 상태와 실제 상태
- MCP와 내부 함수의 실제 호출 대상 및 인자와 순서
- 각 호출의 결과와 오류
- 기대 결과의 일치 여부와 불일치 이유
- 사용한 분해 모듈과 테스트 대역 여부

경로 분류는 실제 업무 계산과 MCP 호출을 구분한다. `internal`은 내부 계산만 실행한 경우다. `mcp`는 MCP만 호출한 경우다. `mcp_then_internal`은 조회 후 계산한 경우다. 호출 전 보류되거나 검증에 실패하면 실제 경로는 `none`이다.

값 연결용 `latest_quote_unit_price`는 `binding` 이벤트로 남긴다. 이를 별도의 업무 계산으로 집계하지 않는다. 비교기가 기대값을 확인하려고 값을 읽는 작업도 실제 실행 이벤트에 섞지 않는다. 기존 분해 결과가 `general_knowledge`라 내부 함수를 실행하지 않았다면 그대로 기록한다. 기대 결과에 맞추려고 계산 경로를 만들어 붙이지 않는다.

## 테스트 대역으로 실행

worktree 루트에서 실행한다. 출력은 이 worktree의 QA 결과 디렉터리에만 저장한다.

```bash
cd agent
uv sync --locked
uv run --locked python scripts/qa_decomposer.py \
  --module qa \
  --factory negopilot_agent.qa_decomposition.fixtures:create_decomposer \
  --io test-double \
  --cases qa/decomposer/cases.json
uv run --locked python -m pytest -q tests/test_qa_decomposition_*.py
```

결과 파일은 실행 시각과 임의 식별자를 넣어 새로 만든다. `--output qa/decomposer/results/새이름.json`으로 지정할 수도 있다. 기존 결과 파일을 덮어쓰지 않는다. 실행기는 전용 브랜치와 별도 worktree를 확인하며 결과가 이 worktree 밖에 저장되면 실행을 거절한다.

테스트 MCP는 가상 A기업의 고정된 조회 응답을 반환한다. 실제 MCP와 모델 및 공유 DB는 호출하지 않는다. QA 실행 중에는 LangSmith 추적도 비활성화한다. 기존 서비스의 환경변수는 변경하지 않는다. 통과 수는 연결 검사 결과이며 실제 모델의 분류 정확도가 아니다.

## 나중에 실제 모듈을 연결할 때

교체 파일을 구현한 뒤 동일한 사례 파일로 각각 실행한다. 다음 명령은 연결 방법을 보여주는 예다. 이번 작업에서는 실행하지 않는다. 기존 분해기의 모델 공급자와 키 설정은 별도로 준비해야 한다. QA 모듈의 모델 설정은 해당 구현에서 정한다.

```bash
# 기존 모듈. QA factory 환경변수가 있으면 먼저 해제한다.
unset NEGOPILOT_QA_DECOMPOSER_FACTORY
uv run --locked python scripts/qa_decomposer.py \
  --module existing --allow-model-calls --io test-double

# candidate.py에 연결한 교체 모듈
uv run --locked python scripts/qa_decomposer.py \
  --module qa \
  --factory negopilot_agent.qa_decomposition.candidate:create_decomposer \
  --allow-model-calls --io test-double
```

현재 도구의 MCP 입출력은 `test-double`만 지원한다. 나중에 실제 분해 모델을 연결해도 MCP 부분은 같은 대역을 사용하므로 분해 모듈 간 경로를 비교하기 쉽다. 실데이터 연동은 별도 작업이다. 결과 보고서의 모델 성능 미평가 표시는 이번 연결 검사 도구의 범위를 뜻한다. 향후 정식 평가에서는 사례집과 채점 기준을 따로 정해야 한다.

## 이번 작업에서 검증하지 않는 범위

실제 QA 모델의 자연어 분류 품질과 모델 간 정확도 차이 및 운영 데이터의 대표성은 검증하지 않는다. 실제 MCP의 최신 데이터와 견적 선택의 업무 적합성 및 제품 채팅의 응답 품질도 별도 검증 대상이다. 공개 API에 QA 계획을 추가하거나 회원 기능과 대화 저장을 변경하는 작업은 하지 않는다.

## 이번 연결 구조의 검증 결과

2026년 10월 22일에 이 worktree의 별도 Python 환경에서 실행했다.

| 검증 | 결과 |
| --- | --- |
| 신규 QA 모듈 선택과 실행기 및 기록 도구 테스트 | 96개 통과 |
| 기존 Agent 회귀 테스트 | 982개 통과 |
| 기존 shared 계약 테스트 | 147개 통과 |
| 임시 분해기와 MCP 대역의 사례 비교 | 8개 모두 기대값과 일치 |

대표 실행 기록은 `agent/qa/decomposer/results/wiring.json`이다. 내부 계산과 MCP 조회 및 조회 후 계산을 확인했다. 누락 입력과 잘못된 실행 대상 및 후행 단계 오류와 이전 대화 전달 및 모호한 최신 견적도 확인했다. 실패해야 하는 사례는 실제 실패 여부와 호출 차단 여부를 비교했다. 8개 일치는 정해진 대역의 연결 검사 결과이며 모델 정확도가 아니다.

실행한 테스트 명령은 다음과 같다.

```bash
# worktree의 agent 디렉터리
.venv/bin/python -m pytest -q tests/test_qa_decomposition_loader.py tests/test_qa_decomposition_executor.py tests/test_qa_decomposition_runner.py
.venv/bin/python -m pytest -q tests --ignore=tests/test_qa_decomposition_executor.py --ignore=tests/test_qa_decomposition_loader.py --ignore=tests/test_qa_decomposition_runner.py

# worktree의 shared 디렉터리
../agent/.venv/bin/python -m pytest -q
```

Spring과 프런트 및 DB 통합 테스트와 Docker 재빌드는 수행하지 않았다. 해당 코드는 변경하지 않았다. 실제 모델과 실제 MCP를 연결한 종단 간 실행도 이번 검증에 포함하지 않았다.
