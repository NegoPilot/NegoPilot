# 협상 작업 API 1단계

Spring이 협상 작업과 검토 결과 및 협상안 버전을 저장한다. 법규 검토와 협상안 생성 및 외부 문서 렌더링은 기존 Agent 경로를 호출한다.
Agent 문서 형식은 [offer-document.md](offer-document.md)를 따른다. 내부 협상 구조는 [offer-proposal.md](offer-proposal.md)를 따른다.

## 1. 공통 규칙

활성 회원의 세션 인증이 필요하다. 변경 요청에는 `X-CSRF-TOKEN`을 보낸다. 작업 소유자는 세션에서 정한다. ADMIN도 다른 회원의 작업에 접근하면 404 `RESOURCE_NOT_FOUND`를 받는다.

JSON 키는 snake_case다. 시각은 오프셋이 있는 ISO-8601 문자열이다. 정의하지 않은 요청 키는 400 `VALIDATION_ERROR`다. `user_id`와 `owner_id` 및 `edited_by`와 `requested_by`는 받지 않는다. 검토와 생성 요청의 거래 대상은 작업에서 정한다. `sender`는 회원 DB의 이름과 부서 및 직책과 이메일로 채운다.

생성과 실행 POST 및 협상안 PATCH는 선택 헤더 `Idempotency-Key`를 받는다. UUID 하나만 보낼 수 있다. 같은 키와 같은 본문이면 첫 성공 응답과 상태 코드를 돌려준다. 본문이 다르면 409 `IDEMPOTENCY_CONFLICT`다. 임대가 남은 실행도 같은 오류를 반환하며 `retryable`은 true다. 임대가 끝나면 다시 실행할 수 있다. 실패 응답은 멱등 결과로 저장하지 않는다.

## 2. 작업과 검토

`{id}`는 협상 작업 UUID다. `review_id`도 같은 값이다.

엑셀 업로드 검토([legal-review-upload.md](legal-review-upload.md))도 정상 행마다 이 규칙으로 작업을 만들고 검토를 저장한다. 그 작업과 검토는 아래 API로 그대로 조회·재검토하고 협상안을 만든다.

| 메서드와 경로 | 요청 | 응답 |
| --- | --- | --- |
| `POST /api/negotiations` | `{supplier_id, rfq_id, title?}` | 201 작업 요약 |
| `GET /api/negotiations?limit=20` | limit 기본 20이며 최대 50 | 200 본인 작업 목록. 최근 수정순 |
| `GET /api/negotiations/{id}` | 없음 | 200 작업 요약과 검토 eligibility 및 현재 협상안 요약 |
| `PATCH /api/negotiations/{id}` | `{row_version, title?, status?}` | 200 작업 요약. status는 OPEN 또는 ARCHIVED |
| `POST /api/negotiations/{id}/reviews` | 기존 ReviewOffer에서 supplier와 rfq_id를 제외한 필드 및 base_revision | 201 저장한 검토 |
| `GET /api/negotiations/{id}/reviews/{revision}` | 없음 | 200 저장한 검토 |

작업 생성 시 협력사 상세 조회로 존재를 확인한다. title 기본값은 `"{rfq_id} · {협력사명}"`이다. stage는 검토 전 `NO_REVIEW`다. 검토 후 `REVIEWED`가 된다. 현재 협상안이 있으면 `PROPOSED`다.

작업 제목은 DDL에 따라 100자 이하다. 기본 제목이 이를 넘으면 400이다. 이 경우 100자 이내 title을 지정한다.

작업 응답 예시:

```json
{
  "negotiation_id": "4bb94157-18fc-4e4c-b913-197452c39648",
  "review_id": "4bb94157-18fc-4e4c-b913-197452c39648",
  "supplier_id": "SUP-0060",
  "rfq_id": "RFQ-2026-002",
  "title": "단가 협상",
  "status": "OPEN",
  "stage": "NO_REVIEW",
  "row_version": 0,
  "review_head_revision": 0,
  "current_proposal_id": null,
  "rfq_verified_at": null,
  "created_at": "2026-10-23T06:00:00Z",
  "updated_at": "2026-10-23T06:00:00Z"
}
```

Agent 실행이 `FAILED`이면 저장하지 않고 기존 `/api/legal-reviews`와 같이 검증된 본문을 200으로 돌려준다. 검토 head는 바뀌지 않는다.

첫 검토는 base_revision을 생략하거나 null로 보낸다. 이후에는 검토 head와 같은 값을 보낸다. 다르면 409 `REVISION_CONFLICT`다. 검토 응답은 기존 generation 0.3 응답에 `review_id`와 `review_revision` 및 `base_revision`과 `input_snapshot` 및 `eligibility`와 `created_at`을 더한다.

```json
{
  "as_of": "2026-10-23",
  "unit_price": 2050,
  "payment_days": 60,
  "price_linkage": true,
  "contract_days": 365,
  "supplier_size": {},
  "buyer_size": {},
  "raw_material_ratio": 0.42,
  "energy_ratio": null,
  "linkage_waived_by_agreement": false
}
```

검토 입력의 null과 빈 객체 및 transaction은 그대로 보존한다. assessment의 `ready`는 `ready`로 저장한다. `needs_information`은 `conditional`로 저장한다. `blocked`는 `blocked`로 저장한다. legal_review가 없으면 eligibility도 null이다. `review_token`은 서버 전용 열에만 보관한다. 공개 응답과 저장 응답 JSON에는 넣지 않는다.

## 3. 협상안 생성과 조회

| 메서드와 경로 | 요청 | 응답 |
| --- | --- | --- |
| `POST /api/negotiations/{id}/proposals` | `{review_revision, offer:{intent?, target_price?, max_price?, notes?}}` | 201 협상안 내부 응답 |
| `GET /api/negotiations/{id}/proposals/{pid}?revision=1` | revision 생략 시 head | 200 내부 응답 |
| `GET /api/negotiations/{id}/proposals/{pid}/external?revision=1` | revision 생략 시 head | 200 외부 전용 응답 |
| `PATCH /api/negotiations/{id}/proposals/{pid}` | `{base_revision, changes, change_note?}` | 200 내부 응답과 impacts |

생성할 review_revision은 검토 head여야 한다. 이전 검토를 사용하면 409 `REVIEW_RECHECK_REQUIRED`다. eligibility가 blocked이거나 null이면 422 `REVIEW_NOT_READY`다. max_price는 target_price 이상이어야 한다.

```json
{"review_revision":1,"offer":{"intent":"target","target_price":1750,"max_price":2050}}
```

Agent 생성 입력에는 검토의 거래 사실과 요청한 협상 목표를 합친다. 검토 단가 `unit_price`와 `transaction`은 넣지 않는다. target_price는 요청값만 보낸다. 새 협상안을 저장할 때 이전 ACTIVE 협상안은 SUPERSEDED가 된다.

내부 응답은 다음 필드를 포함한다.

- `proposal_id`와 `proposal_revision` 및 `head_revision`과 `status`
- `based_on_review`와 `review_recheck_required`
- 공유 조건과 writing을 포함한 `proposal`
- `external_document`와 `generation_input_snapshot`
- `validation`과 `edit_history`
- `available_export_formats`: `{"internal":[],"external":["pdf","docx"]}`

no_offer이면 외부 형식도 빈 배열이다. no_offer는 검사할 협상안이 없으므로 생성 검사 결과를 저장하지 않고 검사 상태는 `not_checked`다. 외부 전용 조회와 재검사 및 내보내기는 422 `NO_OFFER`다.

외부 전용 응답은 `proposal_id`와 `proposal_revision` 및 `external_document`와 최소 검사 요약 `validation`만 반환한다. external_document는 OfferDocument 허용 필드로 새로 조립한다. 내부 응답에서 키를 지워 만드는 방식은 쓰지 않는다. 내부 가격 범위와 양보 순서 및 예상 쟁점과 writing은 반환하지 않는다. benchmarks와 considered 및 filled와 rationale도 반환하지 않는다. warnings와 law_refs 및 target_price와 max_price도 제외한다.

미리보기 document의 `document_id`는 협상안별 고정값이다. version은 `external_content_rev`다. status는 draft이며 content_hash는 null이다. 저장된 비율은 반올림하지 않는다.

## 4. 수정과 검사 상태

```json
{
  "base_revision": 1,
  "changes": {
    "offer": {"payment_days": 45,"notes":"납품 일정을 협의합니다"},
    "writing": {"strategy":"지급 조건을 먼저 협의합니다"}
  },
  "change_note": "지급 조건과 비고 수정"
}
```

| 수정 경로 | 변경 범위 |
| --- | --- |
| `offer.unit_price` / `payment_days` / `price_linkage` / `contract_days` / `linkage_waived_by_agreement` | TERMS |
| `offer.price_range.settlement` / `walk_away` | PRICE_RANGE |
| `offer.notes` | INTERNAL_TEXT와 EXTERNAL_TEXT. delivery_notes도 같은 값 |
| `offer.concessions` / `objections` | INTERNAL_TEXT |
| `writing.strategy` / `objection_scripts` / `opening_message` | INTERNAL_TEXT |
| `external.title` / `cover_letter` / `reply_by` / `linkage_terms` | EXTERNAL_TEXT |

linkage_terms는 `기준 지표`와 `조정 요건(변동률)` 및 `조정 주기`와 `조정 산식`만 받는다. 거래 사실 변경은 409 `REVIEW_RECHECK_REQUIRED`다. 시스템 계산값 변경은 422 `FIELD_NOT_EDITABLE`다. 그 밖의 키는 400이다. 변경 없는 요청은 새 revision을 만들지 않는다.

제시가와 목표 합의가 및 최대 수용가는 오름차순이어야 한다. notes는 최대 500자다. cover_letter는 최대 1500자이며 title은 최대 200자다. reply_by는 실제 ISO 날짜다. 단가 수정 시 opening도 같은 값으로 맞춘다. 파생 금액과 비율 및 과거 가격 판정은 다시 계산하지 않으며 `derived_stale`이 true가 된다. 위험 조건도 사용자가 저장한 값을 유지한다.

TERMS 변경은 세 카운터를 모두 올린다. PRICE_RANGE와 INTERNAL_TEXT 변경은 internal_content_rev를 올린다. EXTERNAL_TEXT 변경은 external_content_rev를 올린다. notes 변경은 내부와 외부 카운터를 모두 올린다. impacts는 문서별 content_revision과 재검사가 필요한 `conditions` 또는 `price_range` 또는 `text`를 알려 준다.

검사 상태는 저장된 revision과 검사 실행 행으로 계산한다.

1. head에 임대가 남은 실행이 있으면 checking이다.
2. head의 최신 실행이 checked이면 그 결론을 사용한다. failed이면 결론은 null이다.
3. head에 실행이 없으면 이전 revision 중 실행 기록이 있는 가장 최근 revision의 최신 실행을 본다. 그 실행이 checked가 아니면(failed 등) 더 예전 결과를 이어받지 않고 recheck_required다. 이후 변경이 INTERNAL_TEXT뿐이면 결과를 이어받고 inherited_from에 실제 검사 revision을 적는다. 사용자가 수정한 내부 문구는 검사하지 않았다는 한계를 더한다.
4. 다른 범위가 바뀌면 recheck_required다. conclusion은 null이다. 과거 결과는 previous에만 둔다. changed_fields에 이후 변경 경로를 넣는다.
5. checked 결과가 없으면 not_checked다.

checked_revision은 실제 검사한 revision이다. 현재 revision과 다른 결과를 현재 결론으로 표시하는 경우는 내부 문구 변경의 이어받기뿐이다.

## 5. 재검사와 외부 내보내기

| 메서드와 경로 | 요청 | 응답 |
| --- | --- | --- |
| `POST /api/negotiations/{id}/proposals/{pid}/revisions/{rev}/validations` | 없음 | 200 실행 결과와 head 기준 validation |
| `GET /api/negotiations/{id}/proposals/{pid}/revisions/{rev}/validations` | 없음 | 200 실행 목록. 최신순 |
| `POST /api/negotiations/{id}/proposals/{pid}/exports` | `{revision, document_type, formats, final?}` | 201 새 묶음. 재사용은 200 |
| `GET /api/negotiation-exports/{fileId}/file` | 없음 | 200 파일 바이트 |

재검사는 해당 revision의 draft 문서를 기존 Agent render에 전달한다. 살아 있는 checking이 있으면 409 `VALIDATION_IN_PROGRESS`다. 임대가 끝난 실행은 `failed`와 `STALE_CHECK`로 정리한다. OFFER_VIOLATION도 검사 실행은 성공했으므로 200으로 반환한다. 이때 conclusion은 risk_identified다. 다른 상류 오류는 실행을 failed로 기록하고 원래 오류로 응답한다.

재검사는 선택한 협상안의 조건만 검사한다. 가격 범위와 문구는 검사하지 않는다. scope와 limitations에 이 범위를 적고 text_findings는 null로 둔다. 생성 시에는 협상 범위 가격 검사도 포함하지만 모든 양보 조합을 검사한 것은 아니다.

```json
{"revision":2,"document_type":"external","formats":["pdf","docx"],"final":true}
```

document_type은 external만 지원한다. formats는 pdf 한 개 또는 docx 한 개 또는 둘 다다. 내부 문서 요청은 422 `UNSUPPORTED_EXPORT_FORMAT`이다. include_review_details는 400이다.

final은 요청 revision이 head이며 협상안이 ACTIVE이고 기반 검토가 검토 head일 때만 허용한다. 위반하면 422 `EXPORT_NOT_ALLOWED`와 reasons를 반환한다. 같은 협상안 revision과 문서 상태 및 정렬한 formats의 ready 묶음은 재사용한다. 재사용 전에 파일이 모두 있고 sha256이 맞는지 확인하며, 아니면 묶음을 failed(`FILE_MISSING`)로 바꾸고 새로 렌더한다. processing 묶음이 있으면 retryable인 409 `IDEMPOTENCY_CONFLICT`다. Agent 응답 제한 + 30초가 지난 processing 묶음은 failed(`STALE_EXPORT`)로 정리한다.

묶음 응답에는 `bundle_id`와 `status` 및 `final`과 `document_type` 및 `proposal_revision`과 `content_hash` 및 `render_check`와 `validation_snapshot` 및 `files`가 있다. files는 파일마다 `file_id`와 `format` 및 `file_name`과 `content_type` 및 `byte_size`와 `sha256` 및 `download_url`을 담는다. 파일 이름은 Agent 렌더 응답 그대로다.

파일은 `NEGOTIATION_EXPORT_DIR` 아래 `exports/<yyyy>/<mm>/<file_id>.<ext>`에 원자적으로 저장한다. 기본 디렉터리는 `./var/negotiation-exports`다. 로컬 Docker는 named volume `negotiation-exports`를 `/app/var/negotiation-exports`에 마운트한다. 다운로드에서 소유자와 sha256을 확인한다. 해시가 다르면 500 `INTERNAL_ERROR`다. Content-Disposition은 ASCII 대체 이름과 UTF-8 filename*를 함께 사용한다.

## 6. 오류와 범위

```json
{
  "request_id":"c3823f53-fc08-4438-a4db-c22b9aa453c1",
  "error":{
    "code":"REVISION_CONFLICT",
    "message":"현재 버전과 요청 버전이 다릅니다.",
    "retryable":false,
    "details":{"requested_revision":1,"current_revision":2}
  }
}
```

| 상태 | 코드 |
| --- | --- |
| 400 | VALIDATION_ERROR |
| 401 / 403 | 기존 인증 및 CSRF 오류 |
| 404 | RESOURCE_NOT_FOUND |
| 409 | REVISION_CONFLICT / REVIEW_RECHECK_REQUIRED / VALIDATION_IN_PROGRESS / IDEMPOTENCY_CONFLICT |
| 422 | REVIEW_NOT_READY / FIELD_NOT_EDITABLE / PROPOSAL_SUPERSEDED / UNSUPPORTED_EXPORT_FORMAT / EXPORT_NOT_ALLOWED / NO_OFFER |
| 422 | RFQ_NOT_FOUND / SUPPLIER_NOT_FOUND / VALUE_OUT_OF_RANGE / OFFER_VIOLATION |
| 502 / 503 / 504 | UPSTREAM_INVALID_RESPONSE / UPSTREAM_UNAVAILABLE / UPSTREAM_TIMEOUT |
| 500 | INTERNAL_ERROR |

내부 PDF 렌더는 제공하지 않는다. 문구 검사와 가격 범위 재검사 및 from-review 이식도 하지 않는다. Agent를 호출하는 동안 DB 행 잠금을 유지하지 않는다. 호출 전후에 버전을 확인하고 저장한다.

## 7. 메일 미리보기와 발송

외부 확정본(`final`) 내보내기 묶음을 협력사 담당자에게 보낸다. Spring은 Agent `/internal/offer-mails/*`를 거쳐 mail-mcp를 부른다. 내부 브리핑은 이 경로로 보내지 않는다(`INTERNAL_DOCUMENT` 400).

| 메서드 | 경로 | 설명 |
|---|---|---|
| POST | `/api/negotiations/{id}/proposals/{pid}/mail-previews` | 미리보기 (201) |
| GET | `/api/negotiations/{id}/proposals/{pid}/mails` | 이 협상안의 미리보기·발송 목록 |
| GET | `/api/negotiation-mails/{mailId}` | 하나 조회 |
| POST | `/api/negotiation-mails/{mailId}/send` | 발송. `Idempotency-Key` 필수 |
| POST | `/api/negotiation-mails/{mailId}/reconcile` | 결과 불명확(`UNKNOWN`) 복구 |

미리보기 요청: `bundle_id`(필수), `contact_id`(생략하면 기본 담당자), `message`(2,000자 이하), `acknowledgements` `{check_needed, unresolved_issue_ids}`.
발송 요청: `content_hash` (미리보기에서 본 값).

응답: `mail_id, status, to, from, reply_to, subject, body, message, attachments, content_hash, document_id, document_version, dry_run, expires_at, acknowledgements, send_id, message_id, error_code, created_at, send_requested_at, finished_at`. 확인 토큰은 응답에 넣지 않고 DB에만 둔다.

발송 허용 조건 (미리보기와 발송 직전에 모두 본다. 아니면 422 `SEND_NOT_ALLOWED`, `details.reasons`):

| 이유 | 조건 |
|---|---|
| `BUNDLE_NOT_FINAL` | 외부 확정본이 아니거나 준비되지 않음 |
| `BUNDLE_STALE` | 묶음 이후 조건이나 외부 문서가 바뀜. 내부 문구만 바뀐 경우는 허용 |
| `PROPOSAL_SUPERSEDED` / `REVIEW_RECHECK_REQUIRED` | 대체된 협상안 / 검토가 다시 실행됨 |
| `VALIDATION_REQUIRED` / `RISK_IDENTIFIED` | head 검사가 `checked`가 아님 / 결론이 위반 소지 |
| `OFFER_VIOLATION` | 확정본 `verdict_code`가 `VIOLATION` |
| `CHECK_NEEDED_NOT_ACKNOWLEDGED` | `CHECK_NEEDED`인데 `acknowledgements.check_needed`가 없음 |
| `UNRESOLVED_NOT_ACKNOWLEDGED` | 결론이 `conditional`인데 미해결 쟁점을 모두 확인하지 않음 |

- 추가 문구에 내부 표현(대외비, 최대 수용가, 목표 합의가, 양보 등)이나 제시 단가가 아닌 내부 가격 범위 숫자가 있으면 422 `MESSAGE_INTERNAL_INFO`.
- 상태: `PREVIEWED → SENDING → SENT | DRY_RUN | FAILED | UNKNOWN`, 미리보기 이후 바뀌면 `STALE`.
- 같은 문서 버전·담당자에게 `SENDING`·`SENT`·`UNKNOWN`이 있으면 409 `ALREADY_SENT` (DB 부분 고유 인덱스). 만료 409 `PREVIEW_EXPIRED`, 해시 불일치·변경 409 `PREVIEW_STALE`, 실패한 미리보기 재사용 409 `PREVIEW_USED`.
- mail-mcp가 보내기 전에 거절하면 `FAILED`(`error_code`에 mail-mcp 코드)이고 새 미리보기가 필요하다. 응답은 200이다.
- 시간 초과·연결 실패·`ALREADY_SENT`·`CLIENT_REF_USED`면 `UNKNOWN`(202)으로 두고 자동으로 다시 보내지 않는다. 오래된 `SENDING`도 `UNKNOWN`이 된다.
- 복구: `send_offer_mail`에 `client_ref = mail_id`를 넘겨 두었으므로 `list_offer_mail_sends(document_id, client_ref)`로 정확히 찾는다. 기록이 있으면 그 상태를 반영한다. 없고 토큰이 유효하면 같은 토큰·같은 `client_ref`로 한 번 더 부른다(이미 잡힌 요청이면 mail-mcp가 거절해 중복 발송이 없다). 토큰 만료 + 180초 뒤에도 기록이 없으면 `FAILED`(`NOT_SENT`).
- 저장: `V4__negotiation_mail_sends.sql`의 `neg_mail_sends`. 원본 발송 기록은 mail-mcp SQLite에 있다.
- Agent 쪽 `/internal/offer-mails/*`와 mail-mcp `client_ref`는 별도 Agent PR에 있다.
