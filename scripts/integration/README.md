# Spring과 Python HTTP 통합 검증

이 디렉터리의 smoke 실행기는 Spring과 Python을 임시 포트에서 함께 띄워 공개 API와 내부 API 경로를 검사하는 개발용 회귀 검사입니다. 사용자용 서버를 실행하는 용도로 사용하지 않습니다.

Spring은 운영과 같은 기본 프로필로 실행합니다. 실행마다 일회용 PostgreSQL 컨테이너를 만들고, JAR의 bootstrap CLI로 관리자와 승인된 회원을 만든 뒤, 공개 API로 로그인해 세션과 CSRF 토큰을 붙여 호출합니다. 셸의 운영 프로필과 DB 설정은 상속하지 않습니다. 대화 저장 검사는 [이력 v0.4 계약](../../docs/contracts/history.md)을 따릅니다.

컨테이너 재생성 후 기록 보존과 실제 MCP 공급사 조회 등 Compose 전체 흐름은 `compose_smoke.py`가 확인합니다. 실행 방법은 [로컬 Docker 안내](../../docs/LOCAL_DOCKER.md)를 따릅니다.

Generation 경로의 계약 회귀는 `generation_smoke.py`로 검사합니다. [Generation HTTP 계약](../../docs/contracts/agent-generation.md)의 공개 경로(`/api/...`)와 내부 경로(`/internal/...`)를 연결합니다. 실제 `build_graph`와 `QueryDecomposer` 및 `AnswerGenerator`를 실행하며 모델 backend와 도구 입출력에 fake를 주입합니다.

`direct_chat_smoke.py`는 같은 경로에서 기업을 미리 선택하지 않은 직접 질문과 추가 답변을 검사합니다. 문서화된 질문을 내장 stub에 보내 검색과 이름 조회 및 법령 조회와 원래 질문의 재개를 확인합니다. HTTP 스키마는 0.3을 유지합니다.

이 검사들은 Python live 환경이나 모델 인증 키 및 MCP 서버를 준비할 필요가 없습니다. 실제 모델과 MCP를 호출하지 않으며 메일도 보내지 않습니다. DB는 실행이 직접 만든 일회용 컨테이너만 사용합니다.

## 새 체크아웃에서 준비

저장소 루트에서 아래 명령을 순서대로 실행합니다. macOS 또는 Linux 환경에서 Python 3.12 이상과 uv, Java 21, Maven 3.6.3 이상, 그리고 실행 중인 Docker가 필요합니다. 각 명령이 성공한 뒤 다음 단계로 진행합니다.

```bash
python3 --version
uv --version
java -version
mvn -version
docker info

env -u VIRTUAL_ENV UV_PROJECT_ENVIRONMENT=.venv \
  uv sync --directory agent --locked --group dev
mvn -f backend/pom.xml -B -ntp package
```

위 `uv sync` 명령은 활성 가상환경을 상속하지 않고 현재 체크아웃의 `agent/.venv`를 준비합니다. `--locked`로 저장된 의존성 잠금 파일을 유지합니다. 개발 의존성의 `jsonschema`는 HTTP 응답을 공통 OpenAPI 명세와 비교할 때 사용합니다. Maven은 backend 테스트를 실행하고 `backend/target/negopilot-backend-0.1.0-SNAPSHOT.jar`를 만듭니다. 최초 준비에는 의존성을 내려받을 네트워크 연결이 필요합니다.

Python과 Spring의 상세 설정은 [agent 안내](../../agent/README.md)와 [backend 안내](../../backend/README.md)를 참고합니다. 이 통합 검사에는 `.env` 작성이나 live 모델 설정이 필요하지 않습니다. 서버와 DB도 별도로 시작할 필요가 없습니다. PostgreSQL 이미지(`postgres:16`)가 없으면 첫 실행 때 내려받습니다.

## 실행

준비가 끝나면 같은 저장소 루트에서 helper 단위 검사와 통합 검사를 실행합니다.

```bash
python3 -B -m unittest discover -s tests/integration -p 'test_*.py' -v
python3 -B scripts/integration/generation_smoke.py \
  --report /tmp/negopilot-generation-results.json
python3 -B scripts/integration/direct_chat_smoke.py \
  --report /tmp/negopilot-direct-chat-results.json
```

helper 단위 검사는 표준 라이브러리 `unittest`로 실행하며 Docker나 서버를 띄우지 않습니다. 통합 실행기는 준비된 `agent/.venv/bin/python`으로 Uvicorn을 직접 시작하며 현재 체크아웃의 `agent/src`와 `shared/src`를 사용합니다. 실행 중에는 의존성을 설치하거나 Spring을 빌드하지 않습니다. 각 실행기는 약 10초가 걸립니다.

다른 Spring 실행 JAR을 선택하려면 `--jar`를 지정합니다.

```bash
python3 -B scripts/integration/generation_smoke.py \
  --jar backend/target/negopilot-backend-0.1.0-SNAPSHOT.jar \
  --report /tmp/negopilot-generation-results.json
```

모든 검사가 통과하면 종료 코드는 0입니다. 검사 실패는 1입니다. JAR이나 해당 OpenAPI 명세 또는 `agent/.venv/bin/python`이 없거나 Docker를 찾을 수 없으면 2입니다. 터미널에는 검사별 통과 여부를 출력합니다. 결과 JSON에는 검사 결과와 실행 시간 및 실행한 JAR의 SHA-256과 검증 한계를 기록합니다. 결과 JSON과 서버 로그 및 과거 실행 기록은 저장소에 추가하지 않습니다.

## Generation 검증

Generation 검사는 실제 FastAPI 서버와 Spring 서버를 임시 포트에서 실행합니다. 내장 stub과 검사 전용 fake backend를 사용하며 최종 Answer 객체는 실제 Generator가 만듭니다. 질의와 메시지의 Generation 응답은 저장된 `agent-generation.openapi.json`과 대조합니다. 이 명세의 대화 스냅샷은 과거 메모리 형식이므로, v0.4 대화 생성과 스냅샷 응답은 실행기가 필드 단위로 검사합니다. 앱이 실행 중 생성한 OpenAPI를 기대값으로 사용하지 않습니다.

답변과 추가 입력 및 거절과 결과 없음과 실패를 구분합니다. 일부 Routing 오류가 있어도 최종 답변이 만들어지면 COMPLETED를 유지하는지 검사합니다. citations와 evidence 및 verification을 보존하며 보정된 답변의 masked=true를 실패로 바꾸지 않는지도 확인합니다.

공개 대화 경로에서는 선택 문맥과 이력 전달을 확인합니다. 문맥 변경과 실패 시 저장 보존을 검사합니다. 응답의 decomposition.original_query가 전달한 질문과 다르면 거절해야 합니다. 긴 assistant 답변은 원문으로 저장하고 다음 요청 이력에서만 4000 code point로 제한해야 합니다. 앞부분을 자른 결과가 공백만이면 전송용 이력의 앞 공백을 제외하고 다시 제한합니다. 모델 이력은 최신 20개입니다. 저장 메시지는 삭제하지 않으며, 스냅샷은 최신 50개와 이전 페이지 커서를 반환합니다. 104개 메시지를 저장한 뒤 이전 페이지 조회로 나머지가 모두 남아 있는지 확인합니다.

같은 대화에 생성이 진행 중이면 다음 요청은 `409 CONVERSATION_BUSY`로 거절되고 Python에 전달되지 않아야 합니다. 다른 대화는 그동안에도 처리됩니다. 같은 `Idempotency-Key`의 재시도는 저장된 응답을 그대로 돌려주며 Python을 다시 호출하지 않습니다. 같은 키를 다른 질문에 쓰면 `409 IDEMPOTENCY_CONFLICT`입니다. 로그인하지 않은 요청은 401, CSRF 토큰이 없는 변경 요청은 403이며 둘 다 Python에 전달되지 않아야 합니다.

검사 중 주입한 응답 손상과 HTTP 오류는 저장 경계 검사용입니다. 이 결과를 실제 모델 실패나 실제 도구 응답으로 표시하지 않습니다. 임시 내부 키는 출력하지 않습니다. 결과 본문과 로그를 저장할 때는 `/tmp` 경로를 사용합니다.

실제 모델 검증은 위 fake 검사와 별도로 수행합니다. 일반 서비스 실행에는 회원 DB와 승인된 계정 및 세션과 CSRF가 필요합니다. OpenAI 모델을 사용할 때는 분해와 생성의 backend를 모두 `openai`로 지정하고 Python 서버 환경에 `OPENAI_API_KEY`를 설정합니다. 모델을 지정하지 않으면 `gpt-4.1-mini`를 씁니다. `NEGOPILOT_DECOMPOSER_BACKEND`와 `NEGOPILOT_GENERATOR_BACKEND`도 모두 설정합니다. Python의 `AGENT_REQUEST_TIMEOUT_SECONDS`는 180초 등 모델 지연을 수용할 값으로 두고 Spring의 `AGENT_RESPONSE_TIMEOUT_SECONDS`는 Python 응답 정리 시간을 포함하도록 설정합니다. Ollama 설정은 [Agent 안내](../../agent/README.md#ollama-native-공급자)를 참고합니다.

실제 모델 확인은 일반 지식 질문 한 건부터 순차 실행합니다. HTTP 성공 여부와 실제 분해 모델 호출 및 생성 모델 호출을 각각 기록합니다. 답변 대신 추가 입력이나 실패가 나오면 실제 생성 성공으로 세지 않습니다. MCP가 준비되지 않은 환경의 일반 지식 검증은 실제 기업 정보나 법령 근거 조회의 성공을 의미하지 않습니다.

## 직접 질문 검증

직접 질문 검사는 선택 기업이 없는 상태에서 협력사 목록과 지역 및 공정 검색을 요청합니다. 공정을 말하지 않은 목록 및 지역 검색도 도구로 전달돼야 합니다. 법령 질문과 일반적인 지급 조건 질문은 조문 근거를 조회해야 합니다.

기업 이름 조회는 `find_suppliers`의 후보 수에 따라 분기합니다. 한 곳이면 `get_supplier`로 상세 정보를 조회합니다. 여러 곳이면 `NEEDS_INPUT`과 후보 선택지를 반환합니다. 검색 결과가 없거나 도구가 실패했을 때는 기업 선택을 요구하지 않아야 합니다. 두 경우는 Routing의 `empty`와 `error`로 구분합니다.

대화 검사에서는 기업 정보가 부족한 질문 뒤 짧은 기업 ID나 후보 표시 문자열을 보내 원래 질문이 재개되는지 확인합니다. HTTP 응답의 `decomposition.original_query`에는 현재 요청을 보존하며 재개한 질문은 하위 질문에서 확인합니다. 추가 답변 대신 새 주제의 질문을 보내면 새 요청을 처리해야 합니다.

후보가 여러 개이면 Spring은 실제 `Answer.options`를 번호가 있는 JSON 블록으로 Python 전송용 이력에 붙입니다. `2번`은 이 블록의 두 번째 후보로 이어져야 합니다. 저장된 답변 원문과 옵션 순서를 유지하며 전송 이력은 4000 code point 이내여야 합니다. 번호 블록이 없는 이력에서는 임의로 후보를 고르면 안 됩니다. 내장 stub의 문서화된 범위를 벗어난 번호 입력은 `VALIDATION_ERROR`로 확인합니다.

이 실행은 실제 graph와 Generator를 사용합니다. 모델과 도구 결과는 fixture입니다. 도구 장애 시나리오에서만 별도 fake caller가 오류를 반환합니다. 결과와 응답 및 로그는 `/tmp`에 저장하며 실제 MCP와 모델을 호출하지 않습니다.

직접 질문의 기업 fixture는 `docs/contracts/fixtures/supplier-chat.json`과 추가 `docs/contracts/fixtures/direct-chat.json`을 사용합니다. stub 질문에 대한 성공을 임의의 자연어 질문이나 실제 회사 데이터의 검증으로 해석하지 않습니다.

## 검증 범위

| 범위 | 확인 내용 |
| --- | --- |
| Generation 실행 | 답변 종류별 상태와 일부 Routing 실패, 근거·인용·검증 결과 보존 |
| 선택 기업 대화 | v0.4 대화 생성과 문맥 유지 및 기업 변경, 질문과 답변의 함께 저장 |
| 저장 경계 | 실패 응답과 잘못된 upstream 응답 및 연결 실패 때 메시지와 문맥 유지 |
| 이력 | Python에 최신 20개 전달, 전체 메시지 보존과 스냅샷 50개 및 이전 페이지 |
| 동시 요청 | 같은 대화의 `409 CONVERSATION_BUSY`와 다른 대화의 독립 처리 |
| 재시도 | 같은 `Idempotency-Key`의 저장 응답 재사용과 다른 본문 충돌 |
| 회원 인증 | 로그인하지 않은 요청과 CSRF 토큰이 없는 변경 요청의 거절 |
| 입력과 내부 키 | 잘못된 입력과 없는 ID 및 Python 내부 키 검증 |
| 응답 명세 | 수집한 Generation 응답을 고정 OpenAPI 명세로 검사 |
| 정보 노출 | 응답과 자식 프로세스 로그에 임시 내부 키와 회원 비밀번호가 없는지 확인 |
| 직접 질문 | 기업 미선택 검색과 이름 후보 분기 및 법령 근거와 추가 답변 재개 및 주제 변경 |

Spring과 Python 사이의 로컬 HTTP 프록시는 정상 요청을 전달하며 이력과 ID 및 인증 헤더 전달 여부를 메모리에서 확인합니다. 장애 검사는 응답이나 인증 헤더를 의도적으로 바꿔 Spring의 처리와 저장 경계를 확인합니다. 프록시는 내부 키 값을 기록하지 않습니다.

## 실행 자원과 정리

포트는 운영체제가 할당한 사용 가능한 로컬 포트에서 선택합니다. 서버 기동 직전 포트를 반환하므로 짧은 포트 경쟁 가능성이 있습니다. 실행기는 자신이 시작한 서버의 기동 로그와 health 응답을 함께 확인합니다. 충돌한 기존 서버를 종료하지 않습니다.

내부 키와 회원 비밀번호는 실행할 때 생성해 자식 프로세스 환경으로만 전달합니다. 자식 환경은 실행에 필요한 일부 변수만 상속하며 셸의 모델 인증 정보와 앱 설정은 제외합니다. stub 모드를 명시하고 모델과 MCP 주소를 로컬 검사 프록시로 지정해 호출 여부를 확인합니다. 요청과 응답 본문 및 서버 로그는 검사 중 메모리에서 수집합니다. Generation과 직접 질문 실행기의 증거 파일은 `/tmp`에 기록하며 내부 키와 비밀번호 값은 기록하지 않습니다.

Generation과 직접 질문 실행기는 준비된 JAR을 임시 디렉터리에 복사한 뒤 그 사본으로 검사를 진행합니다. JAR 빌드를 끝낸 후 실행하며 검사가 끝날 때까지 원본 JAR을 다시 빌드하지 않습니다. 종료할 때 이 실행이 만든 프로세스 그룹과 프록시, `negopilot-smoke-` 접두사의 PostgreSQL 컨테이너 및 임시 JAR 사본만 정리합니다. 한 자원의 정리가 실패해도 나머지 자원의 정리를 계속하고 실패를 결과에 남깁니다. 프로세스 그룹의 최초 프로세스가 먼저 끝나도 같은 그룹의 자식 프로세스를 정리합니다. 포트 번호로 기존 프로세스를 찾아 종료하지 않습니다.

## 결과 해석의 한계

직접 질문 검사의 기업 정보는 fixture의 가상 데이터입니다. 실제 기업 정보나 MCP 실행 결과가 아닙니다.

stub 검사는 문서화된 질문과 fixture의 동작을 확인합니다. 직접 질문 검사는 실제 core의 이력 처리와 재개된 하위 질문을 확인합니다. 이 결과가 live 모델의 임의 질문 처리 능력이나 이력 이해를 입증하지는 않습니다.

fake backend를 사용한 Generation 검사는 live 모델의 품질을 검증한 결과로 해석하지 않습니다. 일회용 DB를 쓰는 저장 검사는 서버 재시작 후 보존을 확인하지 않습니다. 재시작 후 보존은 `compose_smoke.py`가 확인합니다.
