# NegoPilot Spring backend

Java 21과 Spring Boot 4.1.1을 사용한다. 기본 실행은 PostgreSQL에 회원과 로그인 세션 및 개인 채팅 이력을 저장한다. Python Agent의 분해와 Routing 및 Generation 응답 계약은 v0.3을 유지한다. 대화 목록과 저장 스냅샷은 v0.4다.

기능별 파일 위치와 요청 흐름은 [백엔드 구조](../docs/BACKEND_STRUCTURE.md)에서 확인한다.

전체 서비스를 Docker로 실행할 때는 [로컬 Docker 안내](../docs/LOCAL_DOCKER.md)를 따른다. 기본 `compose.yaml`은 실제 모델을 사용하는 live 환경이다. 기본 OpenAI 구성에는 루트 `.env`에 모델 키를 준비해야 한다. 키 없는 검사는 `compose.yaml`과 `compose.test.yaml`을 함께 사용하는 별도 stub 환경에서 실행한다.

## API 명세

| 영역 | 명세 | 주요 경로 |
| --- | --- | --- |
| 인증과 가입 승인 및 접근 이력 | [membership.md](../docs/contracts/membership.md) | `/api/auth` 및 `/api/admin/members` |
| 개인 채팅 이력 | [history.md](../docs/contracts/history.md) | `/api/conversations` |
| 협력사 조회 | [supplier-lookup.md](../docs/contracts/supplier-lookup.md) | `/api/business/catalog` 및 `/api/suppliers` |
| 협력사 관리 | [admin-suppliers.md](../docs/contracts/admin-suppliers.md) | `/api/admin/suppliers` |
| 데이터 현황과 법령 버전 및 최신화 | [admin-laws.md](../docs/contracts/admin-laws.md) | `/api/admin/data/summary` 및 `/api/admin/laws` |
| Agent 실행 응답 | [agent-generation.md](../docs/contracts/agent-generation.md) | `/internal/v2/query-executions` |

일반 업무 API는 ACTIVE 회원만 사용한다. 관리자 API는 ACTIVE ADMIN만 사용한다. 관리자도 다른 회원의 채팅은 읽을 수 없다. 회원 제거는 비활성화와 세션 폐기로 처리한다. 개인 대화 삭제는 메시지와 저장 응답 및 근거까지 물리 삭제한다.

## DB 준비

애플리케이션 DB와 비즈니스 DB를 분리한다. 실행 전에 PostgreSQL에 애플리케이션 전용 DB와 접근 계정을 만든다. 기본 DB명은 `negopilot_app`이다. Flyway가 회원과 세션 및 대화 테이블을 생성한다. 운영 DB의 데이터를 자동 seed하거나 초기화하지 않는다.

| 환경 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `APP_DATABASE_URL` | `jdbc:postgresql://127.0.0.1:5432/negopilot_app` | 회원과 세션 및 채팅 DB |
| `APP_DATABASE_USER` | `negopilot_app` | 애플리케이션 DB 계정 |
| `APP_DATABASE_PASSWORD` | 빈 값 | 비밀 설정에서 주입 |
| `BUSINESS_DATABASE_ENABLED` | `false` | 관리자 협력사 기능 활성화 |
| `BUSINESS_DATABASE_URL` | 빈 값 | 기존 비즈니스 DB의 JDBC URL |
| `BUSINESS_DATABASE_USER` | 빈 값 | 비즈니스 관리 쓰기 계정 |
| `BUSINESS_DATABASE_PASSWORD` | 빈 값 | 비밀 설정에서 주입 |
| `SESSION_COOKIE_SECURE` | `true` | 로컬 HTTP 확인에만 `false` |
| `SERVER_ADDRESS` | `127.0.0.1` | Spring 수신 주소 |
| `SERVER_PORT` | `8080` | Spring 포트 |
| `AGENT_BASE_URL` | `http://127.0.0.1:9200` | Python origin |
| `AGENT_INTERNAL_API_KEY` | 빈 값 | Spring과 Python이 공유하는 내부 키 |
| `AGENT_EXECUTION_MODE` | `live` | Spring과 Python에 같은 모드 설정 |
| `AGENT_CONNECT_TIMEOUT_SECONDS` | `2` | 내부 연결 제한 |
| `AGENT_RESPONSE_TIMEOUT_SECONDS` | `35` | 내부 응답 제한 |

비즈니스 DB는 기존 8개 테이블을 준비한 뒤 활성화한다. Spring이 별도 Flyway 이력으로 관리 열을 추가한다. business-mcp를 먼저 실행할 때는 [독립 마이그레이션 명령](../docs/contracts/admin-suppliers.md)을 실행한다. business-mcp의 `DATABASE_URL`은 동일 DB에 대한 조회 전용 계정을 사용한다. 새 데모 DB에만 기존 seed를 명시적으로 실행한다. 기존 DB에 seed를 실행하면 안 된다.

`BUSINESS_DATABASE_ENABLED=false`여도 회원과 채팅은 사용할 수 있다. 협력사 관리 API는 503을 반환한다. 데이터 현황은 해당 영역을 `UNAVAILABLE`과 `data:null`로 반환한다. 이 설정은 Spring의 관리자 쓰기 기능 설정이다. 협력사 조회는 Python과 business-mcp의 별도 DB 연결을 사용한다.

## 최초 관리자와 실행

[최초 관리자 생성 절차](../docs/contracts/membership.md#최초-관리자-생성)에 따라 `AuthBootstrapAdmin` CLI를 한 번 실행한다. 비밀번호는 콘솔 또는 비밀 환경변수로 전달한다. CLI는 HTTP 서버를 띄우지 않는다. 공개 가입으로 ADMIN을 만들 수 없다.

저장소 루트에서 실행한다.

```bash
mvn -f backend/pom.xml -B -ntp package
java -jar backend/target/negopilot-backend-0.1.0-SNAPSHOT.jar
```

[Python 실행 안내](../agent/README.md)에 따라 내부 API를 실행한다. 모델 없이 연결을 확인할 때는 Spring과 Python 모두 `AGENT_EXECUTION_MODE=stub`를 명시한다. stub에서도 회원과 세션 및 대화 저장을 위한 애플리케이션 PostgreSQL DB는 필요하다. 실제 모델을 연결할 때는 두 서버에 `live`를 설정하고 Python의 모델 공급자를 지정한다. 실패한 live 실행을 stub으로 대체하지 않는다.

협력사 조회는 위 채팅 모드와 관계없이 business-mcp를 조회한다. 모델을 호출하지 않으며 성공 응답은 `schema_version: "0.3"`과 `mode: "live"`다. 채팅이 stub이어도 협력사 조회에는 Python과 business-mcp 및 business DB 연결이 필요하다. `/suppliers` 화면도 이 API로 검색과 상세를 조회한다. v1 조회와 메모리 대화 API는 #130에서 제거됐다.

법령 조회에는 law-mcp가 필요하다. 법령 최신화에는 law-mcp의 `LAW_RUNTIME_DIR`과 별도 `LAW_ADMIN_API_KEY`가 필요하다. Python에도 같은 법령 관리 키와 `LAW_MCP_URL`을 설정한다. [법령 서버 실행 안내](../mcp-servers/law-mcp/README.md)를 따른다.

로컬 Ollama로 직접 대화하려면 [Ollama native 공급자](../agent/README.md#ollama-native-공급자)와 [개발 시작 가이드의 실행 순서](../docs/GETTING_STARTED.md#5-로컬에서-돌려-보기)를 따른다. 전체 실행 제한은 Python의 `AGENT_REQUEST_TIMEOUT_SECONDS`와 Spring의 `AGENT_RESPONSE_TIMEOUT_SECONDS`에 각각 설정한다. 프런트 대화 생성 요청은 기본 330초를 기다린다. 각 제한은 실제 환경에 맞춰 조정한다.

루트 Docker 기본 구성은 Python 300초와 Spring 310초를 지정한다. 테스트 override는 Python 30초와 Spring 35초를 사용한다. 위 환경 변수 표는 Docker 설정을 적용하지 않은 독립 실행의 기본값이다.

## 현재 프런트 연결 상태

| 화면 | 현재 동작 |
| --- | --- |
| 가입과 로그인 및 승인 대기 | Spring의 실제 회원 API와 세션을 사용한다. `PENDING`은 대기 화면으로 이동한다. 승인 상태를 서버에서 다시 확인한다. |
| `/chat` | `/overview`로 이동한 뒤 오른쪽 AI 패널을 연다. 기업 선택 없이 질문하며 Spring이 Python Agent의 분해와 도구 호출 및 Generation을 실행한다. stub 답변에는 테스트 응답 표시가 붙는다. |
| 개인 대화 기록과 근거 상세 | 메시지와 근거 상세는 DB 저장 응답을 조회한다. 전체 대화 목록과 제목 변경 및 영구 삭제는 API를 제공하지만 현재 패널에는 해당 UI가 없다. PostgreSQL 저장이므로 Spring 재시작 후에도 이력은 남는다. |
| `/admin`과 `/admin/members` 및 `/admin/approvals` | 실제 회원 현황과 목록 및 가입 승인과 반려를 제공한다. 나머지 관리자 메뉴는 연결 준비 안내를 표시한다. |
| `/suppliers` | 협력사 조회 API로 검색과 상세를 조회한다. 일부 연결 준비 문구는 화면에 남아 있다. |
| `/overview`와 `/review` | RFQ 개요와 법규 검토 예시는 화면용 자료다. 실시간 조회나 실행 결과로 해석하지 않는다. |

프런트는 같은 출처의 `/api` 요청에 세션 쿠키를 보내며 변경 요청 전에 현재 CSRF 토큰을 조회한다. 회원의 역할과 상태는 `/api/auth/me`로 확인한다. 대화 목록과 저장 스냅샷의 v0.4를 Generation 응답의 v0.3과 구분한다.

관리자 화면에서 가입을 승인하거나 [회원 계약](../docs/contracts/membership.md#관리자-회원-api)에 따라 관리자 세션과 CSRF 토큰으로 승인 API를 호출한다. `PATCH /api/admin/members/{id}/status`에 `{"status":"ACTIVE"}`를 보낸다. 공개 가입으로 관리자 권한을 지정할 수 없다.

프런트 실행은 저장소 루트에서 별도 터미널을 사용한다.

```bash
cd frontend
npm ci
NEGOPILOT_BACKEND_URL=http://127.0.0.1:8080 npm run dev
```

Vite는 `/api`와 `/health`를 위 Spring 주소로 전달한다. 로컬 HTTP 실행에는 Spring의 `SESSION_COOKIE_SECURE=false`가 필요하다. 운영 HTTPS에서는 기본값 `true`를 유지한다. 내부 API 키와 DB 비밀번호 및 모델 키는 프런트 환경변수에 넣지 않는다.

`GET /health`는 인증 없이 서버 생존 상태만 확인한다. Python이나 MCP의 연결 성공을 보증하지 않는다.

## 검증

```bash
mvn -f backend/pom.xml -B -ntp test
```

일반 검사에는 HTTP 스텁과 단위 테스트가 포함된다. HTTP 스텁 테스트는 DB와 회원 보안 없이 필요한 컨트롤러만 띄우므로 DB 설정 없이도 실행된다. 실제 DB 테스트는 `TEST_DATABASE_URL`과 `TEST_DATABASE_USER` 및 `TEST_DATABASE_PASSWORD`를 지정해야 실행된다. TEST_DATABASE_URL은 폐기 가능한 테스트 DB여야 한다. 전체 DB 검사는 DB명 `business_test`를 사용한다. 회원과 이력 테스트는 앱 테이블을 초기화한다. 협력사 테스트는 독립 스키마를 만들고 제거한다.

```bash
TEST_DATABASE_URL='jdbc:postgresql://127.0.0.1:5432/business_test' \
TEST_DATABASE_USER='test_user' \
TEST_DATABASE_PASSWORD="$TEST_DB_PASSWORD" \
mvn -f backend/pom.xml -B -ntp test
```

