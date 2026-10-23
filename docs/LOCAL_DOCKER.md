# Docker로 로컬 실행하기

기본 실행은 실제 모델로 질문을 해석하고 MCP 도구를 조회한 뒤 답변을 생성합니다. 모델 키 없이 연결을 검사하려면 아래의 별도 테스트 구성을 사용합니다.

Docker Desktop 또는 Docker Engine과 Compose 플러그인을 준비합니다. Docker가 실행 중이어야 합니다. 테스트 구성의 `!override` 문법에는 Docker Compose 2.24.4 이상이 필요합니다. `docker compose version`으로 버전을 확인합니다. 호스트에 Node.js와 Java 및 Python을 따로 설치하거나 Vite 개발 서버를 실행할 필요는 없습니다.

## 기본 실행: 실제 모델 연결

저장소 루트에서 최초 한 번 예시 설정을 복사합니다. 이미 `.env`가 있다면 덮어쓰지 말고 필요한 항목을 추가합니다.

```bash
cp .env.example .env
```

`.env`의 `OPENAI_API_KEY`에 사용할 API 키를 입력합니다. 기본 공급자는 질문 해석과 답변 생성 모두 `openai`이며 기본 모델은 `gpt-4.1-mini`입니다. 모델을 명시하려면 다음 값을 사용합니다.

```dotenv
NEGOPILOT_DECOMPOSER_BACKEND=openai
NEGOPILOT_DECOMPOSER_MODEL=gpt-4.1-mini
NEGOPILOT_GENERATOR_BACKEND=openai
NEGOPILOT_GENERATOR_MODEL=gpt-4.1-mini
```

API 키를 설정한 뒤 실행합니다.

```bash
docker compose up --build -d
```

처음 실행할 때는 이미지와 의존성을 내려받고 DB를 준비하므로 시간이 걸릴 수 있습니다. 상태와 로그는 다음 명령으로 확인합니다.

```bash
docker compose ps -a
docker compose logs --tail=100
```

준비가 끝나면 [http://localhost:5173](http://localhost:5173)을 엽니다. `admin-bootstrap`과 `user-bootstrap`은 계정 초기화가 끝나면 `Exited (0)`으로 남는 일회성 서비스입니다. 관리자 초기화 성공 후 승인 회원을 준비하며 backend는 두 서비스가 성공한 뒤 시작합니다. 나머지 서비스가 실행되고 `frontend`가 healthy인지 확인합니다. 초기화 로그는 `docker compose logs admin-bootstrap user-bootstrap postgres`로 확인합니다.

Docker의 Agent 시작기는 서버를 열기 전에 선택한 공급자의 모델 설정과 필요한 키를 확인합니다. 누락되거나 잘못된 설정이 있으면 시작을 중단합니다. 이 검사는 모델을 호출하지 않으므로 키의 유효성이나 모델 사용 권한 및 MCP 연결 성공까지 보증하지 않습니다. 실제 질문을 보내면 모델 API 호출 비용이 발생할 수 있습니다. live 실행이 실패해도 stub 답변으로 전환하지 않습니다.

웹 포트만 `127.0.0.1`에 공개합니다. DB와 Spring 및 MCP는 Compose 내부 네트워크를 사용합니다. 모델 키는 Python Agent에 전달하며 프런트 빌드에는 넣지 않습니다. `.env`는 Git에 올리지 않습니다.

## 공급자와 모델 설정

질문 해석과 답변 생성의 공급자를 따로 선택할 수 있습니다. 기본 실행은 두 단계 모두 OpenAI를 사용합니다.

| 변수 | Docker 기본값 | 용도 |
| --- | --- | --- |
| `NEGOPILOT_DECOMPOSER_BACKEND` | `openai` | 질문 해석 공급자. `openai` 또는 `anthropic` 또는 `ollama` |
| `NEGOPILOT_GENERATOR_BACKEND` | `openai` | 답변 생성 공급자. 선택지는 질문 해석과 같음 |
| `NEGOPILOT_DECOMPOSER_MODEL` | 공급자 기본 모델 | 질문 해석 모델 |
| `NEGOPILOT_GENERATOR_MODEL` | 공급자 기본 모델 | 답변 생성 모델 |
| `OPENAI_API_KEY` | 빈 값 | 한 단계라도 OpenAI를 선택하면 필요 |
| `ANTHROPIC_API_KEY` | 빈 값 | 한 단계라도 Anthropic을 선택하면 필요 |
| `NEGOPILOT_DECOMPOSER_EFFORT` | `low` | Claude 질문 해석 effort |
| `NEGOPILOT_GENERATOR_EFFORT` | `medium` | Claude 답변 생성 effort |
| `NEGOPILOT_DECOMPOSER_FALLBACKS` | `1` | Claude 질문 해석의 서버 측 대체 모델 설정 |
| `NEGOPILOT_GENERATOR_FALLBACKS` | `1` | Claude 답변 생성의 서버 측 대체 모델 설정 |
| `AGENT_REQUEST_TIMEOUT_SECONDS` | `300` | Python의 전체 질문 처리 제한 시간 |
| `AGENT_RESPONSE_TIMEOUT_SECONDS` | `310` | Spring의 Agent 응답 대기 제한 시간 |
| `OLLAMA_BASE_URL` | `http://host.docker.internal:11434` | 컨테이너에서 접근할 Ollama 주소 |
| `OLLAMA_TIMEOUT_SECONDS` | `300` | Ollama 모델 호출 제한 시간 |
| `OLLAMA_NUM_CTX` | `32768` | Ollama 문맥 크기 |
| `OLLAMA_NUM_PREDICT` | `4096` | Ollama 출력 토큰 상한 |
| `LANGSMITH_TRACING` | `false` | LangSmith 추적. `true`이고 `LANGSMITH_API_KEY`도 있을 때만 켜짐. 켜면 질문과 협력사 데이터, 법령 근거가 LangSmith로 전송됨 |
| `LANGSMITH_API_KEY` | 빈 값 | LangSmith API 키. 각자 발급해 `.env`에만 넣음 |
| `LANGSMITH_PROJECT` | `negopilot` | 추적을 모을 LangSmith 프로젝트 이름. 팀이 같은 이름을 쓰면 한 프로젝트에 모임 |
| `LANGSMITH_HIDE_INPUTS`, `LANGSMITH_HIDE_OUTPUTS` | `false` | `true`면 입력·출력 내용 없이 실행 구조와 시간·토큰만 남김 |

Docker 시작기는 MODEL 값이 미설정이거나 빈 문자열이면 기존 코어의 공급자 기본값을 사용합니다. OpenAI는 `gpt-4.1-mini`입니다. Anthropic은 `claude-opus-5-5`이며 Ollama는 `qwen3:8b`입니다. 공백만 입력한 모델 값은 설정 오류입니다. 공급자를 바꿀 때 이전 공급자의 MODEL 값도 함께 바꾸거나 비워야 합니다. FALLBACKS는 stub 전환 설정이 아닙니다.

예를 들어 두 단계를 Ollama로 바꾸려면 BACKEND 두 값을 `ollama`로 지정하고 MODEL 두 값에 준비한 모델 이름을 넣습니다. Ollama 서버와 모델은 Compose에 포함되지 않으므로 별도로 준비합니다. `host.docker.internal`로 호스트를 연결하지만 Ollama가 컨테이너에서 접근 가능한 주소를 수신해야 합니다. 이 연결에 문제가 있으면 `OLLAMA_BASE_URL`을 실제 접근 가능한 HTTP origin으로 지정합니다. 두 단계 모두 Ollama라면 OpenAI와 Anthropic 키는 필요하지 않습니다.

이 기본값 선택과 시작 전 검사는 Docker 시작기 `deploy/local/agent_start.py`의 동작입니다. 직접 uvicorn을 실행할 때는 [Agent 실행 안내](../agent/README.md#v2-live-모델-선택과-비용-없는-검사)의 환경 설정을 따릅니다. `.env`를 수정한 뒤에는 같은 `docker compose up --build -d` 명령으로 컨테이너에 반영합니다.

## 포트와 계정 설정

다음 변수는 저장소 루트의 `.env`에서 바꿀 수 있습니다. 셸 환경변수에 설정한 값이 `.env`보다 우선합니다.

| 변수 | 기본값 | 용도 |
| --- | --- | --- |
| `LOCAL_WEB_PORT` | `5173` | 기본 live 웹 포트 |
| `TEST_WEB_PORT` | `5174` | 별도 stub 테스트 웹 포트 |
| `LOCAL_ADMIN_EMAIL` | `admin@negopilot.local` | 최초 로컬 관리자 이메일 |
| `LOCAL_ADMIN_PASSWORD` | `LocalAdmin2026!` | 최초 로컬 관리자 비밀번호 |
| `LOCAL_USER_EMAIL` | `user@negopilot.local` | 승인된 로컬 회원 이메일 |
| `LOCAL_USER_PASSWORD` | `LocalUser2026!` | 승인된 로컬 회원 비밀번호 |
| `POSTGRES_PASSWORD` | `local-postgres-password` | PostgreSQL 초기화와 상태 확인 |
| `APP_DB_PASSWORD` | `local-app-password` | 회원과 세션 및 대화 DB 연결 |
| `BUSINESS_DB_PASSWORD` | `local-business-password` | Spring의 business DB 쓰기 연결 |
| `BUSINESS_READER_PASSWORD` | `local-reader-password` | business MCP의 조회 전용 연결 |
| `LOCAL_AGENT_API_KEY` | `local-agent-test-key` | Spring과 Python의 내부 키 |

예를 들어 `.env`에서 `LOCAL_WEB_PORT=5178`로 바꾼 뒤 기본 시작 명령을 실행하면 접속 주소는 `http://localhost:5178`입니다. 이 방법은 Windows에서도 사용할 수 있습니다.

계정 비밀번호는 해당 계정이 처음 생성되기 전에 정합니다. DB 비밀번호는 최초 volume 초기화 전에 정합니다. 이후 `.env`의 비밀번호만 바꿔도 저장된 비밀번호는 자동 변경되지 않습니다. 기존 volume과 다른 DB 비밀번호를 설정하면 연결이 실패할 수 있습니다.

## 처음 로그인하기

로컬 관리자와 승인된 일반 회원을 자동으로 준비합니다. 채팅 기능은 일반 회원으로 바로 확인할 수 있습니다.

| 계정 | 이메일 | 비밀번호 | 역할과 상태 |
| --- | --- | --- | --- |
| 일반 회원 | `user@negopilot.local` | `LocalUser2026!` | `USER`와 `ACTIVE` |
| 관리자 | `admin@negopilot.local` | `LocalAdmin2026!` | `ADMIN`과 `ACTIVE` |

기존 계정이나 암호는 덮어쓰지 않습니다. `admin-bootstrap`은 활성 관리자가 있으면 생성을 건너뜁니다. 지정한 이메일이 일반 회원이나 비활성 계정에 이미 사용 중이면 오류로 종료하고 backend 시작을 막습니다. 기존 계정을 자동 승격하거나 활성화하지 않습니다. DB volume을 유지한 채 다시 시작하면 기존 관리자로 로그인합니다.

`user-bootstrap`은 활성 관리자가 있는 상태에서 지정한 이메일의 `USER/ACTIVE` 계정을 준비합니다. 같은 이메일의 활성 일반 회원이 있으면 비밀번호와 회원 정보 및 기존 기록을 그대로 보존합니다. 이메일이 관리자나 `PENDING` 또는 `REJECTED` 또는 `DISABLED` 계정과 충돌하면 실패하며 자동 승인이나 역할 변경을 하지 않습니다. `LOCAL_ADMIN_EMAIL`과 `LOCAL_USER_EMAIL`은 서로 달라야 합니다.

기존 volume에 일반 회원이 없어도 다시 시작하면 추가됩니다. 이 계정을 만들기 위해 volume을 삭제할 필요는 없습니다. 기본 실행과 테스트 실행은 각자의 DB에서 같은 초기화 규칙을 사용합니다.

두 계정 모두 로그인 후 `/chat`을 거쳐 `/overview`의 오른쪽 AI 패널을 엽니다. 로그인 전에 관리자 경로로 접근했다면 관리자는 해당 화면으로 돌아갈 수 있습니다. 홈의 `관리자 화면`에서 회원 현황과 목록 및 가입 승인을 확인합니다.

이 기본 계정은 팀원의 로컬 기능 확인용입니다. 공개 서버 배포 설정으로 사용하지 않습니다. 공개 회원가입은 여전히 `USER/PENDING`을 생성하며 승인 전에는 채팅을 사용할 수 없습니다. 관리자는 `/admin/approvals` 또는 `/admin/members`에서 가입을 승인할 수 있습니다. 로컬 승인 회원 자동 생성은 공개 가입의 승인 규칙을 바꾸지 않습니다.

## 화면과 데이터 확인

기본 live 채팅에서는 `부산 협력사 찾아줘` 또는 `해솔정밀에 대해 알려줘`처럼 질문할 수 있습니다. 기업을 미리 선택할 필요는 없습니다. 후보가 여러 곳이면 Agent의 추가 질문에 답하고 같은 대화에서 후속 질문을 보냅니다. 질문별 처리 기준은 [직접 질문 계약](contracts/direct-chat.md)을 참고합니다. 모델 출력은 실행마다 달라질 수 있습니다.

| 화면 | 현재 상태 |
| --- | --- |
| 가입과 로그인 및 승인 대기 | 실제 회원 API와 세션을 사용함 |
| `/chat` | `/overview`로 이동한 뒤 오른쪽 AI 패널을 엶. 기본 실행은 실제 모델과 MCP를 사용함 |
| 근거 상세 | 답변의 근거 링크를 새 탭으로 열고 저장 응답을 조회함 |
| 개인 대화 기록 | DB에 저장됨. 현재 패널에는 전체 대화 목록과 제목 변경 및 삭제 UI가 없으므로 [개인 이력 API](contracts/history.md)를 사용해야 함 |
| `/suppliers` | 협력사 조회 API로 검색과 상세 조회를 실행함. 일부 연결 준비 문구는 화면에 남아 있음 |
| `/admin`과 `/admin/members` 및 `/admin/approvals` | 실제 회원 현황과 목록 및 가입 승인과 반려를 제공함 |
| 나머지 관리자 메뉴 | 접근 이력과 데이터 관리 및 등록 화면은 연결 준비 안내를 표시함 |
| `/overview`의 RFQ와 수치 및 `/review` | 화면용 예시 자료이며 실시간 업무 결과가 아님 |

회원과 로그인 세션 및 개인 대화는 실제 PostgreSQL에 저장합니다. 협력사 조회 API는 business MCP를 거쳐 별도 business DB를 조회합니다. 이 조회는 채팅 모드와 무관하게 동작하며 모델을 호출하지 않습니다. 승인된 계정으로 로그인한 상태에서 `/api/business/catalog`를 열면 MCP 카탈로그 응답을 확인할 수 있습니다. 검색 조건과 상세 경로는 [협력사 조회 계약](contracts/supplier-lookup.md)를 참고합니다.

`live`는 실제 모델이나 MCP 실행 경로를 뜻하며 모든 자료가 실데이터라는 뜻은 아닙니다. 초기 business DB에는 저장소의 DART 공시 스냅샷과 가상 기업 및 가상 운영 데이터가 함께 들어갑니다. 실행할 때마다 DART를 새로 수집하지 않습니다. 인증과 생산능력 및 품질 같은 항목은 기본 기업 정보의 출처와 다를 수 있습니다. 각 필드의 DART와 MOCK 및 MANUAL 출처를 구분해서 확인합니다. 홈의 RFQ와 수치는 예시 데이터입니다.

프런트는 Node.js 22로 빌드한 정적 파일을 Nginx가 제공합니다. `/api`와 `/health`는 같은 주소에서 `backend:8080`으로 전달합니다.

## 모델 키 없이 테스트 실행

테스트에는 `compose.test.yaml`을 추가합니다. 모델 키를 설정하지 않아도 실행할 수 있습니다.

```bash
docker compose -f compose.yaml -f compose.test.yaml up --build -d --wait
docker compose -f compose.yaml -f compose.test.yaml ps -a
docker compose -f compose.yaml -f compose.test.yaml logs --tail=100
```

[http://localhost:5174](http://localhost:5174)에서 같은 기본 계정으로 로그인합니다. 이 구성은 Spring과 Python의 채팅 모드를 `stub`으로 지정하고 OpenAI와 Anthropic 키를 빈 값으로 덮어씁니다. 모델과 채팅용 도구의 입출력은 테스트 응답을 사용합니다. 그래프와 근거 검증 및 DB 저장 코드는 실행합니다. `해솔에 대해 알려줘` 같은 정해진 예시로 후보 선택과 후속 대화를 확인합니다. stub 답변은 실제 모델의 판단이나 현재 DB 조회 결과가 아닙니다.

회원과 세션 및 대화 DB는 실제로 사용합니다. 별도 협력사 조회 API도 실제 business MCP와 DB를 사용합니다. stub 채팅 성공만으로 모델이나 채팅용 MCP 연결을 확인했다고 판단하지 않습니다.

기본 프로젝트 이름은 `negopilot-local`이며 테스트 프로젝트 이름은 `negopilot-test`입니다. 프로젝트별 DB와 법령 named volume이 분리됩니다. 테스트 웹 포트는 기본 포트에 추가되는 대신 `TEST_WEB_PORT`로 교체되므로 기본값에서 두 환경을 동시에 실행할 수 있습니다. `-p` 또는 `COMPOSE_PROJECT_NAME`을 직접 사용한다면 두 환경에 서로 다른 이름을 지정해야 합니다.

테스트 backend의 세션 쿠키 이름은 `NEGOPILOT_TEST_SESSION`으로 지정합니다. 쿠키는 포트만으로 격리되지 않으므로 기본 환경과 다른 이름을 사용해 로그인 세션을 구분합니다.

테스트를 종료할 때도 두 파일을 모두 지정합니다. 테스트를 시작한 뒤 `docker compose down`만 실행하면 기본 환경을 대상으로 하므로 사용하지 않습니다.

```bash
docker compose -f compose.yaml -f compose.test.yaml down
```

## 데이터 보존과 종료

각 프로젝트의 `postgres-data` named volume에는 애플리케이션 DB와 business DB를 저장합니다. 컨테이너를 중지하거나 다시 만들더라도 volume이 남아 있으면 저장된 데이터를 유지합니다. 초기 데이터 준비와 계정 생성은 기존 자료를 덮어쓰지 않습니다.

기본 환경의 일반 종료 명령은 다음과 같습니다. named volume은 유지됩니다.

```bash
docker compose down
```

다시 시작할 때는 같은 저장소 루트에서 `docker compose up --build -d`를 실행합니다. 테스트 환경은 시작과 상태 확인 및 종료까지 항상 `-f compose.yaml -f compose.test.yaml`을 붙입니다. 기존 데이터로 계속 작업할 때는 같은 프로젝트 이름을 유지합니다.

### 데이터를 삭제하고 처음부터 시작하기

`down -v`는 해당 프로젝트의 named volume을 삭제합니다. 회원과 비밀번호 및 대화와 business DB 자료 및 법령 최신화 이력도 사라집니다. 필요한 데이터를 별도로 백업했거나 해당 데이터를 삭제하려는 경우에만 실행합니다.

기본 환경을 삭제하는 명령입니다.

```bash
docker compose down -v
```

테스트 환경만 삭제하는 명령입니다.

```bash
docker compose -f compose.yaml -f compose.test.yaml down -v
```

## 연결 문제 확인

| 증상 | 확인할 내용 |
| --- | --- |
| 기본 실행에서 Agent가 시작되지 않음 | `docker compose logs agent`에서 선택 공급자와 모델 및 필수 키 설정을 확인합니다. 키를 로그나 Git에 복사하지 않습니다. |
| healthy인데 실제 질문 실패 | 시작 검사는 키의 유효성이나 모델 사용 권한을 확인하지 않습니다. 응답 오류와 Agent 로그에서 모델 및 MCP 연결을 확인합니다. |
| 5173 또는 5174 포트 사용 중 | 기본 실행은 `LOCAL_WEB_PORT`를 바꾸고 테스트는 `TEST_WEB_PORT`를 바꿉니다. |
| `!override`를 읽지 못함 | Docker Compose를 2.24.4 이상으로 갱신합니다. |
| 로그인 화면은 열리지만 API 실패 | 사용 중인 Compose 구성의 `ps`와 로그에서 backend 및 DB 초기화 상태를 확인합니다. |
| 기본 계정 로그인 실패 | 기존 volume의 해당 계정 이메일과 비밀번호를 확인합니다. 초기 설정을 바꿔도 기존 암호는 바뀌지 않습니다. |
| `admin-bootstrap` 오류 종료 | 지정 이메일이 기존 일반 회원이나 비활성 계정과 충돌하는지 확인합니다. |
| `user-bootstrap` 오류 종료 | 활성 관리자가 있는지 확인합니다. 지정 이메일이 다른 역할이나 상태의 계정과 충돌하면 기존 계정을 변경하지 않고 실패합니다. |
| `.env` 변경 후 DB 연결 실패 | 기존 volume의 DB 비밀번호와 설정이 같은지 확인합니다. 환경변수 수정은 DB 비밀번호 변경 명령이 아닙니다. |
| 회원가입 후 승인 대기 | 관리자 화면에서 가입을 승인해야 합니다. |
| stub 질문이 거절됨 | 정해진 테스트 질문을 사용합니다. 임의 질문의 실제 모델 답변은 기본 live 환경에서 확인합니다. |
| 협력사 조회 API 실패 | business MCP와 business DB 상태를 확인합니다. |

기본 live 구성의 전체 Agent 실행 제한은 Python 300초와 Spring 310초입니다. 테스트 구성은 Python 30초와 Spring 35초입니다. 프런트 질문 생성은 최소 330초를 기다리고 Nginx의 API 응답 대기 시간은 360초입니다. `/health`는 Spring 생존 확인이며 모델이나 모든 MCP의 연결 성공을 보증하지 않습니다.

## 키 없는 통합 검사

`compose_smoke.py`는 stub 환경을 대상으로 기본 일반 회원 로그인과 공개 가입 및 승인과 실제 MCP 조회 및 후속 채팅을 검사합니다. 두 일반 회원과 관리자가 서로의 개인 대화를 볼 수 없는지도 확인합니다. 재시작 후 저장된 계정과 대화를 다시 조회하는 모드도 제공합니다. 실제 모델 API와 외부 법령 수집은 호출하지 않습니다.

Python 3가 있는 환경에서 다음 명령을 사용합니다. 기본 일반 회원과 별개로 가입 및 승인 검증용 회원 한 명을 만들고 두 일반 회원의 대화를 저장합니다. state 파일에는 로그인 정보가 저장됩니다. 최초 검사에는 아직 없는 state 파일 경로를 사용합니다.

```bash
docker compose -f compose.yaml -f compose.test.yaml up --build -d --wait
python3 scripts/integration/compose_smoke.py --base-url http://127.0.0.1:5174 --state /tmp/negopilot-compose-test-state.json
docker compose -f compose.yaml -f compose.test.yaml down
docker compose -f compose.yaml -f compose.test.yaml up -d --wait
python3 scripts/integration/compose_smoke.py --base-url http://127.0.0.1:5174 --verify-state /tmp/negopilot-compose-test-state.json
docker compose -f compose.yaml -f compose.test.yaml down
```

테스트 포트를 바꾸었다면 두 Python 명령의 `--base-url`도 바꿉니다. 기본 계정 설정을 바꾸었다면 스크립트를 실행하는 환경에도 해당 `LOCAL_ADMIN_EMAIL`과 `LOCAL_ADMIN_PASSWORD` 및 `LOCAL_USER_EMAIL`과 `LOCAL_USER_PASSWORD`를 지정합니다. 스크립트는 `.env`를 직접 읽지 않습니다. 자동 검증 스크립트는 POSIX 파일 권한을 사용하므로 macOS와 Linux 또는 Windows의 WSL 환경에서 실행합니다. 일반 Docker 실행에는 이 스크립트나 호스트 Python이 필요하지 않습니다.
