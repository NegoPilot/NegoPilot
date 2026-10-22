# 백엔드 리팩터링 검증

검증일은 2026-10-22 KST다. 기준 HEAD는 `16afb1d1f111820b263467abb7cb7ad27f7d2925`다. `feat/32-spring-http-backend`의 작업 트리에서 기능별 패키지 이동과 서비스 및 저장소 분리와 공통 HTTP 추출을 검증했다. [구조 안내](../BACKEND_STRUCTURE.md)에서 변경된 파일 위치를 확인할 수 있다.

## 전체 Java 테스트

기준 소스는 전체 저장소를 git archive로 임시 디렉터리에 추출했다. 미추적 파일과 기존 target을 포함하지 않았다. 기준 실행과 최종 실행 모두 Java 21.0.11 및 Maven과 전용 PostgreSQL 17.11을 사용했다. 최종 실행은 모든 팀 편집 완료 후 공유 checkout의 backend에서 `clean test`로 수행했다.

| 실행 | suite | 테스트 | 실패 | 오류 | skip |
| --- | ---: | ---: | ---: | ---: | ---: |
| 기준 archive | 23 | 898 | 0 | 0 | 0 |
| 패키지 이동 직후 | 23 | 898 | 0 | 0 | 0 |
| 모든 기능 리팩터링 통합 후 | 28 | 941 | 0 | 0 | 0 |

최종 결과는 BUILD SUCCESS이며 실행 시간은 31.838초다. 기준의 898개 사례를 보존하고 43개를 추가했다. 897개는 기존 테스트 이름이 유지됐다. 비활성 business DB 검증 1개는 `disabledBusinessStoreAllowsServiceConstructionAndFailsOnlyOnAccess`로 이름을 바꾸고 서비스 접근 시 오류 검증을 추가했다. 기존 enabled=false와 JDBC 오류 코드 assertion이 남아 있음을 소스에서 확인했다. 테스트 이름 변경을 삭제로 계산하지 않았다.

CI의 실제 Surefire XML 검사 스크립트도 실행했다. 아래 PostgreSQL suite가 모두 실행됐으며 전체 skip이 0임을 확인했다.

| 필수 PostgreSQL suite | 테스트 |
| --- | ---: |
| `auth.AuthMembershipPostgresTest` | 42 |
| `auth.AuthBootstrapLocalUserPostgresTest` | 13 |
| `chat.PersistentConversationServiceTest` | 22 |
| `admin.supplier.AdminSupplierPostgresTest` | 10 |

실행 당시 전용 컨테이너는 `negopilot-refactor-foundation-db`였다. 공유 DB와 다른 tmpfs 저장소를 사용했고 localhost의 임의 포트 52547만 게시했다. 테스트가 끝난 뒤 이 컨테이너는 제거했다. 아래 값은 당시 실행 기록이며 현재 연결 가능한 DB를 뜻하지 않는다.

```sh
cd backend
JAVA_HOME=/Library/Java/JavaVirtualMachines/temurin-21.jdk/Contents/Home \
TEST_DATABASE_URL=jdbc:postgresql://127.0.0.1:52547/business_test \
TEST_DATABASE_USER=refactor \
TEST_DATABASE_PASSWORD=foundation_temp_only \
mvn -B -ntp clean test
```

첫 기준 실행은 테스트 DB 이름을 foundation_test로 정해 AdminSupplierPostgresTest의 안전 조건에서 실패했다. 이 실행은 통과로 계산하지 않았다. 전용 컨테이너에 business_test를 만든 뒤 clean test로 다시 실행했다. 최종 통합 실행에서는 실패가 없었다.

## 계약과 호환성

기준 소스와 최종 소스를 읽기 전용으로 비교했다. 컨트롤러 10개의 MVC 어노테이션 117개가 같았다. HTTP 메서드 매핑은 49개로 유지됐다. profile에 따라 같은 URL을 처리하는 별도 컨트롤러도 이 수에 포함된다.

회원 JSON의 9개 필드와 user wrapper를 유지했다. 인증과 권한 및 CSRF와 세션 회전 및 폐기 규칙을 비교했다. 대화의 lease와 멱등 응답 및 소유자 조건과 관리자 데이터의 version 및 감사 트랜잭션은 기존 검증에 추가 회귀 사례를 더해 확인했다. Gateway별 오류 ID와 상태 코드 및 retryable 규칙과 응답 정리 문구도 유지했다.

`UserIdentity` 소스는 기준과 바이트 단위로 동일하다. 기존 클래스가 직렬화한 객체를 최종 클래스에서 읽었으며 반대 방향도 확인했다. 두 직렬화 결과 바이트가 같았고 serialVersionUID는 0이다. 루트 bootstrap 클래스의 인자 거절 동작을 직접 실행했다. Compose에서는 같은 루트 FQCN을 사용해 관리자와 로컬 사용자를 실제로 생성했다.

application 설정과 DB migration 및 계약 문서와 fixture는 변경하지 않았다. migration SHA-256은 다음과 같다.

| 파일 | SHA-256 |
| --- | --- |
| `V1__membership.sql` | `9fffc8a596f7fa181ebadbf0bb393719bea914004fb70ba9c77930a72bda4310` |
| `V2__conversation_history.sql` | `a2c2df8760d167a443ac0b2712cbfc3675a7daee858bfbf0321151ea96608b2a` |
| `V1__supplier_management.sql` | `24ef4577af0987e3b10e319a7ec355edf637bcadb6abefdb9f7518022a7f8205` |

의도한 profile 변경은 SupplierChatGateway와 SupplierChatConfiguration의 legacy-test 제한이다. 실제 v2 조회는 새 SupplierLookupGateway가 담당해 두 profile에서 계속 사용할 수 있다. 일반 실행에서 레거시 fixture bean이 생성되지 않는 점과 legacy-test에서 두 조회 adapter가 공존하는 점을 별도 context 테스트로 확인했다.

## 격리 Compose 검증

기존 root compose.yaml을 수정하지 않고 별도 프로젝트 `negopilot-refactor-final-20261022`로 실행했다. 기존 프로젝트와 같은 이름의 컨테이너나 volume이 없음을 먼저 확인했다. 웹 포트는 사용 중이지 않은 `127.0.0.1:60200`을 선택했다. `.env`를 읽지 않도록 `--env-file /dev/null`을 지정했고 테스트 환경 변수만 사용했다.

| 단계 | 결과 |
| --- | --- |
| 전체 build와 up 및 health 대기 | 성공. 35.932초 |
| 최초 compose_smoke.py | 성공. 3.561초 |
| volume을 유지하는 down | 성공 |
| bootstrap 비밀번호 환경 변수를 바꾼 up | 성공. 28.612초 |
| 원래 상태의 verify-state | 성공. 1.085초 |
| 전용 프로젝트 자원 정리 | 성공 |

실행한 순서는 다음과 같다. 초기 실행과 검증은 Compose의 기본 로컬 테스트 자격 정보를 사용했다. 변경 비밀번호는 재생성 명령에만 전달했다.

```sh
export COMPOSE_PROJECT_NAME=negopilot-refactor-final-20261022
export LOCAL_WEB_PORT=60200

docker compose --env-file /dev/null -p "$COMPOSE_PROJECT_NAME" -f compose.yaml \
  up --build -d --wait --wait-timeout 180

python3 scripts/integration/compose_smoke.py \
  --base-url http://127.0.0.1:60200 \
  --state /tmp/negopilot-refactor-compose-state.json

docker compose --env-file /dev/null -p "$COMPOSE_PROJECT_NAME" -f compose.yaml down

LOCAL_ADMIN_PASSWORD='MustNotReplace2026!' \
LOCAL_USER_PASSWORD='MustNotReplaceUser2026!' \
docker compose --env-file /dev/null -p "$COMPOSE_PROJECT_NAME" -f compose.yaml \
  up -d --wait --wait-timeout 180

python3 scripts/integration/compose_smoke.py \
  --base-url http://127.0.0.1:60200 \
  --verify-state /tmp/negopilot-refactor-compose-state.json
```

최초 smoke는 기본 ACTIVE USER와 신규 USER/PENDING 가입을 확인했다. 관리자 승인 뒤 기존 pending 세션의 권한 갱신과 관리자 API 거절 및 CSRF를 검사했다. 실제 공급사 catalog와 검색 및 상세를 조회했고 법령 catalog와 refresh-job 조회를 확인했다. 각 사용자에게 후속 질문이 있는 대화를 저장한 뒤 목록과 메시지 및 저장 응답을 확인했다. 다른 사용자와 관리자도 타인의 대화를 읽을 수 없었다. 로그아웃 후 세션이 무효화되는 점도 확인했다.

재생성 때 두 bootstrap은 기존 계정을 발견해 변경을 건너뛰고 종료 코드 0으로 끝났다. 원래 관리자와 사용자 비밀번호로 다시 로그인했다. 두 USER ID와 메시지 8개 및 전체 답변과 제목이 유지됐다. 재생성 후에도 사용자별 접근 격리가 유지됐다.

## 검증 모드와 한계

Java HTTP 테스트는 로컬 HTTP stub을 사용한다. 이 결과만으로 Python과 MCP의 실제 연결을 확인했다고 주장하지 않는다.

Compose 검증은 실제 Python 프로세스와 business-mcp 및 law-mcp와 신규 테스트 PostgreSQL을 연결했다. 채팅 실행은 Spring과 Python 모두 `AGENT_EXECUTION_MODE=stub`으로 고정했다. 대화 응답은 schema_version 0.3과 mode stub이다. 실제 모델 API는 호출하지 않았으므로 모델 답변 품질과 live generation을 검증한 결과는 아니다.

v2 공급사 조회는 채팅 모드와 관계없이 실제 business-mcp와 seed된 테스트 DB를 사용했으며 응답 mode는 live다. 법령은 로컬 데이터 조회를 검증했다. 외부 법령 수집과 실제 최신화 작업은 실행하지 않았다. 기존 shared 서버의 배포를 교체하거나 실제 데이터를 검증한 작업도 아니다.

## 정리와 기록

다른 팀의 DB 테스트가 끝났고 foundation DB에 다른 client 연결이 없음을 확인한 뒤 제가 만든 자원만 정리했다.

```sh
docker compose --env-file /dev/null -p negopilot-refactor-final-20261022 \
  -f compose.yaml down -v --remove-orphans
docker stop negopilot-refactor-foundation-db
```

전용 Compose의 컨테이너 8개와 volume 2개 및 network 1개를 제거했다. 전용 프로젝트 이름의 이미지 태그 6개도 제거했다. foundation DB는 생성 시 --rm을 지정했으므로 stop 후 자동 제거됐다. 테스트 비밀번호가 들어 있는 mode 0600의 smoke 상태 파일도 삭제했다. 공유 build cache는 정리하지 않았다.

정리 후 해당 프로젝트의 컨테이너와 volume 및 network가 모두 0개임을 확인했다. 기존 negopilot-shared의 서비스 7개는 실행 중이다. healthcheck가 있는 6개는 healthy였으며 cloudflared도 실행 중이었다.

로컬 실행 증거는 `/tmp/negopilot-refactor-final-maven.log`와 `/tmp/negopilot-refactor-final-test-results.json` 및 `/tmp/negopilot-refactor-final-reports/`에 보관했다. Compose 단계별 로그와 종료 코드는 `/tmp/negopilot-refactor-compose-run.json`에 있다. 호환성 소스 점검은 `/tmp/negopilot-refactor-final-compatibility-audit.md`에 있다. 이 임시 경로는 현재 작업 환경의 기록이며 저장소에 포함된 산출물은 아니다.
