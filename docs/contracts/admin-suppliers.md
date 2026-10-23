# 관리자 협력사 관리 계약

모든 경로는 `/api/admin/suppliers`로 시작한다. 활성 ADMIN 세션과 변경 요청의 CSRF 토큰이 필요하다. 회원 식별자는 세션에서 얻는다. 요청에서 수정자 ID나 권한을 받지 않는다.

이 API는 기존 supplier DB를 관리한다. 회원과 세션 및 개인 대화가 저장되는 application DB와 분리한다. 재무와 계약은 조회만 제공한다. 견적은 추가만 할 수 있다. 삭제 API는 없으며 비활성화로 조회 대상에서 제외한다.

## 조회

`GET /api/admin/suppliers?page=0&size=20&q=기업&source=MANUAL&status=ACTIVE`

page는 0부터 1000000까지다. size는 1부터 100까지이며 기본값은 20이다. q는 선택 문자열이며 앞뒤 공백을 제거한 뒤 1부터 100자까지 허용한다. 기업명과 ID의 부분 일치로 검색한다. `%`와 `_`는 문자 그대로 검색한다. source는 DART 또는 MOCK 또는 MANUAL이다. status는 ACTIVE 또는 INACTIVE다. 미지정 시 모든 출처와 상태를 포함한다. 알 수 없는 parameter와 중복 parameter는 400이다. ID 순으로 정렬한다.

```json
{
  "items": [{
    "supplier_id": "SUP-0151", "name": "수동기업", "region": "부산",
    "source": "MANUAL", "status": "ACTIVE", "version": 0,
    "iatf16949": true, "iso14001": false,
    "certification_source": "MANUAL", "capability_source": "MANUAL", "quality_source": "MANUAL",
    "defect_ppm": null, "on_time_rate": null, "capability_count": 1,
    "updated_at": "2026-10-21T00:00:00Z"
  }],
  "total": 1, "page": 0, "size": 20
}
```

`GET /api/admin/suppliers/{supplier_id}`는 아래 구조를 반환한다. 상세와 쓰기 응답은 동일한 구조다. 관리자는 비활성 기업도 조회할 수 있다. 없는 ID는 404다.

```json
{
  "supplier": {
    "supplier_id": "SUP-0151", "source": "MANUAL", "name": "수동기업", "region": "부산",
    "corp_code": null, "stock_code": null, "induty_code": null,
    "employees": 12, "revenue_eok": null, "founded_year": 2020, "biz_no_masked": "123-45-*****",
    "iatf16949": true, "iso14001": false, "status": "ACTIVE", "version": 0,
    "profile_origin_source": "MANUAL", "certification_source": "MANUAL",
    "capability_source": "MANUAL", "quality_source": "MANUAL",
    "profile_original": {}, "created_at": "2026-10-21T00:00:00Z", "updated_at": "2026-10-21T00:00:00Z"
  },
  "capabilities": [{"process": "프레스", "material": "SPCC", "monthly_capacity": 1000}],
  "quality": null, "financials": [], "contract": null,
  "quotes": [{"quote_id": 412, "process": "프레스", "material": "SPCC", "unit_price": 1200, "quoted_at": "2026-01-02", "source": "MANUAL"}],
  "audit": []
}
```

예시의 profile_original과 audit는 구조를 줄여 표시했다. profile_original은 관리 도입 시점의 원본을 보존한다. audit는 최신 100건을 event_id 역순으로 반환한다. 각 이력에는 event_id와 actor_user_id 및 action과 before_data 및 after_data와 created_at이 있다. 이력은 DB에 계속 보존한다. quotes는 quoted_at과 quote_id의 역순이다. 견적의 source는 MOCK(기존 합성 자료) 또는 MANUAL(관리자 추가)이다. 금액은 원래 DB 단위를 유지한다. revenue_eok만 억 원 단위다. quality.on_time_rate와 contract.raw_material_ratio는 0부터 1까지의 비율이다.

## 수동 등록

`POST /api/admin/suppliers`는 201과 상세를 반환한다.

```json
{
  "profile": {
    "name": "수동기업", "region": "부산", "employees": 12,
    "founded_year": 2020, "biz_no_masked": "123-45-*****",
    "revenue_eok": null, "stock_code": null, "induty_code": "30332",
    "source": "MANUAL", "corp_code": null
  },
  "certifications": {"iatf16949": true, "iso14001": false},
  "capabilities": [{"process": "프레스", "material": "SPCC", "monthly_capacity": 1000}],
  "quality": {"defect_ppm": 10, "on_time_rate": 0.975, "audit_grade": "A", "years_dealing": 0}
}
```

quality는 생략하거나 null로 둘 수 있다. 다른 세 영역은 필수다. profile의 name과 region 및 founded_year와 biz_no_masked는 필수다. 나머지 profile 필드는 생략하거나 null로 둘 수 있다.

ID는 요청하지 않는다. 서버가 `SUP-숫자` ID를 시퀀스로 생성하며 4자리를 넘어가도 잘리지 않는다.

profile.source는 기본 정보의 출처이며 DART 또는 MOCK 또는 MANUAL이다. 생략하거나 null이면 MANUAL이다. DART는 DART 고유번호 corp_code(숫자 8자리)가 필수이고, DART가 아니면 corp_code를 보낼 수 없다(생략 또는 null). 이미 다른 기업이 쓰는 corp_code는 409 DATA_CONFLICT다. 요청한 source는 source와 profile_origin_source에 함께 저장한다. 그래서 DART로 등록한 기업의 profile은 이후 수정할 수 없다. 인증과 생산능력 및 입력한 품질의 출처는 MANUAL이다. source와 corp_code는 등록할 때만 받으며 PATCH로 바꿀 수 없다.

재무와 계약 및 견적은 이 요청에서 거절한다. 견적은 등록 후 견적 추가 API로 넣는다. 현재 프런트 폼의 전체 DB 행 payload를 그대로 보내지 않고 이 세 영역과 선택 quality만 보낸다.

## 수정

모든 수정에는 조회한 version이 필요하다. 성공하면 version이 1 증가한다. 동시에 수정했거나 오래된 version이면 409 VERSION_CONFLICT다. 화면은 최신 상세를 다시 조회한 뒤 사용자의 입력을 확인한다.

`PATCH /api/admin/suppliers/{id}`

```json
{"version": 0, "profile": {"employees": 20}, "certifications": {"iso14001": true}}
```

profile과 certifications 중 하나 이상이 필요하다. 각 영역은 부분 수정이며 빈 객체는 거절한다. DART 원본 기업의 profile 요청은 403 READ_ONLY_SOURCE다. 인증은 DART 기업도 수정할 수 있다. MOCK 기본 정보를 수정하면 source는 MANUAL로 바뀌며 profile_origin_source와 profile_original은 유지된다. 인증만 변경하면 기본 source는 그대로 두고 certification_source만 MANUAL로 바꾼다.

`PUT /api/admin/suppliers/{id}/capabilities`

```json
{"version": 1, "capabilities": [{"process": "프레스", "material": "SPCC", "monthly_capacity": 2000}]}
```

기존 capability 전체를 한 트랜잭션에서 교체한다. capability_source는 MANUAL이다. 최소 1개부터 최대 100개까지 허용하며 같은 공정과 소재의 조합은 중복할 수 없다.

`PUT /api/admin/suppliers/{id}/quality`

```json
{"version": 2, "quality": {"defect_ppm": 0, "on_time_rate": 0.934, "audit_grade": "B", "years_dealing": 0}}
```

품질 네 필드 전체가 필수다. 기존 행이 없으면 추가한다. quality_source는 MANUAL이다.

`POST /api/admin/suppliers/{id}/quotes`는 201과 상세를 반환한다.

```json
{"version": 3, "quotes": [{"process": "프레스", "material": "SPCC", "unit_price": 1200, "quoted_at": "2026-01-02"}]}
```

견적은 이력이라 기존 견적을 바꾸거나 지우지 않고 한 트랜잭션에서 덧붙인다. 한 번에 1개부터 100개까지 받는다. 추가한 견적의 source는 MANUAL이다. 견적이 없는 협력사는 추천 순위에서 "품질 또는 견적 자료 미등록"으로 제외되므로, 등록한 협력사를 순위에 넣으려면 견적을 추가한다. 생산능력에 없는 공정과 소재의 견적도 받는다. 다만 순위는 생산능력과 같은 공정·소재의 견적만 쓴다.

`PATCH /api/admin/suppliers/{id}/status`

```json
{"version": 3, "status": "INACTIVE"}
```

ACTIVE로 복구할 수 있다. 하위 자료나 과거 채팅에 저장한 근거를 삭제하지 않는다. 비활성 기업은 MCP 검색과 이름 조회 및 상세와 추천 순위 및 협상안 검사 대상에서 제외한다. 소재 카탈로그와 지역 및 출처별 기업 수도 활성 기업만 집계한다. 협상안의 시장 견적 중앙값에서도 비활성 기업을 제외한다.

## 관리자 데이터 요약

```http
GET /api/admin/data/summary
```

이 경로도 활성 관리자만 사용할 수 있다. 쿼리 파라미터는 받지 않는다. 협력사 데이터의 연결 상태와 출처별·상태별 건수를 반환한다. 관리자 법령 요약은 #230에서 제거했다.

```ts
type SourceSummary =
  | { status: "AVAILABLE"; data: { total: number; by_source: Record<"DART" | "MOCK" | "MANUAL", number>; by_status: Record<"ACTIVE" | "INACTIVE", number> } }
  | { status: "UNAVAILABLE"; data: null; error_code: string }

type DataSummary = {
  checked_at: string
  suppliers: SourceSummary
}
```

UNAVAILABLE은 0건을 뜻하지 않는다. 화면은 `data: null`을 0건으로 변환하지 않아야 한다. `checked_at`은 이번 연결 상태 확인 시각이다.

## 입력 제한과 오류

- 기본 문자열은 공백 제거 후 1부터 100자까지다. 제어 문자는 받지 않는다.
- 지역은 현재 17개 시도 약칭을 사용한다. 공정은 다이캐스팅과 프레스 및 사출과 단조 및 CNC가공이다.
- employees는 1 이상의 정수다. revenue_eok는 0 이상의 정수다. 둘 다 PostgreSQL INT 범위다.
- founded_year는 1900부터 현재 연도까지다. stock_code는 숫자 6자리다. induty_code는 숫자 3부터 5자리다.
- biz_no_masked는 `123-45-*****` 형태만 받는다. 사업자번호 원문을 서버에 전송하지 않는다.
- monthly_capacity는 정수 1부터 10000000까지다. defect_ppm은 0 이상의 INT다. years_dealing은 정수 0부터 1000까지다.
- unit_price는 정수 1부터 INT 최댓값까지이며 원 단위다. quoted_at은 `YYYY-MM-DD` 형식의 실제 날짜이며 오늘 이후는 받지 않는다. 견적의 process는 위 다섯 공정 중 하나다.
- audit_grade는 A 또는 B 또는 C 또는 D다. on_time_rate는 0부터 1까지이며 소수점 셋째 자리까지 허용한다. 화면의 백분율을 100으로 나눠 전송한다. 정밀도가 넘으면 임의 반올림하지 않고 400을 반환한다.
- 알 수 없는 본문 필드와 잘못된 자료형은 400이다. 인증 실패는 401이다. 권한 부족과 DART 원본 수정은 403이다. 없는 기업은 404다. 버전 충돌과 제약 충돌은 409다. business DB 미설정과 연결 장애는 안전한 503 BUSINESS_DATA_UNAVAILABLE이다.

## DB 적용과 실행 순서

Spring의 application DataSource와 별도로 BusinessDataStore가 내부 Hikari pool과 JdbcTemplate 및 TransactionTemplate을 소유한다. 추가 DataSource 빈을 등록하지 않는다. 설정은 BUSINESS_DATABASE_ENABLED와 BUSINESS_DATABASE_URL 및 BUSINESS_DATABASE_USER와 BUSINESS_DATABASE_PASSWORD다. URL은 JDBC 형식이다.

활성화한 Spring은 기존 8개 business 테이블을 먼저 확인한다. 빈 DB에는 자동 seed를 하지 않는다. 기존 스키마를 baseline 0으로 기록한 뒤 별도 business_schema_history를 사용하는 V1(관리 필드·감사 이력)과 V2(협력사 담당자) 및 V3(견적 source) migration을 적용한다. 기존 application Flyway history와 분리한다.

Spring 없이 MCP만 준비할 때도 같은 SQL을 사용할 수 있다. 저장소의 business-mcp 디렉터리에서 실행한다. DATABASE_URL은 관리 스키마 변경 권한이 있는 전용 접속 환경 변수로 먼저 지정한다. 실제 비밀번호는 셸 명령 이력이나 저장소에 쓰지 않는다.

```bash
uv run python scripts/migrate_management.py
uv run python server.py --http 9102
```

아직 business 테이블이 없는 새 실습 DB에만 기존 `scripts/seed.py`를 명시적으로 실행한 뒤 위 migration을 실행한다. seed는 기존 자료를 삭제하므로 관리자 수정이 있는 DB에 다시 실행하지 않는다. migration 자체는 원본과 수정 버전 및 감사 이력을 보존하며 다시 실행할 수 있다. 독립 migration 이후 Spring을 시작해도 동일한 V1을 안전하게 적용하고 Flyway history에 기록한다.

먼저 DB migration을 마친 다음 새 MCP를 시작한다. MCP 실행에는 별도의 SELECT 전용 계정을 사용한다. 회원 DB나 변경 이력에 접근 권한을 주지 않는다. 권한 예시는 business-mcp README에 있다.

## 검증

`AdminSupplierPostgresTest`는 명시한 business_test DB 안에 고유 schema를 만들고 지운다. TEST_DATABASE_URL과 TEST_DATABASE_USER 및 TEST_DATABASE_PASSWORD가 필요하다. 기존 공개 스키마나 다른 DB를 초기화하지 않는다. 환경이 없으면 이 검사는 건너뛴다.

```bash
cd backend
mvn -Dtest=AdminSupplierPostgresTest,AdminSupplierValidatorTest test
```

business MCP 테스트는 고유 schema에 기존 seed와 같은 V1을 적용한다. 저장소 CSV를 덮어쓰지 않는다. 실제 모델은 호출하지 않는다.

```bash
cd mcp-servers/business-mcp
uv run pytest tests offline_tests -q
```
