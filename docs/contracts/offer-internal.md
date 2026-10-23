# 협상 문서 내부 API

이 경로는 백엔드만 호출한다. 모든 요청은 `X-Internal-Api-Key` 인증이 필요하다.
챗봇 intents에 연결하지 않는다. 요청 최상위의 알 수 없는 필드는 거절한다.

공통 오류 형식은 다음과 같다. 본문이 없는 GET 요청의 `request_id`는 Agent가 만든다.

```json
{"request_id": "uuid 또는 null", "error": {"code": "CODE", "message": "설명", "retryable": false}}
```

인증 실패는 401 `UNAUTHORIZED`다. 입력 형식 오류는 400 `VALIDATION_ERROR`다.
응답과 저장본은 내부 자료다. 메일 확인 토큰과 검토 토큰은 백엔드 서버에 보관한다.

## 1. 외부 문구 탐지

`POST /internal/offer-texts/check`

```json
{
  "request_id": "uuid",
  "document": {"...": "현재 OfferDocument"},
  "texts": {
    "cover_letter": "제안서 인사말",
    "title": "제목",
    "delivery_notes": "납품 안내",
    "linkage_terms.조정 산식": "조정 조건",
    "mail_message": "추가 메일 문구"
  }
}
```

`document.content_hash`는 무시한다. `texts` 키는 자유 문자열이다. 최대 20개를 받으며 값은 각각 2000자 이하다.
빈 문자열은 건너뛴다. 모델과 MCP를 호출하지 않는다.

`writer.INTERNAL_TERMS`와 `writer.unknown_numbers`를 재사용한다.
허용 숫자는 `writer.payloads()`의 `supplier_terms`와 같은 공개 조건에서 얻는다.
`reply_by`와 `issued_on` 및 `as_of`의 날짜 숫자도 허용한다.

```json
{
  "request_id": "uuid",
  "checked": true,
  "findings": [
    {"field": "cover_letter", "code": "INTERNAL_TERM", "detail": "최대 수용가"},
    {"field": "mail_message", "code": "UNKNOWN_NUMBER", "detail": "2050"}
  ],
  "method": ["내부 협상 용어 목록 대조", "숫자는 제안서 공개 조건 값과 대조"],
  "limitations": ["목록에 없는 표현이나 한글로 쓴 숫자는 탐지하지 못합니다"]
}
```

탐지 결과가 있어도 200이다. 실제 발송 허용 여부와 사람의 최종 확인은 백엔드가 처리한다.

## 2. 제안 조건과 가격 범위 재검사

`POST /internal/offer-proposals/validate`

```json
{
  "request_id": "uuid",
  "offer": {
    "supplier": "SUP-0060",
    "rfq_id": "RFQ-2026-002",
    "as_of": "2026-10-23",
    "unit_price": 1948,
    "payment_days": 60,
    "price_linkage": true,
    "contract_days": 365,
    "raw_material_ratio": 0.42,
    "energy_ratio": null,
    "linkage_waived_by_agreement": false,
    "supplier_size": {},
    "buyer_size": {}
  },
  "price_points": [
    {"name": "settlement", "unit_price": 1999},
    {"name": "walk_away", "unit_price": 2050}
  ]
}
```

`price_points`는 생략하면 빈 배열이다. 이름은 빈 문자열을 제외한 문자열이다. 단가는 양의 정수다.
`offer`로 `check_offer`를 한 번 호출한다. 각 가격점은 단가만 바꾸어 한 번씩 호출한다.
조건을 자동 수정하거나 협상안을 다시 생성하지 않는다.
선택 `offer.transaction`을 전달하면 기존 생성기와 같은 거래 사실 판정도 각 조건 검사에 반영한다.

`get_price_benchmarks`를 한 번 호출해 RFQ 목표가와 시장 중앙값 및 직전 견적 대비 비율을 계산한다.
비교 기준이 없으면 해당 비율은 null이다. 계약금액은 검사 결과를 우선한다.
검사 결과에 금액이 없으면 기존 생성기와 같이 `연간 물량 × 단가 × 계약 일수 / 365`를 반올림한다.

```json
{
  "request_id": "uuid",
  "conditions": {
    "verdict_code": "CHECK_NEEDED", "violations": [], "warnings": [], "law_refs": [],
    "contract_amount": 467520000,
    "vs_target_pct": 0.0, "vs_market_pct": null, "vs_last_quote_pct": null
  },
  "price_points": [
    {"name": "settlement", "unit_price": 1999, "verdict_code": "CHECK_NEEDED",
     "violations": [], "warnings": [], "law_refs": [], "contract_amount": 479760000,
     "vs_target_pct": 2.6, "vs_market_pct": null, "vs_last_quote_pct": null}
  ],
  "scope": ["선택한 협상안의 조건 검사", "협상 범위의 가격별 검사"],
  "limitations": ["양보 순서의 모든 조건 조합을 검사하지는 않았습니다"]
}
```

위반이 있어도 검사 실행이 끝나면 200이다.
도구 입력 오류는 422 `RFQ_NOT_FOUND` 또는 `SUPPLIER_NOT_FOUND` 또는 `VALUE_OUT_OF_RANGE`다.
`SUPPLIER_AMBIGUOUS`와 `DB_UNAVAILABLE` 도구 오류도 코드만 유지해 422로 반환한다.
시간 초과는 504 `UPSTREAM_TIMEOUT`이며 `retryable=true`다. 예상하지 못한 오류는 500 `INTERNAL_ERROR`다.

## 3. 저장본 내부 브리핑 PDF

`POST /internal/offer-proposals/briefing/render`

```json
{
  "request_id": "uuid",
  "briefing": {"...": "생성 응답의 internal_briefing에 사용자 수정을 반영한 저장본"},
  "state": {
    "current_revision": 3,
    "validation_status": "recheck_required",
    "checked_revision": 1,
    "conclusion": null,
    "inherited_from": null,
    "derived_stale": true,
    "user_edited_paths": ["/offer/payment_days", "/writing/strategy"],
    "limitations": ["사용자가 수정한 내부 문구는 검사하지 않았습니다"]
  },
  "issued_on": "2026-10-23"
}
```

`state`와 `issued_on`은 선택 필드다. 모델이나 MCP를 호출하지 않는다.
`issued_on`이 없으면 `briefing.as_of`로 PDF 시각을 고정한다. 같은 입력은 같은 파일 바이트를 만든다.

`validation_status`는 `not_checked` 또는 `checking` 또는 `checked` 또는 `failed` 또는 `recheck_required`다.
`conclusion`은 null 또는 `no_risk_identified_in_scope` 또는 `conditional` 또는 `risk_identified`다.

상태가 있으면 표지 아래에 버전과 한글 검사 상태를 표시한다.
`checked`이고 검사 버전이 현재 버전과 같거나 `inherited_from`이 있을 때만 판정 문구를 표시한다.
그 외에는 현재 버전을 검사하지 않았다는 문구와 마지막 검사 버전을 표시한다.
위반 건수는 저장본의 선택 필드 `offer.violations` 배열이 있을 때 그 길이를 사용한다.

`derived_stale=true`이면 협상 범위의 대비 비율과 가격별 판정을 `갱신 대기`로 표시한다.
수정 경로에 해당하는 전략과 제안 멘트 및 대응 멘트는 `사용자 수정 문구 (검사하지 않음)`으로 표시한다.
미검사 상태에서는 문구 검사 통과 표시도 사용하지 않는다. `limitations`는 고지 앞에 목록으로 표시한다.
모든 쪽의 기존 대외비 표시는 유지한다. `state`가 없으면 기존 생성 경로와 같은 내용을 렌더한다.

200 응답은 `application/pdf`다.

- `Content-Disposition`: 기존 UTF-8 파일명 규칙을 사용한다. 상태가 있으면 `내부브리핑_<RFQ>_<SUP>_r<current_revision>.pdf`다.
- `X-File-Sha256`: PDF 바이트의 SHA-256이다.

## 4. 검토 결과에서 협상안 생성

`POST /internal/offer-proposals/from-review`

```json
{
  "request_id": "uuid",
  "review_token": "서명된 검토 토큰",
  "target_price": 1948,
  "max_price": 2050,
  "intent": "target",
  "notes": "협상 요청 사항",
  "sender": {"name": "홍길동", "department": "구매팀", "job_title": "담당", "email": "buyer@example.test"}
}
```

필수는 `request_id`와 `review_token`이다. 나머지는 선택 필드다.
검토 스냅샷에서 `OfferProposalRequest` 필드만 가져온다. `transaction`은 포함하며 검토 `unit_price`는 제외한다.
`target_price`가 없으면 기존 생성기의 RFQ 목표 단가를 쓴다. 검토 단가로 자동 치환하지 않는다.
`sender`는 백엔드가 세션 회원 정보로 채워 전달한다.

200 응답은 기존 JSON 생성 응답과 같은 `{request_id, offer_document, check, internal_briefing}`이다.
거래 사실이 있으면 내부 브리핑에 `review_basis`와 `draft_status`가 들어간다.
협상안이 있으면 `offer.condition_basis`와 `offer.assessed_alternatives` 및 `offer.commercial_comparison`도 들어간다.
`review_basis`에는 검토 ID와 버전 및 입력 해시만 담는다. 전체 evidence는 복사하지 않는다.
차단된 조건이면 `offer_document=null`이며 내부 브리핑에 `status=no_offer`와 사유를 담는다.
이 내부 근거와 금융효과 및 구매부담은 외부 제안서 JSON과 PDF 및 DOCX에 넣지 않는다.

서명 변조와 만료를 포함한 검토 토큰 검증 실패는 409 `REVIEW_EXPIRED_OR_CHANGED`다.
도구 오류와 시간 초과는 기존 생성 API와 같은 422 및 504 응답이다.
그 밖의 오류는 500 `INTERNAL_ERROR`다. 문구 생성은 기존 생성기와 같이 `?writing=false`로 끌 수 있다.

## 5. 메일 전달

| 경로 | 본문 또는 쿼리 | mail-mcp 도구 |
| --- | --- | --- |
| `POST /internal/offer-mails/preview` | `request_id`와 `document` 및 `attachments`. 선택 `contact_id`와 `message` | `preview_offer_mail` |
| `POST /internal/offer-mails/send` | 미리보기 입력과 `confirmation_token` 및 `requested_by`. `contact_id`와 `client_ref` 필수 | `send_offer_mail` |
| `GET /internal/offer-mails` | 필수 `document_id`. 선택 `client_ref` | `list_offer_mail_sends` |

문서와 첨부 및 선택 인자는 그대로 전달한다. 생략한 선택 인자를 새로 채우지 않는다.
성공은 200 `{request_id, result}`이며 `result`는 도구 결과 그대로다. 미리보기의 `confirmation_token`도 포함한다.

`MAIL_MCP_URL` 기본값은 `http://localhost:9104/mcp`다. compose는 `http://mail-mcp:9104/mcp`를 사용한다.
기존 `McpToolCaller`의 Streamable HTTP 연결을 사용한다.
`MAIL_SEND_TIMEOUT_SECONDS` 기본값은 30이다. 0보다 크고 35보다 작아야 한다.
미리보기와 조회는 기존 `AGENT_REQUEST_TIMEOUT_SECONDS`를 사용한다.

| 상황 | 응답 |
| --- | --- |
| [mail.md 5절](mail.md)의 도구 오류 | 422. 원래 코드와 메시지를 유지한다. `INVALID_CLIENT_REF`와 `CLIENT_REF_USED`도 포함한다 |
| 연결 실패 | 503 `UPSTREAM_UNAVAILABLE` |
| 시간 초과 | 504 `UPSTREAM_TIMEOUT`. `retryable=true` |

발송 시간 초과 메시지에는 `결과 불명확`을 명시한다. Agent는 같은 발송 요청을 자동 재전송하지 않는다.
백엔드는 저장한 `document_id`와 `client_ref`로 발송 기록을 조회해 결과를 확인한다.
