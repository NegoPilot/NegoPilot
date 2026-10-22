# 개발 시작 가이드

NegoPilot의 현재 실행 경로와 담당별 계약 및 개발 시작 순서를 정리합니다.
협업 규칙(이슈 → 브랜치 → PR)은 [CONTRIBUTING.md](../CONTRIBUTING.md)를 따릅니다.

## 1. 현재 처리 흐름

```
Vue 가입과 로그인 및 /chat
  → Spring: 회원 승인 상태와 세션 및 CSRF 확인
  → Python Agent: decompose_query
  → route: business-mcp와 law-mcp에서 필요한 자료 조회
  → generate: 조회 결과로 답변 생성
  → Spring: 질문과 답변 및 근거를 개인 대화 DB에 저장
  → Vue: 답변과 개인 대화 기록 및 근거 표시
```

기업 선택은 필수가 아닙니다. 채팅의 실제 모델 실행에는 `live`를 사용합니다. 채팅의 `stub`은 정해진 질문의 테스트 응답을 반환하며 실제 모델이나 업무 MCP를 실행한 결과가 아닙니다. 두 모드 모두 기본 Spring 실행에는 회원과 세션 및 대화 저장용 PostgreSQL이 필요합니다.

가입과 로그인 및 개인 대화 화면은 서버에 연결돼 있습니다. 관리자 화면과 별도 협력사 검색 화면은 연결 준비 안내를 표시합니다. 관리자 백엔드 API는 구현돼 있으므로 현재 가입 승인은 관리자 세션으로 API를 호출해야 합니다. RFQ 개요와 법규 검토 화면의 예시는 실제 조회 결과와 구분합니다.

별도 [협력사 조회 API](contracts/supplier-lookup.md)는 `/api/business/catalog`과 `/api/suppliers` 및 `/api/suppliers/{id}`로 제공합니다. 이 API는 채팅 모드와 관계없이 Python을 거쳐 business MCP를 조회합니다. 모델은 호출하지 않습니다. 채팅이 stub이어도 협력사 조회에는 business MCP와 DB가 필요합니다. `/suppliers` 화면이 이 API로 검색과 상세를 조회합니다.

Agent 그래프는 `agent/src/negopilot_agent/graph.py`에 있습니다. CLI 진입점 `agent/scripts/ask.py`는 회원 인증이나 개인 대화 저장을 수행하지 않습니다. 브라우저 대화는 Spring의 `/api/conversations` 경로를 사용합니다.

## 2. 모두가 먼저 읽을 것

| 순서 | 문서 | 내용 |
| --- | --- | --- |
| 1 | [README.md](../README.md) | 서비스 구조와 MVP 범위 |
| 2 | [docs/contracts/query-decomposition.md](contracts/query-decomposition.md) | 분해 형식과 intent 및 엔티티와 근거 및 되묻기 규칙 |
| 3 | [shared/fixtures/decomposition/](../shared/fixtures/decomposition/) | 질문별 분해 결과 예시 11개. Routing과 Generation의 입력 데이터 |
| 4 | [shared/src/negopilot_shared/intents.yaml](../shared/src/negopilot_shared/intents.yaml) | intent에 대응하는 MCP 서버와 도구 등록표 |
| 5 | [docs/contracts/routing.md](contracts/routing.md) | Routing 결과 형식과 status별 처리 및 근거 인용 규칙 |
| 6 | [shared/fixtures/routing/](../shared/fixtures/routing/) | fixtures를 실제 MCP 서버로 Routing한 결과. **Generation 개발의 입력 데이터** |
| 7 | [backend/README.md](../backend/README.md) | 애플리케이션 DB와 business DB 분리 및 서버 설정 |
| 8 | [회원 계약](contracts/membership.md) | 가입과 세션 및 최초 관리자 생성과 승인 |
| 9 | [개인 대화 계약](contracts/history.md) | 계정별 저장과 페이지 조회 및 제목 변경과 삭제 |
| 10 | [협력사 관리 계약](contracts/admin-suppliers.md)과 [법령 관리 계약](contracts/admin-laws.md) | 관리자 API와 DB migration 및 법령 최신화 |
| 11 | [협력사 조회 계약](contracts/supplier-lookup.md) | 모델 호출 없는 MCP 조회와 검색 조건 및 출처 |

## 3. 담당별 시작점

### Routing

| 볼 것 | 계약 3절과 6절 및 `intents.yaml`과 `DecompositionResult.parallel_groups()` |
| --- | --- |
| 입력 | `DecompositionResult` (fixtures의 `expected`) |
| 출력 | `RouteResult` ([Routing 결과 계약](contracts/routing.md)) |

변경 시 점검할 것

1. fixtures 11개를 입력으로 받아 `intents.yaml`에서 서버와 도구를 찾아 호출하는 `route` 노드
2. `parallel_groups()` 순서로 실행하기. 같은 묶음은 병렬로 처리하고 다음 묶음은 앞 묶음이 끝난 뒤 실행
3. intent별 앞 결과와 선택어의 대응 확인. `rank_suppliers`의 기업 ID와 `list_rfqs`의 RFQ ID를 추출하고 `inputs.select`를 해석
4. `status: planned`인 `draft_email`과 도구 없는 `general_knowledge` 및 `out_of_scope`를 Generation으로 넘기기
5. 꺼낼 값이 없으면(앞 결과 0건) 멈추고 Generation에 알리기

### Generation

| 볼 것 | 계약 5절과 8절 및 [Routing 결과 계약](contracts/routing.md) 3절과 4절 |
| --- | --- |
| 입력 | `DecompositionResult` + `RouteResult` (`shared/fixtures/routing`) |
| 출력 | 사용자에게 보여 줄 답변 |

변경 시 점검할 경우입니다. fixtures에 각 경우의 예시가 있습니다.

| 경우 | fixture | 답변 |
| --- | --- | --- |
| 도구 결과로 답함 | 01부터 05까지와 07 | 도구 결과의 근거 인용. law-mcp의 `citation` 유지 |
| `general_knowledge` | 06 | 도구 없이 답하고 **"근거 자료 없음"** 표시. 확인되지 않은 조문 번호와 숫자 및 회사 정보 처리 |
| `assumptions` 있음 | 08 | 가정을 답변에 밝힘 ("마감이 가장 가까운 RFQ 기준으로 답했습니다") |
| `status: clarify` | 09 | `clarification.question`과 `options`를 선택지로 보여 줌 |
| `out_of_scope` | 10 | 정중히 거절하고 할 수 있는 일 안내 |
| `status: failed` | 11 | 질문을 다시 해 달라고 안내 |

### MCP 서버

| 볼 것 | 계약 4절의 엔티티 사전과 `intents.yaml` |
| --- | --- |

할 일

1. `supplier`와 `rfq_id` 및 `unit_price` 등 도구 인자 이름이 엔티티 사전과 같은지 확인
2. 도구를 추가하거나 변경하면 **같은 PR에서** `intents.yaml`과 계약 문서 및 fixtures를 함께 고치고 관련자 승인
3. mail-mcp가 생기면 `draft_email`의 `status`를 `active`로 바꾸고 `tools`를 채우기

### 쿼리 분해 (agent)

| 볼 것 | [agent/README.md](../agent/README.md)와 [평가 보고서](../agent/eval_reports/) |
| --- | --- |

검증 세트는 `shared/fixtures/validation/`의 50개 질문입니다. 평가는 agent 폴더에서 `uv run python scripts/eval_decomposition.py --set validation --repeat 3`으로 실행합니다.
모델을 바꿀 때는 기존 평가 보고서와 현재 설정을 먼저 확인합니다. 실제 모델 평가 명령은 네트워크를 사용하며 공급자에 따라 비용이 발생합니다. 모델 호출 없는 단위 테스트와 구분해 실행합니다.

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
테스트는 `pyproject.toml`의 `[tool.pytest.ini_options] pythonpath`로 경로를 지정하면 피할 수 있습니다. `agent/`와 `shared/` 설정을 참고합니다.

## 5. 로컬에서 돌려 보기

Java 21과 Maven 및 Python용 uv와 PostgreSQL을 준비합니다. 프런트에는 Node.js 22.12 이상이 필요합니다. 아래 명령은 저장소 루트에서 시작하며 서버별로 별도 터미널을 사용합니다.

### DB와 최초 관리자

PostgreSQL 관리 권한이 있는 터미널에서 애플리케이션 전용 계정과 새 DB를 준비합니다. 비밀번호는 프롬프트로 입력합니다. 이미 준비된 계정과 DB가 있으면 생성 명령을 반복하지 않습니다.

```bash
createuser --pwprompt negopilot_app
createdb --owner=negopilot_app negopilot_app

export APP_DATABASE_URL=jdbc:postgresql://127.0.0.1:5432/negopilot_app
export APP_DATABASE_USER=negopilot_app
export SESSION_COOKIE_SECURE=false
```

`APP_DATABASE_PASSWORD`는 위 계정의 비밀번호를 비밀 환경설정으로 전달합니다. 저장소나 프런트 설정에 기록하지 않습니다. 같은 설정으로 [최초 관리자 생성 CLI](contracts/membership.md#최초-관리자-생성)를 한 번 실행합니다. CLI와 Spring이 Flyway로 회원과 세션 및 대화 테이블을 준비합니다. 관리자 비밀번호도 콘솔 또는 `ADMIN_BOOTSTRAP_PASSWORD`로 전달합니다.

business DB는 애플리케이션 DB와 분리합니다. [business DB 준비](../mcp-servers/business-mcp/SETUP.md)와 [관리 열 migration](contracts/admin-suppliers.md#db-적용과-실행-순서)을 따릅니다. seed는 기존 자료를 삭제하므로 새 실습 DB에만 사용합니다. 기존 DB에는 migration만 적용합니다. Spring의 관리자 기능은 쓰기 계정을 사용하며 business-mcp는 같은 DB의 SELECT 전용 계정을 사용합니다.

### MCP와 Python Agent

실제 협력사 질문에는 business-mcp가 필요합니다. 법령 질문에는 law-mcp가 필요합니다. 각 서버의 접속 설정을 준비한 뒤 별도 터미널에서 실행합니다.

```bash
# DATABASE_URL에는 준비된 business DB의 조회 전용 접속 정보를 주입
(cd mcp-servers/business-mcp && uv sync && uv run python server.py --http 9102)

# 별도 터미널
(cd mcp-servers/law-mcp && uv sync && uv run python server.py --http 9103)
```

법령 조회는 저장소에 포함된 자료를 읽습니다. 관리자 최신화를 사용하려면 [법령 서버 안내](../mcp-servers/law-mcp/README.md)에 따라 영속적인 `LAW_RUNTIME_DIR`과 별도 `LAW_ADMIN_API_KEY`를 설정합니다. 같은 관리 키를 Python에도 전달합니다. 법령 관리 API는 stub 모드에서도 실제 law-mcp를 호출합니다.

로컬 Ollama 서버와 `qwen3:8b` 모델은 먼저 준비합니다. Agent는 모델을 자동 다운로드하거나 Ollama를 시작하지 않습니다. 자세한 설정은 [Ollama native 공급자](../agent/README.md#ollama-native-공급자)를 따릅니다.

Python을 실행하는 터미널에는 Spring과 동일한 `AGENT_INTERNAL_API_KEY`를 비밀 환경설정으로 주입합니다. 다음 예시는 로컬 모델의 대기 시간을 고려한 제한 값입니다. 모델 성능에 따라 조정합니다.

```bash
export AGENT_EXECUTION_MODE=live
export NEGOPILOT_DECOMPOSER_BACKEND=ollama
export NEGOPILOT_GENERATOR_BACKEND=ollama
export NEGOPILOT_DECOMPOSER_MODEL=qwen3:8b
export NEGOPILOT_GENERATOR_MODEL=qwen3:8b
export OLLAMA_BASE_URL=http://127.0.0.1:11434
export OLLAMA_TIMEOUT_SECONDS=280
export AGENT_REQUEST_TIMEOUT_SECONDS=300
export BUSINESS_MCP_URL=http://127.0.0.1:9102/mcp
export LAW_MCP_URL=http://127.0.0.1:9103/mcp

(cd agent && uv sync && uv run uvicorn negopilot_agent.api.app:app --host 127.0.0.1 --port 9200)
```

모델 없는 연결 점검은 Python과 Spring 모두 `AGENT_EXECUTION_MODE=stub`로 실행합니다. [지원하는 stub 질문](contracts/direct-chat.md)을 사용합니다. 공급자나 연결 오류가 발생한 live 요청은 stub으로 자동 전환하지 않습니다.

### Spring과 프런트

Spring 터미널에 위 애플리케이션 DB 설정과 Python과 같은 내부 키를 전달합니다. 협력사 관리가 필요하면 [business.datasource 설정](../backend/README.md#db-준비)도 추가합니다.

```bash
export AGENT_EXECUTION_MODE=live
export AGENT_BASE_URL=http://127.0.0.1:9200
export AGENT_RESPONSE_TIMEOUT_SECONDS=310
export SESSION_COOKIE_SECURE=false

mvn -f backend/pom.xml -B -ntp -DskipTests package
java -jar backend/target/negopilot-backend-0.1.0-SNAPSHOT.jar
```

프런트는 별도 터미널에서 실행합니다. 내부 키와 DB 비밀번호 및 모델 키를 이 터미널의 프런트 환경변수에 넣지 않습니다.

```bash
cd frontend
npm ci
NEGOPILOT_BACKEND_URL=http://127.0.0.1:8080 npm run dev
```

브라우저에서 `http://127.0.0.1:5173`을 엽니다. Vite가 `/api`와 `/health`를 Spring으로 전달합니다. 프런트의 생성 요청 제한은 기본 330초입니다. 다른 환경에서는 `VITE_API_TIMEOUT_MS`와 각 서버의 제한을 함께 확인합니다. 로컬 HTTP에만 `SESSION_COOKIE_SECURE=false`를 사용하며 운영 HTTPS에서는 기본값 `true`를 유지합니다.

1. `/signup`에서 가입하면 `USER/PENDING`으로 등록됩니다. 로그인해도 업무 화면을 사용할 수 없습니다.
2. 관리자는 [회원 계약](contracts/membership.md#요청-순서)에 따라 로그인 세션과 CSRF 토큰을 준비합니다. `PATCH /api/admin/members/{id}/status`에 `{"status":"ACTIVE"}`를 보내 승인합니다. 현재 관리자 화면에서는 승인할 수 없습니다.
3. 승인된 사용자는 `/chat`에서 기업 선택 없이 질문합니다. 개인 대화 목록에서 이전 기록과 근거를 다시 열 수 있습니다.
4. 응답의 모드와 오류를 확인합니다. `/health`의 성공은 Python과 MCP 및 모델 연결 성공을 뜻하지 않습니다.

### 모델 호출 없는 검사

```bash
(cd shared && uv sync && uv run python -m pytest -q)
(cd agent && uv sync && uv run python -m pytest -q)
mvn -f backend/pom.xml -B -ntp test
(cd frontend && npm ci && npm run build)
```

실제 DB 회귀 테스트에는 폐기 가능한 전용 DB가 필요합니다. 환경변수와 초기화 범위는 [백엔드 검증 안내](../backend/README.md#검증)를 확인합니다.

## 6. 계약을 바꾸고 싶을 때

1. 이슈에 바꿀 내용과 이유를 적습니다.
2. `shared/`의 스키마와 등록표 및 fixtures를 `docs/contracts/`와 **한 PR에서** 고칩니다.
3. `shared/` 테스트가 통과해야 합니다.
4. 분해와 Routing 및 Generation 담당 **전원 승인** 후 머지합니다.
