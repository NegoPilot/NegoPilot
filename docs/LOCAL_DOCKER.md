# Docker로 로컬 실행하기

Docker Desktop 또는 Docker Engine과 Compose 플러그인을 준비합니다. Docker가 실행 중이어야 합니다. 저장소 루트에서 다음 명령을 실행합니다.

```bash
docker compose up --build -d
```

처음 실행할 때는 이미지와 의존성을 내려받고 DB를 준비하므로 시간이 걸릴 수 있습니다. 실행 상태는 아래 명령으로 확인합니다.

```bash
docker compose ps -a
docker compose logs --tail=100
```

준비가 끝나면 [http://localhost:5173](http://localhost:5173)을 엽니다. `admin-bootstrap`은 관리자 초기화가 끝나면 `Exited (0)`으로 남는 일회성 서비스입니다. 나머지 서비스가 실행되고 `frontend`가 healthy인지 확인합니다. 초기화 로그는 `docker compose logs admin-bootstrap postgres`로 확인합니다.

웹 포트만 `127.0.0.1`에 공개합니다. DB와 Spring 및 MCP는 Compose 내부 네트워크를 사용합니다. 호스트에 Node.js와 Java 및 Python을 따로 설치하거나 Vite 개발 서버를 실행할 필요는 없습니다.

## 선택 설정

기본 실행에는 `.env` 파일이 필요하지 않습니다. 설정을 바꾸려면 저장소 루트의 `.env.example`을 같은 위치에 `.env`라는 이름으로 복사한 뒤 필요한 값을 수정합니다. 아래 변수는 Compose가 읽습니다.

| 변수 | 기본값 | 용도 |
| --- | --- | --- |
| `LOCAL_WEB_PORT` | `5173` | 로컬 웹 포트 |
| `LOCAL_ADMIN_EMAIL` | `admin@negopilot.local` | 최초 테스트 관리자 이메일 |
| `LOCAL_ADMIN_PASSWORD` | `LocalAdmin2026!` | 최초 테스트 관리자 비밀번호 |
| `POSTGRES_PASSWORD` | `local-postgres-password` | PostgreSQL 초기화와 상태 확인 |
| `APP_DB_PASSWORD` | `local-app-password` | 회원과 세션 및 대화 DB 연결 |
| `BUSINESS_DB_PASSWORD` | `local-business-password` | Spring의 business DB 쓰기 연결 |
| `BUSINESS_READER_PASSWORD` | `local-reader-password` | business MCP의 조회 전용 연결 |
| `LOCAL_AGENT_API_KEY` | `local-agent-test-key` | Spring과 Python의 내부 키 |
| `LOCAL_LAW_ADMIN_API_KEY` | `local-law-test-key` | Python과 law MCP의 법령 관리 키 |

예를 들어 `.env`에서 `LOCAL_WEB_PORT=5178`로 바꾼 뒤 같은 시작 명령을 실행하면 접속 주소는 `http://localhost:5178`입니다. 이 방법은 Windows에서도 사용할 수 있습니다. 환경변수에 설정한 값은 `.env`보다 우선합니다.

관리자와 DB 비밀번호는 최초 volume 초기화 전에 정합니다. 이후 `.env`의 비밀번호만 바꿔도 DB에 저장된 비밀번호는 자동 변경되지 않습니다. 기존 volume과 다른 DB 비밀번호를 설정하면 연결이 실패할 수 있습니다.

## 처음 로그인하기

빈 애플리케이션 DB에는 로컬 테스트용 관리자를 자동으로 생성합니다.

| 항목 | 기본값 |
| --- | --- |
| 이메일 | `admin@negopilot.local` |
| 비밀번호 | `LocalAdmin2026!` |
| 역할과 상태 | `ADMIN`과 `ACTIVE` |

기존 계정이나 암호는 덮어쓰지 않습니다. `admin-bootstrap`은 활성 관리자가 있으면 생성을 건너뜁니다. 지정한 이메일이 일반 회원이나 비활성 계정에 이미 사용 중이면 오류로 종료하고 backend 시작을 막습니다. 기존 계정을 자동 승격하거나 활성화하지 않습니다. DB volume을 유지한 채 다시 시작하면 기존 관리자로 로그인합니다. 초기 관리자 설정을 바꿔도 이미 생성된 계정의 비밀번호가 변경되는 것은 아닙니다.

이 기본 계정은 팀원의 로컬 기능 확인용입니다. 공개 서버 배포 설정으로 사용하지 않습니다. 새로운 일반 회원은 가입 후 `PENDING` 상태가 되며 승인 전에는 채팅을 사용할 수 없습니다. 관리자의 가입 승인 화면은 아직 연결 준비 상태이므로 [회원 API](contracts/membership.md#요청-순서)로 승인합니다.

## 기본 실행에서 확인하는 기능

- 실제 PostgreSQL DB에 회원과 로그인 세션 및 개인 대화 이력을 저장합니다.
- 채팅은 기본 `stub` 모드입니다. 모델 키 없이 정해진 테스트 질문과 후속 선택을 확인합니다.
- 협력사 조회 v2 API는 business MCP를 거쳐 별도 business DB를 조회합니다. 채팅이 stub이어도 이 조회는 MCP를 사용합니다.
- 프런트는 Node.js 22로 빌드한 정적 파일을 Nginx가 제공합니다. `/api`와 `/health`는 같은 주소에서 `backend:8080`으로 전달합니다.

채팅에서 `부산 협력사 찾아줘` 또는 `해솔정밀에 대해 알려줘`를 입력해 봅니다. `해솔에 대해 알려줘`에 나온 후보를 선택해 후속 대화도 확인할 수 있습니다. 지원 질문은 [직접 질문 계약](contracts/direct-chat.md)을 참고합니다. stub 답변은 실제 모델의 판단이나 현재 DB 조회 결과가 아닙니다.

답변의 근거 링크를 열고 대화 제목을 변경한 뒤 새로고침해 이력을 확인합니다.

| 화면 | 현재 상태 |
| --- | --- |
| 가입과 로그인 및 `/chat` | 실제 인증과 개인 이력 API에 연결됨. 기본 채팅 응답은 stub |
| `/suppliers` | 조회 클라이언트는 v2로 전환됐지만 화면의 `SUPPLIER_READ_AVAILABLE=false`가 유지되어 연결 준비 안내를 표시함. 화면에서 검색 요청을 실행하지 않음 |
| `/admin` 아래 화면 | 서버에서 관리자 권한을 확인한 뒤 연결 준비 안내를 표시함. 회원 승인과 데이터 변경은 화면에서 실행하지 않음 |

협력사 조회 v2 API 자체는 사용할 수 있습니다. 승인된 계정으로 로그인한 상태에서 같은 주소의 `/api/v2/business/catalog`를 열면 실제 MCP 카탈로그 응답을 확인할 수 있습니다. 검색 조건과 상세 경로는 [협력사 조회 v2](contracts/supplier-lookup.md)를 참고합니다.

`mode: "live"`인 협력사 조회도 자료 자체가 모두 실데이터라는 뜻은 아닙니다. DART 공시와 가상 데이터 및 관리자 입력을 각 필드의 출처로 구분해야 합니다.

## 데이터 보존과 종료

`postgres-data` named volume에는 애플리케이션 DB와 business DB를 저장합니다. `law-data`에는 법령 최신화 결과와 작업 상태를 저장합니다. 컨테이너를 중지하거나 다시 만들더라도 volume이 남아 있으면 저장된 데이터를 유지합니다. 초기 데이터 준비와 관리자 생성은 기존 자료를 덮어쓰는 동작으로 사용하지 않습니다.

일반 종료는 다음 명령입니다. named volume은 유지됩니다.

```bash
docker compose down
```

다시 시작할 때는 같은 저장소 루트에서 `docker compose up --build -d`를 실행합니다. 기본 Compose 프로젝트 이름은 `negopilot-local`입니다. `-p` 옵션이나 `COMPOSE_PROJECT_NAME`으로 이름을 바꾸면 다른 volume을 사용하므로 기존 데이터로 계속 작업할 때는 같은 프로젝트 이름을 유지합니다.

### 모든 로컬 데이터를 삭제하고 처음부터 시작하기

다음 명령은 컨테이너와 함께 이 Compose 프로젝트의 named volume을 삭제합니다. 회원과 비밀번호 및 대화와 business DB 자료 및 법령 최신화 이력도 사라집니다. 필요한 데이터가 없거나 별도로 백업한 경우에만 실행합니다.

```bash
docker compose down -v
docker compose up --build -d
```

## 연결 문제 확인

| 증상 | 확인할 내용 |
| --- | --- |
| 5173 포트 사용 중 | `LOCAL_WEB_PORT`에 다른 포트를 지정합니다. |
| 로그인 화면은 열리지만 API 실패 | `docker compose ps`와 로그에서 backend 및 DB 초기화 상태를 확인합니다. |
| 기본 관리자 로그인 실패 | 기존 volume의 관리자 이메일과 비밀번호를 확인합니다. 초기 설정을 바꿔도 기존 암호는 바뀌지 않습니다. |
| `admin-bootstrap` 오류 종료 | 초기화 로그를 확인합니다. 지정 이메일이 기존 일반 회원이나 비활성 계정과 충돌할 수 있습니다. |
| `.env` 변경 후 DB 연결 실패 | 기존 volume의 DB 비밀번호와 설정이 같은지 확인합니다. 환경변수 수정은 DB 비밀번호 변경 명령이 아닙니다. |
| 회원가입 후 승인 대기 | 정상 동작입니다. 관리자 API 승인이 필요합니다. |
| stub 질문이 거절됨 | 지원 질문 예시를 사용합니다. 임의 질문의 실제 모델 답변에는 별도의 live 설정이 필요합니다. |
| 협력사 조회 API 실패 | business MCP와 business DB 상태를 확인합니다. stub 채팅 성공은 MCP 조회 성공을 뜻하지 않습니다. |

기본 Compose의 Python 제한은 30초이며 Spring 제한은 35초입니다. Nginx의 API 응답 대기 시간은 360초이고 프런트 질문 생성은 기본 330초를 기다립니다. Compose에는 채팅 모드가 `stub`으로 지정되어 있으므로 `.env`에 모델 키만 넣어서는 live로 전환되지 않습니다. 실제 모델로 전환할 때는 [Agent 설정](../agent/README.md)과 [개발 시작 가이드](GETTING_STARTED.md)를 따라 Compose의 모드와 모델 및 시간 제한을 함께 설정해야 합니다. `/health`는 Spring 생존 확인이며 모델이나 모든 MCP의 연결 성공을 보증하지 않습니다.

## 통합 검증

CI는 빈 volume에서 시작한 뒤 가입과 승인 및 실제 MCP 조회와 후속 채팅을 검사합니다. 컨테이너를 다시 만들면서 초기 관리자 비밀번호 설정을 바꿔도 기존 계정과 채팅 데이터가 유지되는지 확인합니다. 모델 API와 외부 법령 수집은 호출하지 않습니다.

직접 같은 검사를 실행하려면 Python 3가 있는 환경에서 다음 명령을 사용합니다. 테스트용 일반 회원 한 명과 대화 한 개가 생성됩니다. state 파일에는 그 테스트 회원의 로그인 정보가 저장됩니다. 기본 관리자 설정을 바꿨다면 실행 환경에도 `LOCAL_ADMIN_EMAIL`과 `LOCAL_ADMIN_PASSWORD`를 지정합니다. 스크립트는 `.env`를 직접 읽지 않습니다.

```bash
docker compose up --build -d --wait
python3 scripts/integration/compose_smoke.py --state /tmp/negopilot-compose-state.json
docker compose down
docker compose up -d --wait
python3 scripts/integration/compose_smoke.py --verify-state /tmp/negopilot-compose-state.json
```

포트가 다르면 Python 명령에 `--base-url http://127.0.0.1:5178`처럼 실제 주소를 추가합니다. 최초 검사에는 아직 없는 state 파일 경로를 사용합니다. 자동 검증 스크립트는 POSIX 파일 권한을 사용하므로 macOS와 Linux 또는 Windows의 WSL 환경에서 실행합니다. 일반 Docker 실행에는 이 스크립트나 호스트 Python이 필요하지 않습니다.
