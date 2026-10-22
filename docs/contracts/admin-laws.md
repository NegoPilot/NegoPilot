# 관리자 법령 조회와 최신화 API

공개 경로는 Spring의 `/api/admin/laws`다. Python 내부 경로는 `/internal/admin/laws`다. 공개 API는 로그인한 `ACTIVE` 관리자만 사용할 수 있다. POST에는 로그인 세션과 CSRF 헤더가 필요하다. 내부 API는 서버에 설정된 `X-Internal-Api-Key`를 사용한다.

Spring은 내부 응답의 `{"schema_version":"1.0","data":...}`를 검증한 뒤 `data`만 공개 응답으로 반환한다. 클라이언트의 인증 헤더와 쿠키를 Python으로 전달하지 않는다.

## 경로

아래 표의 경로 앞에 `/api/admin/laws`를 붙인다. 내부 API는 같은 경로 구조에 `/internal/admin/laws`를 사용한다.

| 메서드와 하위 경로 | 성공 상태 | 의미 |
| --- | --- | --- |
| `GET /` | 200 | 현재 적재된 데이터셋과 법령 목록 |
| `GET /{law_key}/versions` | 200 | 보존된 판 목록 |
| `GET /{law_key}/versions/{version_id}/articles` | 200 | 해당 판의 조문 목록 |
| `GET /{law_key}/versions/{version_id}/articles/{article_no}` | 200 | 해당 판의 조문 원문과 출처 |
| `GET /refresh-jobs` | 200 | 최근 최신화 작업 또는 `item: null` |
| `POST /refresh-jobs` | 202 | 새 최신화 작업 접수 |
| `GET /refresh-jobs/{job_id}` | 200 | 지정 작업의 현재 상태 |

법령과 버전 및 조문 조회는 외부 수집을 시작하지 않는다. 최신화 POST만 기존 수집기와 파서를 실행한다. Agent의 일반 법령 도구에 최신화 실행 권한을 추가하지 않는다.

## 요청 검증

- `law_key`는 `^[a-z_]{1,80}$`를 따른다.
- `version_id`는 `YYYY-MM-DD_<40자리 소문자 commit SHA>`다. 조회 응답에서 받은 ID를 그대로 사용한다.
- `article_no`는 `제16조의2`와 `16조의2` 또는 `16` 같은 조문 번호다. URL 경로에서는 한 번만 인코딩한다.
- `job_id`는 하이픈이 포함된 표준 UUID다. 대문자 16진수도 허용하며 내부 요청에서는 소문자로 정규화한다.
- 조문 목록만 `offset`과 `limit`을 받는다. offset 기본값은 0이며 0..100000이다. limit 기본값은 50이며 1..100이다.
- 정의되지 않은 쿼리 파라미터와 중복 파라미터를 거절한다.
- 최신화 POST는 본문이 없어야 한다. `{}`와 `null` 및 공백 본문도 400이다.

## 법령 목록

```http
GET /api/admin/laws
```

공개 응답 형식:

```ts
type LawCatalog = {
  dataset_id: string
  loaded_at: string
  collected_at: string | null
  law_count: number
  version_count: number
  article_count: number
  items: {
    law_key: string
    law_name: string
    version_count: number
    article_count: number
  }[]
}
```

`dataset_id`는 현재 제공 중인 데이터셋의 식별자다. `loaded_at`은 서버에 적재된 시각이다. `collected_at`은 확인할 수 있는 최신화 수집 시각이다. 기본 파일만 적재한 경우 수집 시각을 추정하지 않고 `null`로 표시한다. 시간은 UTC ISO 8601 문자열이며 소수 초가 포함될 수 있다.

각 법령의 `article_count`는 보존된 모든 판의 조문 수를 합한 값이다. 최상위 개수도 같은 집계 규칙을 사용한다. 비어 있는 정상 목록은 `items: []`와 0인 개수를 반환한다. 연결 실패를 빈 목록으로 바꾸지 않는다.

## 버전과 조문

```ts
type VersionSummary = {
  version_id: string
  effective_date: string
  promulgation_date: string
  promulgation_no: string
  source_url: string
  source_commit: string
  article_count: number
}

type LawVersions = {
  law_key: string
  law_name: string
  items: VersionSummary[]
}

type ArticleSummary = {
  no: string
  title: string
  deleted: boolean
}

type ArticlePage = VersionSummary & {
  law_key: string
  total: number
  offset: number
  limit: number
  items: ArticleSummary[]
}

type ArticleDetail = VersionSummary & {
  law_key: string
  law_name: string
  article: ArticleSummary & {
    chapter: string | null
    section: string | null
    paragraphs: { no: string | null; text: string }[]
    text: string
  }
  citation: string
}
```

지정한 버전이 없으면 다른 시행일의 판으로 대체하지 않고 404를 반환한다. 조문 번호도 일치해야 한다. 빈 제목이나 본문은 원문대로 보존한다. 삭제 조문은 `deleted`로 구분한다.

`source_url`은 HTTP 또는 HTTPS 출처 URL이다. 한국어 경로와 쿼리를 포함한 원문을 보존한다. Spring은 출처 링크를 가져오거나 프록시하지 않는다. 화면은 텍스트를 HTML로 실행하지 않고 표시해야 한다. 링크는 응답에 포함된 URL을 사용한다.

예를 들어 `articles?offset=50&limit=50`은 해당 판의 51번째 조문부터 최대 50개를 읽는다. `offset >= total`이면 빈 배열을 반환한다. `total`은 해당 판의 `article_count`와 같다.

## 최신화 작업

화면을 열면 다음 요청으로 최근 작업을 확인할 수 있다.

```http
GET /api/admin/laws/refresh-jobs
```

작업이 없을 때:

```json
{ "item": null }
```

작업이 있으면 `item`에 아래 Job 객체가 들어간다. 직접 ID로 조회하면 Job 객체 자체를 반환한다.

사용자가 최신화를 실행하면 본문 없이 POST한다. `GET /api/auth/csrf`의 `header_name`과 `token`으로 받은 CSRF 헤더를 로그인 세션 쿠키와 함께 보낸다.

```http
POST /api/admin/laws/refresh-jobs
```

202 응답 예시:

```json
{
  "job_id": "74af7d24-2c46-47a9-a281-7b3a11b54482",
  "status": "RUNNING",
  "started_at": "2026-10-21T05:00:00Z",
  "finished_at": null,
  "previous_dataset_id": "previous-dataset-id",
  "dataset_id": null,
  "error_code": null
}
```

반환된 `job_id`로 상태를 조회한다. 202는 접수 성공이며 최신화 완료를 뜻하지 않는다.

```http
GET /api/admin/laws/refresh-jobs/74af7d24-2c46-47a9-a281-7b3a11b54482
```

| status | finished_at | dataset_id | error_code |
| --- | --- | --- | --- |
| `RUNNING` | null | null | null |
| `SUCCEEDED` | 완료 시각 | 적용된 데이터셋 ID | null |
| `FAILED` | 종료 시각 | null | `REFRESH_FAILED` 또는 `REFRESH_INTERRUPTED` |

`previous_dataset_id`는 작업 시작 전 데이터셋 ID다. 성공한 데이터셋의 내용이 같으면 이전 ID와 새 ID도 같을 수 있다. 성공 상태가 되면 법령 목록을 다시 읽어 변경된 데이터셋과 수집 시각을 표시한다.

동시에 하나의 최신화 작업만 실행한다. 진행 중 POST는 `409 REFRESH_BUSY`다. 수집이나 파싱 및 검증이 실패하면 전체 작업을 실패 처리하고 기존 제공 데이터를 유지한다. 처리 도중 중단된 작업은 재시작 시 저장 상태를 확인해 복구하며 적용을 완료하지 못한 작업은 `REFRESH_INTERRUPTED`로 표시한다. 실패 응답에 외부 예외 원문이나 자격 증명을 넣지 않는다.

## 관리자 데이터 요약

```http
GET /api/admin/data/summary
```

이 경로도 활성 관리자만 사용할 수 있다. 쿼리 파라미터는 받지 않는다. 기업과 법령의 연결 상태를 각각 조회하며 한쪽이 실패해도 다른 쪽의 결과를 반환한다.

```ts
type SourceSummary =
  | { status: "AVAILABLE"; data: object }
  | { status: "UNAVAILABLE"; data: null; error_code: string }

type DataSummary = {
  checked_at: string
  suppliers: SourceSummary
  laws: SourceSummary
}
```

법령의 AVAILABLE `data`는 LawCatalog다. UNAVAILABLE은 수집 결과 0건을 뜻하지 않는다. 화면은 `data: null`을 0건으로 변환하지 않아야 한다. `checked_at`은 이번 연결 상태 확인 시각이며 법령 수집 시각과 다르다.

## 오류

오류 envelope는 `{request_id, error:{code,message,retryable}}`다. 내부 오류 문구는 공개 응답의 고정 문구로 바꾼다.

| HTTP | code | 의미 |
| --- | --- | --- |
| 400 | `VALIDATION_ERROR` | 잘못된 경로 인자나 쿼리 또는 본문 |
| 401 | `UNAUTHENTICATED` | 로그인 세션 없음 |
| 403 | `FORBIDDEN` | ACTIVE 관리자 권한 없음 |
| 403 | `CSRF_INVALID` | 변경 요청의 CSRF 토큰 누락 또는 불일치 |
| 404 | `LAW_NOT_FOUND` | 지정 법령이나 판 또는 조문이나 작업이 없음 |
| 409 | `REFRESH_BUSY` | 다른 최신화 작업 진행 중 |
| 502 | `UPSTREAM_INVALID_RESPONSE` | 내부 계약과 다른 응답이나 내부 인증 실패 |
| 503 | `UPSTREAM_UNAVAILABLE` | 내부 설정이나 연결을 사용할 수 없음 |
| 504 | `UPSTREAM_TIMEOUT` | 내부 응답 제한 시간 초과 |

Spring은 내부 응답의 JSON 형식과 Content-Type 및 필수 필드와 상태 조합을 확인한다. 법령 ID와 판 ID 및 조문 번호가 요청과 다르거나 집계가 일치하지 않으면 502다. 내부 리다이렉트를 따라가지 않으며 자동으로 POST를 재시도하지 않는다. POST 통신이 끊겼다면 먼저 최근 작업을 조회해 접수 여부를 확인한다.

## 검증

```sh
mvn -f backend/pom.xml -B -ntp \
  -Dtest=AdminLawGatewayHttpTest,AdminLawControllerTest test
```

게이트웨이 검사는 실제 로컬 HTTP 스텁을 사용한다. 한국어 경로 인코딩과 원문 출처 보존 및 오류 정제와 timeout과 리다이렉트 차단을 검증한다. MVC 검사는 운영 SecurityConfiguration을 적용해 관리자 권한과 CSRF 및 요청 검증을 확인한다. 최신화의 실제 외부 수집과 모델 호출은 이 검사에 포함하지 않는다.
