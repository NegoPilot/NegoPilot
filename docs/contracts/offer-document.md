# 협상 제안서 문서 계약 (OfferDocument, #236)

협력사에게 보내는 **"단가 및 거래조건 제안서"** 의 형식, 편집·렌더링 API, 내용 해시 규칙입니다.
Agent(가연)가 만들고, 화면(편집·다운로드)과 mail-mcp(#237, 발송)가 받아서 씁니다.

내부 협상 브리핑(목표 합의가, 최대 수용가, 양보 순서, 예상 쟁점, 시장가·직전 견적 비교, 내부 경고, 법규 검토 내부 내용)은 이 문서에 **넣지 않습니다**. 브리핑은 [offer-proposal.md](offer-proposal.md)의 `internal_briefing`과 대외비 PDF로만 나갑니다.

| 파일 | 내용 |
| --- | --- |
| [`agent/src/negopilot_agent/offer_proposal/document.py`](../../agent/src/negopilot_agent/offer_proposal/document.py) | `OfferDocument`, `BuyerSettings`, 해시 |
| [`agent/src/negopilot_agent/api/offer_api.py`](../../agent/src/negopilot_agent/api/offer_api.py) | 생성·render·batch API |

## 1. `OfferDocument` (schema_version "1")

알 수 없는 필드는 거절합니다(`extra=forbid`).

| 필드 | 형식 | 뜻 |
| --- | --- | --- |
| `schema_version` | `"1"` | 형식 버전 |
| `document_no` | 문자열 | `NP-<RFQ>-<SUP>-<YYYYMMDD>` |
| `version` | 정수 ≥ 1 | 편집할 때마다 화면이 올린다 |
| `status` | `draft` \| `final` | 발송은 `final`만 |
| `issued_on`, `response_due`, `as_of` | `YYYY-MM-DD` | 작성일, 회신 기한(기본 작성일 + 7일), check_offer 기준일 |
| `supplier_id`, `supplier_name` | `SUP-0000`, 문자열 | 수신 |
| `sender` | `{company, department, contact_name, phone, email}` | 발신. 환경 변수 `NEGOPILOT_BUYER_*`에서 채운다 |
| `subject` | 문자열 | 제목 |
| `rfq_id`, `part_name`, `vehicle`, `material` | 문자열 | 품목 정보 (`vehicle`, `material`은 null 가능) |
| `annual_volume`, `unit_price`, `contract_days`, `contract_amount`, `payment_days` | 정수 | 제안 조건. `contract_amount`는 render가 다시 계산한다 |
| `payment_method` | 문자열 | 기본 `현금` → "목적물 수령일부터 60일 이내 현금 지급" |
| `price_linkage`, `linkage_waived_by_agreement`, `linkage_needs_input` | bool | 연동 포함 / 미연동 합의 / 원재료 비중을 몰라 미정 |
| `raw_material_ratio`, `energy_ratio` | 0~1 또는 null | |
| `linkage_terms` | `{"기준 지표": "", "조정 요건(변동률)": "", "조정 주기": "", "조정 산식": ""}` | 연동 조항에서 협의해 채울 칸 |
| `delivery_notes` | 문자열(500자 이하) 또는 null | 납품·비고 |
| `cover_letter` | 문자열(1500자 이하) | 인사말. 모델이 쓰면 숫자 검사를 거친다 |

## 2. 내용 해시

```
content_hash = "sha256:" + hex(sha256(UTF-8(json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")))))
```

- `document`는 render 응답의 `document`(=`OfferDocument.model_dump(mode="json")`)를 그대로 쓴다. 날짜는 `YYYY-MM-DD` 문자열, 비율은 숫자 그대로다.
- 값이 하나라도 바뀌면(버전, 상태 포함) 해시가 달라진다. 발송 측은 받은 문서로 다시 계산해 승인된 해시와 같은지 확인한다.
- PDF·DOCX 응답에는 `X-Content-Hash`(문서 해시)와 `X-File-Sha256`(첨부 파일 바이트의 sha256 hex) 헤더가 붙는다.

## 3. API (`X-Internal-Api-Key` 인증, 오류는 다른 내부 API와 같은 형식)

### 생성: `POST /internal/offer-proposals`

요청은 [offer-proposal.md](offer-proposal.md)의 `OfferProposalRequest`와 같다.

- `?format=json`(기본): `{request_id, offer_document, content_hash, check: {verdict_code, warnings}, internal_briefing}`
  - `internal_briefing`: 협상안 전체(협상 범위, 양보 순서, 쟁점, 근거, 모델 협상 문구). 협력사에 보내지 않는다
- `?format=pdf` / `?format=docx`: 제안서 파일
- `?format=briefing_pdf`: 내부 브리핑 PDF (모든 쪽에 "대외비 — 협력사 발송 금지")
- 협상안을 만들 수 없으면(`no_offer`) JSON은 `offer_document: null`과 이유를 돌려주고, 파일 형식 요청은 422 `NO_OFFER`

### 편집 후 렌더링: `POST /internal/offer-proposals/render`

```json
{"request_id": "uuid", "document": { "...": "OfferDocument" }}
```

- 고친 조건으로 `check_offer`를 다시 부른다(같은 `as_of`).
  - violation이 있으면 **422 `OFFER_VIOLATION`**, 본문에 `findings`(code, message, law_refs). 제안서를 만들지 않는다
  - warning은 통과시키고 JSON 응답의 `check.warnings`에만 담는다(문서에는 넣지 않는다)
- `?format=json`(기본): `{request_id, document, content_hash, check: {verdict_code, warnings}}`. `document.contract_amount`는 check_offer 값으로 갱신된다
- `?format=pdf` / `?format=docx`: 파일. 헤더 `X-Content-Hash`, `X-File-Sha256`, `X-Document-Version`

### 첨부 파일 이름과 형식

| 형식 | Content-Type | 파일 이름 |
| --- | --- | --- |
| 제안서 PDF | `application/pdf` | `제안서_<RFQ>_<SUP>_v<version>.pdf` |
| 제안서 DOCX | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` | `제안서_<RFQ>_<SUP>_v<version>.docx` |
| 내부 브리핑 PDF | `application/pdf` | `내부브리핑_<RFQ>_<SUP>.pdf` |

`Content-Disposition`은 `attachment; filename="<ASCII 이름>"; filename*=UTF-8''<퍼센트 인코딩한 한글 이름>`이다.

## 4. 발송(mail-mcp, #237)이 지킬 것

1. `status == "final"`인 문서만 보낸다.
2. 받은 `document`로 2절 해시를 다시 계산해 승인된 `content_hash`와 같은지 본다. 첨부는 같은 문서로 `/render`가 만든 파일이며 `X-Content-Hash`가 같아야 한다.
3. 수신 주소는 협력사 담당자 이메일이다. 지금 business-mcp에는 이 값이 없어 컬럼(또는 담당자 테이블)을 추가해야 한다.

## 5. 변경 절차

필드를 바꾸면 이 문서, `document.py`, 테스트를 한 PR에서 같이 고치고, 뜻이 바뀌면 `schema_version`을 올립니다. mail-mcp 담당 승인이 필요합니다.
