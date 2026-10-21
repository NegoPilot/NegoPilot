# 회원 및 인증 API

Spring 서버가 단일 조직의 회원과 세션을 관리한다. 역할은 `ADMIN`과 `USER` 두 개다. 가입 신청은 `USER/PENDING`으로 저장하며 관리자가 승인해야 업무 API를 사용할 수 있다.

## 세션과 권한

- `PENDING`은 로그인할 수 있다. 로그인 후에는 `/api/v1/auth/me`와 `/api/v1/auth/logout`만 사용할 수 있다. CSRF 토큰 조회는 허용한다.
- `ACTIVE`는 업무 API를 사용할 수 있다. `/api/v1/admin/**`는 활성 `ADMIN`만 사용할 수 있다.
- `REJECTED`와 `DISABLED`는 로그인할 수 없다. 기존 세션도 폐기한다.
- Spring Session JDBC가 PostgreSQL에 세션을 저장한다. 세션의 principal name은 회원 UUID다. 기본 유효 기간은 마지막 접근 후 30분이다.
- 로그인 성공 시 세션 ID를 회전하고 로그인 전 CSRF 토큰을 폐기한다. 클라이언트는 로그인 후 CSRF 토큰을 다시 조회해야 한다.
- 이미 로그인한 상태의 가입과 재로그인은 `403 FORBIDDEN`이다. 다른 계정으로 전환하려면 먼저 로그아웃한다.
- 요청마다 회원의 현재 상태와 역할을 DB에서 확인한다. 승인된 신청자의 기존 제한 세션은 다음 요청부터 활성 권한으로 갱신된다. 이때도 세션 ID를 회전한다.
- 역할 변경은 대상 회원의 모든 세션을 폐기한다. 회원 비활성화는 회원 정보와 변경 이력 및 채팅을 삭제하지 않는다.
- 기본 세션 쿠키는 `HttpOnly`와 `SameSite=Lax` 및 `Secure`를 사용한다. 로컬 HTTP 확인에만 `SESSION_COOKIE_SECURE=false`를 설정한다.
- `legacy-test`는 기존 API 회귀 테스트를 위한 보안 제외 프로필이다. 운영 환경에 사용하지 않는다.

## 요청 순서

1. `GET /api/v1/auth/csrf`로 익명 세션 쿠키와 CSRF 토큰을 받는다.
2. 변경 요청에 응답의 `header_name`을 헤더 이름으로 사용해 `token`을 전송한다. 기본 이름은 `X-CSRF-TOKEN`이다. 쿠키도 함께 보낸다.
3. 로그인 성공 후 `/api/v1/auth/csrf`를 다시 호출한다.
4. 이후 변경 요청에는 새 토큰을 사용한다. 로그아웃 후 다시 로그인할 때도 처음부터 진행한다.

가입과 로그인에도 CSRF 검증을 적용한다. CSRF 토큰이 없거나 유효하지 않으면 `403 CSRF_INVALID`다. 브라우저 요청은 동일 출처의 `/api` 프록시를 사용한다.

## 인증 API

| 메서드와 경로 | 요청 | 성공 응답 |
| --- | --- | --- |
| `GET /api/v1/auth/csrf` | 없음 | `200 {"header_name":"X-CSRF-TOKEN","token":"..."}` |
| `POST /api/v1/auth/signup` | 아래 가입 DTO | `201 {"user": Member}` |
| `POST /api/v1/auth/login` | `{"email":"user@example.com","password":"..."}` | `200 {"user": Member}` |
| `GET /api/v1/auth/me` | 없음 | `200 {"user": Member}` |
| `POST /api/v1/auth/logout` | 본문 없음 | `204` |

가입 DTO:

```json
{
  "name": "홍길동",
  "email": "user@example.com",
  "password": "ExamplePassword1!",
  "department": "구매팀",
  "job_title": "담당"
}
```

이름과 부서는 공백을 제거한 뒤 비어 있지 않아야 한다. 최대 길이는 각각 100자다. `job_title`은 최대 100자이며 생략하면 빈 문자열로 저장한다. 이메일은 앞뒤 공백 제거 후 소문자로 저장한다. 최대 254자이며 중복 가입은 `409 EMAIL_ALREADY_REGISTERED`다. 비밀번호는 8자 이상이며 영문자와 숫자 및 영문자나 숫자가 아닌 문자를 각각 포함해야 한다. BCrypt 입력 한계에 맞춰 UTF-8 기준 72바이트를 초과할 수 없다. 비밀번호는 BCrypt 비용 12로 저장한다.

가입과 로그인 및 회원 변경 JSON에서 알 수 없는 필드와 중복 필드 및 추가 JSON 문서는 거절한다. 가입 요청의 역할과 상태는 클라이언트가 정할 수 없다. 이메일 인증과 회사 코드 확인 및 팀장 역할은 이 계약에 없다. 현재 프런트 가입 화면의 `position`은 `job_title`로 매핑해야 한다. `companyCode`와 인증번호 및 `passwordConfirm`은 서버 가입 DTO에 보내지 않는다.

로그인 실패는 존재하지 않는 이메일과 틀린 비밀번호 및 로그인 불가 상태를 구분하지 않고 `401 INVALID_CREDENTIALS`로 반환한다.

`Member` DTO:

```json
{
  "id": "00000000-0000-0000-0000-000000000001",
  "email": "user@example.com",
  "name": "홍길동",
  "department": "구매팀",
  "job_title": "담당",
  "role": "USER",
  "status": "PENDING",
  "created_at": "2026-10-21T00:00:00Z",
  "updated_at": "2026-10-21T00:00:00Z"
}
```

비밀번호와 해시는 어떤 회원 응답에도 포함하지 않는다.

## 관리자 회원 API

| 메서드와 경로 | 요청 | 성공 응답 |
| --- | --- | --- |
| `GET /api/v1/admin/members` | 선택 쿼리 `search` `role` `status` `page` `size` | `200 Page<Member>` |
| `GET /api/v1/admin/members/{id}` | 회원 UUID | `200 {"user": Member}` |
| `PATCH /api/v1/admin/members/{id}/status` | `{"status":"ACTIVE"}` 등 | `200 {"user": Member}` |
| `PATCH /api/v1/admin/members/{id}/role` | `{"role":"ADMIN"}` 또는 `{"role":"USER"}` | `200 {"user": Member}` |

`search`는 최대 254자의 이름 또는 이메일 부분 검색이다. 대소문자를 구분하지 않으며 SQL 와일드카드로 해석하지 않는다. `role`과 `status`는 각 열거값과 정확히 일치해야 한다. 목록은 생성 시각 내림차순 다음 UUID 순이다.

페이지 응답은 `{"items":[],"total":0,"page":0,"size":20}` 형식이다. `page`는 0부터 시작하며 최대 100000이다. `size`는 기본 20이며 1부터 100까지 허용한다.

허용하는 상태 변경:

| 현재 상태 | 새 상태 |
| --- | --- |
| `PENDING` | `ACTIVE` 또는 `REJECTED` |
| `ACTIVE` | `DISABLED` |
| `REJECTED` | `ACTIVE` |
| `DISABLED` | `ACTIVE` |

현재 값과 같은 변경은 대상 값을 그대로 반환한다. 역할은 활성 회원에게만 변경할 수 있다. 관리자는 자신의 역할과 상태를 변경할 수 없다. 변경 트랜잭션은 공통 DB 잠금을 먼저 얻은 뒤 실행자의 현재 권한을 다시 검사한다. 두 관리자가 동시에 서로를 비활성화하거나 권한을 내리더라도 마지막 활성 관리자를 유지한다. 변경과 대상 세션 폐기 및 감사 기록은 같은 트랜잭션으로 처리한다. 공개 재가입과 재신청 및 회원 물리 삭제 API는 제공하지 않는다.

## 접근 이력

`GET /api/v1/admin/access-logs`는 `Page<AccessLog>`를 반환한다. 선택 쿼리는 `user_id`와 `event` 및 `from`과 `to` 및 `success`와 `page` 및 `size`다.

- `user_id`는 UUID다. `event`는 `LOGIN_SUCCESS` 또는 `LOGIN_FAILURE` 또는 `LOGOUT`이다.
- `from`과 `to`는 ISO 8601 시각이다. `from`은 포함하고 `to`는 제외한다. 둘 다 지정하면 `from < to`여야 한다.
- `success=true`는 로그인 성공과 로그아웃을 포함한다. `success=false`는 로그인 실패만 포함한다. 다른 필터와 함께 지정하면 모두 만족해야 한다.
- 기본 조회 범위는 최근 90일이다. 그보다 오래된 행은 조회에서 제외한다. 시각 내림차순 다음 UUID 순으로 반환한다.
- 일반 조회와 채팅 요청은 접근 이력에 추가하지 않는다. 인증 필드 검증이나 CSRF 검증에서 거절된 요청도 로그인 시도로 기록하지 않는다.

```json
{
  "id": "00000000-0000-0000-0000-000000000002",
  "user_id": null,
  "event": "LOGIN_FAILURE",
  "occurred_at": "2026-10-21T00:00:00Z",
  "ip": "127.0.0.1",
  "user_agent": "ExampleBrowser"
}
```

미등록 이메일의 로그인 실패는 `user_id=null`이다. 입력 이메일과 비밀번호 및 토큰과 질의 본문은 저장하지 않는다. IP는 요청의 직접 연결 주소이며 임의의 전달 헤더를 신뢰하지 않는다. User-Agent는 최대 512자로 저장한다. 보존 기간이 지난 접근 이력은 서버 시작 1분 후부터 24시간 간격으로 정리한다. 회원 변경 감사와 회원 데이터 및 채팅은 이 정리 대상이 아니다.

## 오류

공통 오류 형식은 `{"request_id":"UUID","error":{"code":"...","message":"...","retryable":false}}`다. 요청 ID가 아직 부여되지 않은 입력 검증 오류에서는 `request_id`가 `null`일 수 있다.

| HTTP | 코드 | 조건 |
| --- | --- | --- |
| 400 | `VALIDATION_ERROR` | 입력 형식 또는 허용값 오류 |
| 401 | `UNAUTHENTICATED` | 로그인 필요 또는 폐기된 세션 |
| 401 | `INVALID_CREDENTIALS` | 로그인 실패 |
| 403 | `CSRF_INVALID` | CSRF 검증 실패 |
| 403 | `FORBIDDEN` | 현재 회원의 권한 부족 |
| 404 | `MEMBER_NOT_FOUND` | 회원이 없음 |
| 409 | `EMAIL_ALREADY_REGISTERED` | 이메일 중복 |
| 409 | `SELF_CHANGE_FORBIDDEN` | 관리자가 자기 역할 또는 상태 변경 |
| 409 | `INVALID_STATUS_TRANSITION` | 허용하지 않는 상태 변경 |
| 409 | `MEMBER_NOT_ACTIVE` | 활성 회원이 아닌 대상의 역할 변경 |
| 409 | `LAST_ADMIN_REQUIRED` | 마지막 활성 관리자 제거 |
| 503 | `AUTH_UNAVAILABLE` | 요청의 세션 권한을 DB에서 확인할 수 없음 |

## 최초 관리자 생성

공개 관리자 생성 API는 없다. Java 21과 Maven 및 별도 애플리케이션 PostgreSQL DB를 준비한다. `APP_DATABASE_URL`과 `APP_DATABASE_USER` 및 `APP_DATABASE_PASSWORD`를 운영 환경의 비밀 설정으로 전달한다. 비즈니스 데이터 DB와 분리한 `negopilot_app` DB를 사용한다. Flyway가 회원 및 세션 테이블을 준비한다.

저장소의 `backend` 폴더에서 실행한다.

```sh
mvn -B -ntp -DskipTests compile dependency:build-classpath \
  -DincludeScope=runtime -Dmdep.outputFile=target/runtime-classpath.txt

java -cp "target/classes:$(cat target/runtime-classpath.txt)" \
  com.negopilot.backend.AuthBootstrapAdmin \
  --email=admin@example.com --name='초기 관리자' --department='운영' \
  --job_title='관리자'
```

비밀번호는 터미널에서 표시하지 않고 입력받는다. 터미널이 없는 자동화에서는 비밀 저장소가 `ADMIN_BOOTSTRAP_PASSWORD` 환경변수를 전달해야 한다. 비밀번호를 CLI 인자로 받지 않는다. 계정 입력 인자는 위 네 필드만 허용하며 `job_title`은 생략할 수 있다.

CLI는 `bootstrap-admin` 프로필과 `WebApplicationType.NONE`으로 실행한다. HTTP 포트를 열지 않고 처리 후 종료한다. 접근 이력 정리 작업도 실행하지 않는다. 활성 관리자가 이미 있거나 이메일이 등록되어 있으면 실패하며 기존 계정을 덮어쓰지 않는다. 동시 실행은 DB 잠금으로 직렬화한다. 생성된 회원은 `ADMIN/ACTIVE`이며 `BOOTSTRAP` 감사 기록을 남긴다.

로컬 Compose의 일회성 초기화 서비스는 `--if-absent` 옵션을 명시한다. 이 옵션에서는 활성 관리자가 이미 있으면 생성 작업을 생략하고 성공 종료한다. 기존 계정과 비밀번호 및 감사 기록은 변경하지 않는다. 지정한 이메일이 등록되어 있지만 해당 계정이 `ADMIN/ACTIVE`가 아니면 다른 활성 관리자가 있어도 실패한다. 기존 회원을 자동 승격하거나 다시 활성화하지 않는다. 정상 웹 서버 시작에는 관리자 생성 절차가 없다.

패키징된 Spring Boot JAR에서도 같은 CLI를 실행할 수 있다. 아래 명령은 Compose 초기화용 반복 실행 옵션을 포함한다. 비밀번호와 DB 환경변수는 실행 환경에서 전달한다.

```sh
java -Dloader.main=com.negopilot.backend.AuthBootstrapAdmin \
  -cp backend/target/negopilot-backend-0.1.0-SNAPSHOT.jar \
  org.springframework.boot.loader.launch.PropertiesLauncher \
  --if-absent --email=admin@negopilot.local --name='로컬 관리자' \
  --department='운영' --job_title='관리자'
```

`deploy/local/backend.Dockerfile`의 이미지 안에서는 JAR 경로가 `/app/app.jar`다. 이미지의 진입점은 `java`이므로 Compose `command`에는 `-Dloader.main=...`부터 지정한다. 일반 백엔드는 기본 명령인 `-jar /app/app.jar`로 실행한다.

## 검증

`AuthMembershipPostgresTest`는 임시 PostgreSQL을 지정하면 실제 HTTP와 JDBC 세션으로 수행한다. 테스트 전용 DB만 지정해야 한다. 이 클래스는 테스트마다 회원과 세션 및 관련 데이터를 초기화한다.

```sh
TEST_DATABASE_URL='jdbc:postgresql://127.0.0.1:5432/auth_test' \
TEST_DATABASE_USER='test_user' \
TEST_DATABASE_PASSWORD="$AUTH_TEST_DATABASE_PASSWORD" \
mvn -f backend/pom.xml -B -ntp -Dtest=AuthMembershipPostgresTest test
```

가입과 해시 저장 및 권한별 접근과 제한 세션 승인 및 로그인 세션 회전과 이전 CSRF 토큰 거절을 검증한다. 모든 대상 세션 폐기와 상태 재확인 및 접근 이력 보존 기간과 관리자 동시 변경 및 최초 관리자 동시 생성도 검증한다. 반복 초기화의 기존 계정 보존과 이메일 충돌 거절 및 동시 실행도 검증한다. `AuthBootstrapAdminTest`는 별도 DB 없이 CLI 옵션의 명시적 선택과 허용하지 않는 인자 거절을 검증한다.
