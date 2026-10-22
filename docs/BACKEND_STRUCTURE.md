# 백엔드 구조

Java 소스의 시작 패키지는 `com.negopilot.backend`다. HTTP 경로와 요청 검증은 각 기능의 컨트롤러에서 확인한다. 업무 규칙과 트랜잭션은 서비스에서 다룬다. SQL은 기능별 저장소에 모았다. 외부 Python 호출의 공통 처리는 `common.http`가 담당한다.

## 변경할 기능 찾기

| 패키지 | 담당 기능과 시작 파일 |
| --- | --- |
| `auth` | 로그인과 세션 및 회원 승인. [AuthController](../backend/src/main/java/com/negopilot/backend/auth/AuthController.java)와 [MembershipController](../backend/src/main/java/com/negopilot/backend/auth/MembershipController.java) |
| `chat` | DB에 저장되는 대화와 메시지. [PersistentConversationController](../backend/src/main/java/com/negopilot/backend/chat/PersistentConversationController.java) |
| `agent` | v1 질의와 v2 답변 생성의 요청 및 응답 계약. [AgentController](../backend/src/main/java/com/negopilot/backend/agent/AgentController.java)와 [GenerationGateway](../backend/src/main/java/com/negopilot/backend/agent/GenerationGateway.java) |
| `supplier` | 실제 공급사 조회. [SupplierLookupController](../backend/src/main/java/com/negopilot/backend/supplier/SupplierLookupController.java) |
| `admin.supplier` | 공급사 관리. [AdminSupplierController](../backend/src/main/java/com/negopilot/backend/admin/supplier/AdminSupplierController.java) |
| `admin.law` | 법령 조회와 갱신 작업. [AdminLawController](../backend/src/main/java/com/negopilot/backend/admin/law/AdminLawController.java) |
| `admin.data` | 공급사와 법령의 상태 요약. [AdminDataController](../backend/src/main/java/com/negopilot/backend/admin/data/AdminDataController.java) |
| `common.api` | 공통 예외와 JSON 오류 응답. [ApiException](../backend/src/main/java/com/negopilot/backend/common/api/ApiException.java)과 [ApiErrorHandler](../backend/src/main/java/com/negopilot/backend/common/api/ApiErrorHandler.java) |
| `common.http` | 내부 API의 HTTP 전송. [AgentHttpTransport](../backend/src/main/java/com/negopilot/backend/common/http/AgentHttpTransport.java) |
| `legacy` | `legacy-test`에서 사용하는 메모리 대화. [GenerationController](../backend/src/main/java/com/negopilot/backend/legacy/GenerationController.java)와 [SupplierChatController](../backend/src/main/java/com/negopilot/backend/legacy/SupplierChatController.java) |

v2 직접 질의 경로인 `/api/v2/agent/queries`는 일반 실행에서 `PersistentConversationController`가 받는다. 이 경로는 회원 권한을 확인한 뒤 `GenerationGateway`로 전달하며 대화 이력을 저장하지 않는다. 대화에 메시지를 추가하는 경로는 대화 서비스를 거친다.

## 인증과 회원 관리

[SecurityConfiguration](../backend/src/main/java/com/negopilot/backend/auth/SecurityConfiguration.java)이 URL 권한과 CSRF 및 필터를 구성한다. [SecurityUserRefreshFilter](../backend/src/main/java/com/negopilot/backend/auth/SecurityUserRefreshFilter.java)는 세션의 회원 상태를 DB와 비교한다. [CurrentUser](../backend/src/main/java/com/negopilot/backend/auth/CurrentUser.java)는 기능 코드에서 로그인 상태와 ACTIVE 회원 및 관리자 권한을 확인하는 진입점이다.

[MembershipPolicy](../backend/src/main/java/com/negopilot/backend/auth/MembershipPolicy.java)는 로그인 허용 상태와 활성 관리자 판정 및 상태 전이 규칙을 공유한다. 요청 본문은 `AuthRequestValidator`가 검사한다. 회원 목록과 접근 기록의 검색 조건 및 페이지와 기간은 `MembershipQueryValidator`가 검사한다. 공개 회원 JSON은 `MemberView`와 `UserResponse`가 정의한다. 인증 결과에 비밀번호 해시를 전달하지 않는다.

`AuthService`는 가입과 비밀번호 확인 및 접근 기록을 담당한다. `AuthSessionService`는 로그인 시 세션 ID 교체와 보안 컨텍스트 저장 및 세션 폐기를 담당한다. `MembershipService`는 변경자와 대상 회원을 다시 확인한 뒤 역할과 상태를 바꾼다. 마지막 활성 관리자를 유지하는 검사와 세션 폐기 및 감사 저장은 같은 회원 변경 트랜잭션 안에서 수행한다. SQL과 회원 조회 결과의 매핑은 [MembershipRepository](../backend/src/main/java/com/negopilot/backend/auth/MembershipRepository.java)에 있다.

## 대화 저장과 외부 호출 순서

[PersistentConversationService](../backend/src/main/java/com/negopilot/backend/chat/PersistentConversationService.java)가 트랜잭션의 경계를 정한다. [JdbcConversationRepository](../backend/src/main/java/com/negopilot/backend/chat/JdbcConversationRepository.java)는 SQL과 행 매핑 및 JSONB 변환을 담당하며 별도 트랜잭션을 시작하지 않는다.

메시지를 보낼 때의 순서는 다음과 같다.

1. `claim` 트랜잭션에서 회원 상태와 대화 소유자를 확인한다. 멱등 키의 본문 해시를 비교하고 저장된 응답이 있으면 반환한다. 그다음 실행 중 여부를 확인하고 lease를 확보한다.
2. 선점 트랜잭션이 커밋된 뒤 `GenerationGateway`를 호출한다. Python 응답을 기다리는 동안 DB 트랜잭션을 유지하지 않는다.
3. `finish` 트랜잭션에서 회원 상태와 lease의 토큰 및 유효 시간을 다시 확인한다. `COMPLETED`와 `NEEDS_INPUT` 응답의 사용자 메시지와 답변 및 대화 문맥과 제목 및 캐시 응답을 함께 저장한다.

실행 상태가 `FAILED`이면 메시지를 저장하지 않고 lease를 해제한다. 예외가 발생하면 소유자와 토큰이 일치하는 lease만 해제한다. 대화 목록의 커서와 메시지 순서 및 이전 대화의 구성은 서비스에서 확인할 수 있다.

## 관리자 데이터와 DB 구분

[AdminSupplierService](../backend/src/main/java/com/negopilot/backend/admin/supplier/AdminSupplierService.java)는 DART 원본 보호와 수정 버전 비교 및 오류 변환을 담당한다. [AdminSupplierRepository](../backend/src/main/java/com/negopilot/backend/admin/supplier/AdminSupplierRepository.java)는 SQL과 상세 조회 및 감사 저장을 담당한다. 두 클래스는 같은 [BusinessDataStore](../backend/src/main/java/com/negopilot/backend/admin/supplier/BusinessDataStore.java)를 사용한다. 행 잠금부터 변경과 버전 증가 및 변경 전후 감사 저장까지 하나의 business 트랜잭션으로 처리한다.

`BusinessDataStore`는 회원과 대화에 쓰는 기본 datasource와 별도의 Hikari 풀을 관리한다. `business.datasource.enabled`가 꺼져 있으면 풀을 만들지 않는다. 관리 기능을 호출하면 기존 `BUSINESS_DATA_UNAVAILABLE` 오류를 반환한다. 활성화할 때는 기존 업무 테이블과 컬럼을 확인한 뒤 business migration을 적용한다. seed 데이터를 자동 생성하지 않는다.

법령 조회와 갱신은 `AdminLawGateway`를 거쳐 Python 내부 API를 호출한다. `AdminDataController`는 공급사 요약과 법령 요약을 차례로 조회한다. 한 원천의 `ApiException`은 해당 원천의 `UNAVAILABLE`과 오류 코드로 표시하며 다른 원천의 조회를 계속한다.

기본 DB migration은 [db/migration](../backend/src/main/resources/db/migration)에 있다. business migration은 [db/business](../backend/src/main/resources/db/business)에 있다. 두 migration 이력을 구분해 확인한다.

## HTTP 전송과 공급사 조회

[AgentHttpConfiguration](../backend/src/main/java/com/negopilot/backend/agent/AgentHttpConfiguration.java)은 엄격한 JSON 파서와 Apache HTTP client 및 `RestClient`를 만든다. 자동 재시도와 redirect 및 cookie 관리를 끈 설정을 공유한다. 연결 주소와 내부 API 키 및 시간 제한은 `AgentProperties`에서 읽는다.

`AgentHttpTransport`는 설정된 내부 키와 요청 본문을 전송한다. 허용된 상태 코드와 JSON 미디어 타입을 확인하고 응답 본문을 읽는다. 연결 실패는 503으로 변환한다. 시간 초과는 504로 변환한다. 잘못된 응답은 502로 변환한다.

각 Gateway는 성공 응답의 검증과 해당 API의 오류 계약을 유지한다. `request_id`의 null 허용 범위와 상태 코드 및 `retryable` 규칙은 Gateway마다 다르다. [AgentResponseSanitizer](../backend/src/main/java/com/negopilot/backend/agent/AgentResponseSanitizer.java)는 검증된 응답을 복사한 뒤 내부 진단 문구를 공개용 문구로 바꾼다. v1과 v2의 오류 문구 차이는 호출 인자로 전달한다.

실제 공급사 조회는 `SupplierLookupController`에서 [SupplierLookupGateway](../backend/src/main/java/com/negopilot/backend/supplier/SupplierLookupGateway.java)를 거쳐 `/internal/v2`로 전달된다. 응답은 `SupplierLookupResponseValidator`가 검사한다. [SupplierGatewayClient](../backend/src/main/java/com/negopilot/backend/supplier/SupplierGatewayClient.java)는 공급사 URI 인코딩과 오류 매핑을 공유한다.

`SupplierChatGateway`와 `SupplierChatConfiguration`은 `legacy-test`에서만 생성된다. 이 설정이 로드하는 `fixtures/supplier-chat.json`은 레거시 응답 검증에 사용한다. 일반 실행의 실제 공급사 조회는 fixture 빈에 의존하지 않는다.

## 레거시 프로필과 호환 진입점

`legacy`의 두 컨트롤러와 두 서비스는 기존 메모리 대화 계약을 테스트하기 위해 남겼다. [application-legacy-test.properties](../backend/src/main/resources/application-legacy-test.properties)는 DB와 JDBC 세션 자동 구성을 끈다. 이 프로필의 보안 체인은 요청을 허용하며 CSRF를 끈다. 운영 실행에서는 `legacy-test`를 활성화하지 않는다.

[UserIdentity](../backend/src/main/java/com/negopilot/backend/UserIdentity.java)는 루트의 `com.negopilot.backend.UserIdentity`를 유지한다. Spring Session JDBC가 저장한 principal을 다시 읽을 수 있도록 클래스 이름과 직렬화 형태를 보존한다.

루트의 [AuthBootstrapAdmin](../backend/src/main/java/com/negopilot/backend/AuthBootstrapAdmin.java)과 [AuthBootstrapLocalUser](../backend/src/main/java/com/negopilot/backend/AuthBootstrapLocalUser.java)는 기존 Compose의 실행 클래스 이름을 지원한다. 실제 구현은 `auth` 패키지에 있으며 루트 클래스는 전달받은 인자를 그대로 위임한다. 이 클래스들은 호환 실행 진입점이므로 제거하지 않는다.

## 테스트 위치

[테스트 소스](../backend/src/test/java/com/negopilot/backend)는 기능 패키지와 같은 구조다. HTTP 계약은 각 Gateway의 HTTP 테스트에서 확인한다. 프로필별 공급사 의존성은 `SupplierGatewayProfileTest`에서 확인한다. 회원과 대화 및 공급사 변경의 트랜잭션은 PostgreSQL 테스트에서 확인한다.

DB 테스트에는 전용 임시 DB의 `TEST_DATABASE_URL`과 `TEST_DATABASE_USER` 및 `TEST_DATABASE_PASSWORD`를 설정한다. 운영 DB나 공유 서비스 DB를 사용하지 않는다. [CI](../.github/workflows/ci.yml)는 필수 PostgreSQL suite가 실제로 실행됐는지 확인하며 테스트 건너뜀을 성공으로 취급하지 않는다.

이번 리팩터링의 실행 명령과 검증 결과는 [검증 기록](verification/backend-refactor.md)에 있다.
