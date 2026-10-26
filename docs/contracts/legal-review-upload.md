# 엑셀 거래·계약 조건 법규 검토 API (#284)

엑셀에 적은 거래·계약 조건을 행별로 읽어 기존 법규 검토 Agent(`POST /internal/legal-reviews`, [legal-review-evidence.md](legal-review-evidence.md))로 검토한다.
업로드한 조건 자체를 검토한다. 새 협상안을 만들거나 입력 조건을 자동으로 고치지 않는다. 그래서 후보 생성·조정을 포함하는 `/internal/offer-proposals/batch`는 쓰지 않는다. `check_offer`는 Agent 안에서 호출하며 백엔드가 따로 부르지 않는다.

**검토 결과는 협상 작업에 저장한다.** 정상 행마다 새 협상 작업을 만들고(`POST /api/negotiations`와 같은 규칙), 그 작업에 검토를 저장한다(`POST /api/negotiations/{id}/reviews`와 같은 규칙, `neg_review_revisions`). 그래서 행의 `negotiation_id`와 `review_revision`으로 바로 협상안을 만들 수 있다(`POST /api/negotiations/{id}/proposals`, [negotiation-workspace.md](negotiation-workspace.md)). 업로드 테이블에는 업로드 건, 엑셀 행 번호, 원본 칸 값, 그리고 협상 작업에 저장되지 않는 입력 오류·실행 실패 상태만 둔다.

흐름: 엑셀 업로드 → 행별 해석·입력 검증 → 행 저장(QUEUED / INVALID) → 백그라운드에서 행마다 협상 작업 생성 + 검토 저장 → 행별 결과 조회 → 협상안 생성 또는 조건을 바꾼 재검토(협상 작업 API)

| 파일 | 내용 |
| --- | --- |
| [`ReviewWorkbookParser.java`](../../backend/src/main/java/com/negopilot/backend/legalreview/ReviewWorkbookParser.java) | 머리글과 칸 값 → `offer`·`supplier_size`·`buyer_size`·`transaction` |
| [`LegalReviewRunner.java`](../../backend/src/main/java/com/negopilot/backend/legalreview/LegalReviewRunner.java) | 행마다 협상 작업 생성과 검토 저장, 실패·임대 만료·재시작 처리 |
| [`NegotiationReviews.java`](../../backend/src/main/java/com/negopilot/backend/negotiation/NegotiationReviews.java) | 협상 작업 생성·검토 저장을 공개 API와 같은 검증으로 호출하는 입구 |
| [`LegalReviewUploadService.java`](../../backend/src/main/java/com/negopilot/backend/legalreview/LegalReviewUploadService.java) | 업로드 저장, 조회, 실패 행 재시도 |
| [`V5__legal_review_uploads.sql`](../../backend/src/main/resources/db/migration/V5__legal_review_uploads.sql) | 업로드 건과 엑셀 행 |

## 1. 공통 규칙

활성 회원 세션이 필요하다. 변경 요청에는 `X-CSRF-TOKEN`을 보낸다. 업로드 소유자만 조회하고 재검토할 수 있다. 다른 회원의 업로드는 404 `RESOURCE_NOT_FOUND`다. JSON 키는 snake_case다. 정의하지 않은 쿼리 파라미터와 폼 필드는 400이다.

## 2. 엑셀 형식

`.xlsx` 1MB 이하. 시트 이름이 `거래조건`이면 그 시트를, 없으면 첫 시트를 거래 조건으로 읽는다. 연동 대상이 있으면 `연동상세` 시트를 추가한다. 다른 시트는 읽지 않는다.

1행은 머리글, 2행부터 **한 행이 검토 한 건**이다. 빈 행은 건너뛴다. 거래조건은 최대 30행, 연동상세는 최대 300행이다.

머리글은 공백, 괄호 안 단위(`(원)`, `(%)`, `(억원)`), `_`, `-`, `·`, `.`, `*`, `/`를 지우고 비교한다. `제안 단가(원)`과 `제안단가`는 같은 열이다. 표에 없는 열은 만들지 않아도 되며, 없는 열은 빈 칸과 같다.

### 파일 전체를 거절하는 경우 (400)

모든 행에 똑같이 해당하므로 행별로 나누지 않는다. `error.details.errors[]`에 `{column, message}`를 모두 담는다.

- `.xlsx`가 아니거나 열 수 없는 파일
- 모르는 머리글, 같은 항목의 머리글 중복, 필수 열(아래 `*`)이 없음
- 조건 행이 없거나 행 수 제한 초과

### 파일이 1MB를 넘는 경우 (413)

`error.code`는 `CONTENT_TOO_LARGE`, `retryable`은 false다. `details`는 없다. 파일을 읽기 전에 거절하므로 `request_id`는 null이다.

```json
{"request_id": null, "error": {"code": "CONTENT_TOO_LARGE", "message": "Content Too Large", "retryable": false}}
```

Spring 업로드 한도(`max-file-size` 1MB, `max-request-size` 2MB)에서 거절한다. 화면 주소의 nginx도 요청 본문을 2MB까지 넘기고, 그보다 큰 요청은 nginx가 같은 형식으로 413을 돌려준다.

### 행만 INVALID로 두는 경우

필수 값이 비었거나 형식이 틀린 행은 Agent를 부르지 않고 `INVALID`로 저장한다. 나머지 행은 계속 검토한다. `input_errors[]`에 `{column, message}`를 모두 담는다.
판단에 필요한 사실(기업 정보, 거래 사실)이 비어 있다는 이유로는 거절하지 않는다. 미확인 값은 null 또는 `unknown`으로 보내고, Agent가 `needs_information`과 `missing_fields`로 알려 준다.

### 거래조건 시트

| 머리글 (별칭) | 필드 | 값 |
| --- | --- | --- |
| 공급자 ID* (공급자, 협력사, 협력사 ID) | `supplier` | `SUP-0001` |
| RFQ ID* (RFQ) | `rfq_id` | `RFQ-2026-001` |
| 검토 기준일* (기준일) | `as_of` | 날짜 칸 또는 `YYYY-MM-DD`. 없는 날짜는 오류 |
| 제안 단가* (단가) | `unit_price` | 원. 0보다 큰 정수. `3,900원`도 된다 |
| 지급기한* (지급일수) | `payment_days` | 일. `60일`도 된다 |
| 계약 기간* (거래기간) | `contract_days` | 일 |
| 연동 약정 포함* (연동, 연동 약정) | `price_linkage` | 예/아니오 |
| 원재료 비중 / 에너지 비중 | `raw_material_ratio` / `energy_ratio` | `40%`, `40`, `0.4`는 모두 API `0.4`. `%`가 없으면 1 이하는 비율, 1 초과는 퍼센트 |
| 미연동 합의 | `linkage_waived_by_agreement` | 예/아니오. 비우면 아니오 |

**기업 정보** — 머리글 앞에 `공급자`(또는 `협력사`)나 `구매자`를 붙인다. 예: `공급자 평균매출액(억원)`. `/review` 화면과 같은 규칙으로 보낸다.

| 항목 (별칭) | 필드 | 값 |
| --- | --- | --- |
| KSIC (업종코드) | `ksic_code` | `C30399` |
| 평균매출액 / 자산총액 | `average_revenue_eok` / `assets_eok` | 억원. 원으로 바꾸지 않는다 |
| 독립성 / 관계기업 | `independence_met` / `has_related_enterprises` | 예/아니오/모름 |
| 관계기업 합산 평균매출액 | `related_aggregate_revenue_eok` | 억원. 관계기업이 `예`일 때만 보낸다 |
| 관계기업 합산 계산 검증 | `related_calculation_verified` | 예/아니오. 관계기업이 `예`일 때만 보낸다 |
| 의자·좌석 제조업 / 유예·간주 중소기업 | `seat_manufacturing` / `deemed_sme` | 예/아니오/모름 |
| 유예·경과조치 검토 | `transition_reviewed` | 예/아니오. 비우면 아니오 |
| 규모 근거 (근거, 확인자료) | `evidence_reference` | 500자 이하 |

**거래 사실** — `offer.transaction`. 필드와 enum은 [transaction.py](../../shared/src/negopilot_shared/transaction.py)가 기준이다. 모든 행에 transaction 객체를 보내며, 비운 칸은 null, enum은 `unknown`, 증빙 목록은 빈 배열이다.

| 머리글 (별칭) | 필드 | 값 |
| --- | --- | --- |
| 거래 유형 | `transaction_type` | 제조위탁(제조) → `manufacturing`, 구매 → `purchase`, 모름·빈 칸 → `unknown` |
| 당사자 요건 확인 | `party_requirements_confirmed` | 예/아니오/모름 |
| 발주 구분 (신규·기존 발주, 단가 적용 범위) | `price_scope` | 신규(신규 발주) → `new_order`, 기존(기존 발주) → `existing_order`, 모름 → `unknown` |
| 최초 계약일 / 갱신일 | `contracted_on` / `renewed_on` | 날짜 |
| 실제 계약금액 / 확정 구매수량 | `actual_contract_amount` / `committed_quantity` | 원 / 개. 0보다 큰 정수 |
| 기존 단가 (기존 발주 단가) | `previous_unit_price` | 원 |
| 감액 사유 / 감액 증빙 | `reduction_reason` / `reduction_evidence` | 텍스트 / 목록 |
| 지급 기산점 | `payment_basis` | 수령일(목적물 수령일) → `receipt`, 완료일(용역 완료일) → `completion`, 월 합의일(계산서 발행일) → `monthly_agreed`, 모름 → `unknown` |
| 수령일 / 완료일 / 계산서 발행 합의일 / 약정 지급일 | `receipt_date` / `completion_date` / `monthly_agreed_date` / `agreed_due_date` | 날짜 |
| 기성금 수령 해당 / 기성금 수령일 | `upstream_payment_applicable` / `upstream_received_on` | 예/아니오/모름 / 날짜 |
| 지급 예외 주장 / 사유 / 증빙 | `payment_exception_claimed` / `payment_exception_reason` / `payment_exception_evidence` | 예/아니오/모름 / 텍스트 / 목록 |
| 미연동 합의 사유 / 증빙 | `waiver_reason` / `waiver_evidence` | 텍스트 / 목록 |
| 거래 증빙 | `evidence_references` | 목록 |

목록 칸은 `,`, `;`, 줄바꿈으로 나눈다. 예/아니오 칸은 `Y`/`N`, `O`/`X`, `TRUE`/`FALSE`, `1`/`0`, `있음`/`없음`, `포함`/`미포함`, `해당`/`해당없음`, `완료`/`미완료`와 엑셀 논리값도 받는다. 모름은 빈 칸, `모름`, `미확인`, `-`다.

### 연동상세 시트

연동 대상마다 한 행이다. **공급자 ID + RFQ ID**가 같은 거래조건 행의 `transaction.linkage_details[]`에 붙는다.

| 머리글 (별칭) | 필드 | 값 |
| --- | --- | --- |
| 공급자 ID*, RFQ ID* | (연결 키) | 거래조건 시트와 같다 |
| 구분* (대상 구분) | `category` | 원재료 → `material`, 에너지 → `energy` |
| 대상 이름* (대상, 원가 대상) | `name` | 텍스트 |
| 원가 비중 (비중) | `cost_ratio` | 비율. 거래조건의 비중과 같은 규칙 |
| 기준 지표 / 조정 산식 / 조정 주기 | `indicator` / `formula` / `frequency` | 텍스트 |
| 조정요건 (조정요건 변동률) | `trigger_ratio` | 0~10%. `3%`, `3`, `0.03`은 모두 `0.03` |
| 기준·비교 시점 / 조정 반영일 | `reference_period` / `reflection_date` | 텍스트 |
| 증빙 (증빙 자료) | `evidence_references` | 목록 |

- 연동상세 행의 값 오류는 연결된 거래조건 행의 `input_errors`가 된다(`연동상세 5행 · 조정요건`). 그 거래 행은 INVALID다.
- 같은 공급자 ID + RFQ ID의 거래조건 행이 없으면 업로드의 `sheet_errors[]`에 `{sheet, row, message}`로 남는다.
- 같은 키의 거래조건 행이 여러 개면 어느 행인지 알 수 없으므로 그 거래 행들을 모두 INVALID로 둔다. 연동 대상이 있는 거래는 키를 한 행으로 맞춘다.

## 3. API

| 메서드와 경로 | 요청 | 응답 |
| --- | --- | --- |
| `POST /api/legal-review-uploads` | multipart `file` | 202 업로드 (행 해석 결과 포함) |
| `GET /api/legal-review-uploads?limit=20` | limit 1~50, 기본 20 | 200 `{items:[업로드 요약]}`. 최근 업로드순 |
| `GET /api/legal-review-uploads/{upload_id}` | 없음 | 200 업로드와 행 요약 |
| `GET /api/legal-review-uploads/{upload_id}/rows/{row_number}` | 없음 | 200 행 상세 |
| `POST /api/legal-review-uploads/{upload_id}/rows/{row_number}/retry` | 본문 없음 또는 `{}` | 202 행 상세. 실행에 실패한 행을 같은 입력으로 다시 검토 |

검토는 비동기다. 업로드 응답은 해석 결과만 담고, 검토 결과는 업로드 조회로 받는다. 업로드 `status`가 `PROCESSING`인 동안 다시 조회한다.

### 업로드

```json
{
  "upload_id": "8f0f...",
  "file_name": "조건.xlsx",
  "row_count": 2,
  "created_at": "2026-10-23T11:00:00+00:00",
  "status": "PROCESSING",
  "counts": {"invalid": 1, "queued": 1, "running": 0, "completed": 0, "failed": 0},
  "sheet_errors": [],
  "rows": [행 요약, ...]
}
```

`status`는 QUEUED·RUNNING 행이 있으면 `PROCESSING`, 없으면 `DONE`이다. 목록 응답의 항목에는 `rows`가 없다.

### 행 요약

| 필드 | 뜻 |
| --- | --- |
| `row_number` | 엑셀 행 번호(머리글이 1행) |
| `status` | `INVALID` 입력 오류 / `QUEUED` 대기 / `RUNNING` 검토 중 / `COMPLETED` 검토 저장 / `FAILED` 실패 |
| `supplier_id`, `rfq_id` | 형식이 맞을 때만 채운다 |
| `input_errors` | INVALID일 때 `[{column, message}]` |
| `negotiation_id` | 이 행으로 만든 협상 작업. 작업을 만들기 전에 실패했으면 null |
| `review_revision` | 이 업로드로 저장한 검토 버전. 협상안 생성의 `review_revision`이다 |
| `review_head_revision` | 그 협상 작업의 현재 최신 검토 버전. 업로드 뒤 재검토하면 `review_revision`보다 커진다. 협상안은 최신 버전으로만 만들 수 있다 |
| `attempts` | 실행 횟수 |
| `execution_status` | Agent 실행 상태 `COMPLETED` / `NEEDS_INPUT` / `FAILED` |
| `assessment_status`, `eligibility` | 거래 사실 검토 `ready` / `needs_information` / `blocked`와 협상안 생성 가능 여부 `ready` / `conditional` / `blocked`. `ready`는 입력 사실 기준 검토 완료이지 적법성 인증이 아니다 |
| `missing_fields` | `legal_review.assessment.missing_fields`. 추가 입력·확인할 항목 |
| `sources_complete` | 공식 자료 조회가 모두 끝났는지. false면 Agent가 `needs_information`으로 낮춘다 |
| `check_verdict_code` | Agent 안의 `check_offer` 판정 `VIOLATION`(위반 소지) / `CHECK_NEEDED` / `OK`. 호출하지 못했으면 null |
| `check_error_code` | `check_offer`가 입력을 거절한 공개 오류 코드 `RFQ_NOT_FOUND` / `SUPPLIER_NOT_FOUND` / `VALUE_OUT_OF_RANGE` ([check-offer.md](check-offer.md) 3절). Agent 실행은 끝나므로 행은 COMPLETED로 저장되고 이 필드로 구분한다. 없으면 null |
| `company_size` | `{supplier, buyer}`마다 `{status, is_sme, is_small_business, missing}`. null이면 규모 판정 보류다. 답변 근거(`answer.evidence`의 `company_size`)를 먼저 쓰고, 없으면 `check_offer` 결과의 같은 값을 쓴다 |
| `error` | FAILED일 때 `{code, retryable}`, 아니면 null |
| `created_at`, `started_at`, `finished_at` | 시각 |

화면은 위반 소지(`check_verdict_code`), 조건 입력 거절(`check_error_code`), 추가 확인 필요(`assessment_status`, `missing_fields`), 기업 규모 판정 보류(`company_size`), 실행 실패(`status`, `error`)를 서로 다른 필드로 구분해 보여 준다.

### 행 상세

행 요약에 다음을 더한다.

| 필드 | 뜻 |
| --- | --- |
| `source_cells` | 엑셀 원본 `{머리글: 칸 값}`. 연동 상세가 있으면 `연동상세: [{row, cells}]` |
| `offer` | 엑셀에서 변환한 검토 조건 (transaction 포함, null 보존). 협상 작업 검토에는 supplier·rfq_id를 뺀 값이 `input_snapshot`으로 저장된다. INVALID면 null |
| `response` | 이 업로드로 저장한 검토의 Agent 응답(generation 0.3). `answer`(설명·인용·근거), `routing`, `legal_review`(assessment 전체). `review_token`은 없다. 저장하지 못한 실패 행은 null |

## 4. 협상안 생성과 재검토

COMPLETED 행은 협상 작업 API를 그대로 쓴다.

- 협상안: `POST /api/negotiations/{negotiation_id}/proposals` `{review_revision, offer:{...}}`. `eligibility`가 `ready`나 `conditional`이어야 한다.
- 조건을 바꾼 재검토: `POST /api/negotiations/{negotiation_id}/reviews`에 `base_revision`(현재 `review_head_revision`)과 검토 조건 전체를 보낸다. **원래 거래 사실을 함께 보내려면 행 상세의 `offer`에서 supplier·rfq_id를 빼고 바꿀 조건만 고쳐 보낸다.** 같은 작업에 새 검토 버전이 쌓이고 이전 버전은 바뀌지 않는다.

같은 공급자 + RFQ의 열린 협상 작업이 이미 있어도 업로드 행마다 새 작업을 만든다. 진행 중인 협상안을 업로드가 "재검토 필요"로 만들지 않게 하기 위해서다. 작업 제목은 기본 제목 뒤에 `· {파일 이름} {행}행`을 붙인다(100자를 넘으면 줄인다).

`retry`는 실행에 실패한 행만 받는다.

| 상태 | 코드 | 언제 |
| --- | --- | --- |
| 409 | `REVIEW_IN_PROGRESS` | 행이 QUEUED 또는 RUNNING이다. `retryable` true |
| 409 | `ROW_ALREADY_REVIEWED` | 검토를 저장한 행이다. `details.negotiation_id`의 협상 작업 API로 재검토한다 |
| 422 | `ROW_INVALID` | 입력 오류 행이다. 엑셀을 고쳐 다시 올린다 |

재시도해도 한 행의 협상 작업과 엑셀 조건 검토는 한 번만 저장된다. 행마다 고정된 멱등 키로 작업 생성과 검토 저장을 요청하기 때문이다.

- 이미 협상 작업을 만든 뒤 검토에 실패한 행은 재시도할 때 같은 작업에 검토를 저장한다. 작업을 만든 뒤 행에 연결하기 전에 멈췄어도 재시도하면 그 작업을 다시 받는다.
- 앞선 실행이 `STALE_REVIEW`로 정리된 뒤에 검토를 저장했다면, 재시도는 Agent를 다시 부르지 않고 그 검토를 행에 연결한다.
- 엑셀 조건은 협상 작업의 첫 검토로만 저장한다. 실패한 행의 작업에서 사용자가 조건을 바꿔 이미 재검토했다면, 재시도는 그 위에 엑셀 조건을 쌓지 않고 `REVISION_CONFLICT`(`retryable` false)로 끝난다. 그 작업은 협상 작업 API로 이어서 쓴다.

## 5. 실행과 실패

- 행마다 실행할 때마다 새 UUID를 `request_id`로 발급한다. 협상 작업 검토가 같은 값으로 내부 인증 `/internal/legal-reviews`를 호출하고 `neg_review_revisions.agent_request_id`로 저장한다.
- 실행기는 동시에 `LEGAL_REVIEW_UPLOAD_CONCURRENCY`행(기본 2)을 처리한다. 한 행이 실패해도 다른 행은 계속 처리한다.
- `review_token`은 협상 작업 검토의 서버 전용 열에만 있다. 업로드 API 응답과 로그에 넣지 않는다.

| `error.code` | 언제 | `retryable` |
| --- | --- | --- |
| `SUPPLIER_NOT_FOUND` | 협상 작업을 만들 때 공급자가 없다 | false |
| `UPSTREAM_TIMEOUT` / `UPSTREAM_UNAVAILABLE` | Agent 응답 시간 초과 / 연결 실패 | true |
| `UPSTREAM_INVALID_RESPONSE` | Agent 응답이 계약과 다르다 | false |
| `REVISION_CONFLICT` | 협상 작업에 이미 다른 검토가 있다(사용자가 조건을 바꿔 재검토함). 엑셀 조건을 쌓지 않았다 | false |
| `IDEMPOTENCY_CONFLICT` | 같은 행의 앞선 실행이 아직 작업 생성이나 검토를 처리 중이다 | true |
| `VALIDATION_ERROR` / `ACCOUNT_NOT_ACTIVE` 등 | 협상 작업 생성·검토 API가 돌려준 오류 코드 그대로 | 그 오류의 값 |
| Agent `error.code` (예: `GENERATION_FAILED`) | `execution_status`가 FAILED다. 협상 작업에는 저장되지 않는다 | true |
| `STALE_REVIEW` | RUNNING이 Agent 응답 제한 + 60초를 넘겼거나 서버가 다시 시작됐다 | true |
| `QUEUE_FULL` | 실행기 대기열이 가득 찼다 | true |
| `INTERNAL_ERROR` | 그 밖의 서버 오류 | true |

- 공식 자료 일부를 조회하지 못한 부분 조회는 실패가 아니다. 검토를 저장하고 `sources_complete: false`, `assessment_status: needs_information`, `missing_fields`의 `official_source_retrieval`로 알린다.
- 서버가 다시 시작되면 RUNNING 행은 `STALE_REVIEW` 실패로 바꾸고 QUEUED 행은 다시 실행기에 넣는다. 백엔드 한 대 실행을 전제로 한다.
- 임대가 끝나 STALE로 정리된 행에 늦게 도착한 결과는 행에 쓰지 않는다. 그 사이 협상 작업에 검토가 저장됐다면 재시도할 때 새 버전을 쌓지 않고 그 검토를 연결한다(4절).

## 6. 저장

원본 파일은 저장하지 않는다. `legal_review_uploads`에 업로드 건(파일 이름, SHA-256, 행 수, `sheet_errors`)을, `legal_review_rows`에 엑셀 행(원본 칸 값, 입력 오류, 변환한 조건, 연결한 협상 작업과 검토 버전, 마지막 request_id, 실패 상태)을 둔다. Agent 응답과 `review_token`은 `neg_review_revisions`에만 있다. 테이블과 제약은 [V5 마이그레이션](../../backend/src/main/resources/db/migration/V5__legal_review_uploads.sql)에 있다.

## 7. 범위

- 기업 정보를 business DB에서 보완하지 않는다. 엑셀에 적은 값만 보낸다. 보완하게 되면 적용값과 출처를 행 상세에 따로 둔다.
- 업로드 건 삭제 API는 없다. 업로드로 만든 협상 작업은 협상 작업 API로 보관(ARCHIVED)한다.
