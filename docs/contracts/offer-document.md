# 협상 제안서 문서 계약 (OfferDocument, #236)

협력사에게 보내는 **"단가 및 거래조건 제안서"** 의 형식, 편집·렌더링 API, 내용 해시 규칙입니다.
Agent(가연)가 만들고, 화면(편집·다운로드)과 mail-mcp(#237, 발송)가 받아서 씁니다. **필드 이름과 해시 규칙은 mail-mcp 계약 [mail.md](mail.md) 2·3절과 같습니다.** 둘 중 하나를 바꾸면 다른 하나도 같은 PR에서 고칩니다.

내부 협상 브리핑(목표 합의가, 최대 수용가, 양보 순서, 예상 쟁점, 시장가·직전 견적 비교, 내부 경고, 법규 검토 내부 내용)은 이 문서와 첨부에 **넣지 않습니다**. 브리핑은 [offer-proposal.md](offer-proposal.md)의 `internal_briefing`과 대외비 PDF로만 나갑니다.

| 파일 | 내용 |
| --- | --- |
| [`agent/src/negopilot_agent/offer_proposal/document.py`](../../agent/src/negopilot_agent/offer_proposal/document.py) | `OfferDocument`, `BuyerSettings`, `content_hash()` |
| [`agent/src/negopilot_agent/offer_proposal/supplier.py`](../../agent/src/negopilot_agent/offer_proposal/supplier.py) | 제안서 PDF·DOCX, 첨부 목록 |
| [`agent/src/negopilot_agent/api/offer_api.py`](../../agent/src/negopilot_agent/api/offer_api.py) | 생성·render·batch API |
| [`docs/contracts/mail.md`](mail.md) | 발송(preview → 확인 토큰 → send), 해시 검사 |

## 1. `OfferDocument` (schema_version "1")

알 수 없는 필드는 거절합니다(`extra=forbid`). **굵게** 쓴 필드는 mail-mcp가 읽습니다(mail.md 2절).

| 필드 | 형식 | 뜻 |
| --- | --- | --- |
| `schema_version` | `"1"` | 형식 버전 |
| **`document_id`** | 문자열 | 제안서 번호 `NP-<RFQ>-<SUP>-<YYYYMMDD>` |
| **`version`** | 정수 ≥ 1 | 편집할 때마다 화면이 올린다 |
| **`status`** | `draft` \| `final` | 발송은 `final`만 |
| `issued_on`, `as_of` | `YYYY-MM-DD` | 작성일, check_offer 기준일 |
| **`reply_by`** | `YYYY-MM-DD` | 회신 기한 (기본 작성일 + 7일) |
| **`supplier_id`**, `supplier_name` | `SUP-0000`, 문자열 | 수신 |
| `sender` | `{company, department, contact_name, job_title, phone, email}` | 발신. 5절 (`phone`은 null 가능) |
| **`title`** | 문자열 | 제목 (메일 제목에도 쓰인다) |
| **`rfq_id`**, **`part_name`**, `vehicle`, `material` | 문자열 | 품목 정보 (`vehicle`, `material`은 null 가능) |
| `annual_volume`, `unit_price`, `contract_days`, `contract_amount`, `payment_days` | 정수 | 제안 조건. `contract_amount`는 render가 check_offer 값으로 다시 채운다 |
| `payment_method` | 문자열 | 기본 `현금` → "목적물 수령일부터 60일 이내 현금 지급" |
| `price_linkage`, `linkage_waived_by_agreement`, `linkage_needs_input` | bool | 연동 포함 / 미연동 합의 / 원재료 비중을 몰라 미정 |
| `raw_material_ratio`, `energy_ratio` | 0~1 또는 null | |
| `linkage_terms` | `{"기준 지표": "", "조정 요건(변동률)": "", "조정 주기": "", "조정 산식": ""}` | 연동 조항에서 협의해 채울 칸 |
| `delivery_notes` | 문자열(500자 이하) 또는 null | 납품·비고 |
| `cover_letter` | 문자열(1500자 이하) | 인사말. 모델이 쓰면 숫자 검사를 거친다 |
| **`verdict_code`** | `OK` \| `CHECK_NEEDED` \| `VIOLATION` \| null | 마지막 check_offer 판정. render는 `VIOLATION`이면 문서를 만들지 않는다 |
| **`content_hash`** | `sha256:` + 16진수 64자, 또는 null | 2절. render·생성 응답이 채운다 |

## 2. 내용 해시 (mail.md 3절과 같은 계산)

```python
doc = {k: v for k, v in document.items() if k != "content_hash"}
files = sorted(({"filename": name, "content_type": ctype, "sha256": hashlib.sha256(data).hexdigest()}
                for name, ctype, data in attachments), key=lambda f: f["filename"])
payload = json.dumps({"document": doc, "attachments": files},
                     ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
content_hash = "sha256:" + hashlib.sha256(payload).hexdigest()
```

- `document`는 응답의 `document`(`OfferDocument.model_dump(mode="json")`)에서 `content_hash`만 뺀 값이다. 날짜는 `YYYY-MM-DD`, 비율은 숫자 그대로다.
- `attachments`는 **메일에 실제로 붙일 협력사 제안서 파일**(PDF·DOCX 중 고른 것)이다. 내부 브리핑은 넣지 않는다.
- 값이 하나라도 바뀌면(버전, 상태, 단가, 비고 등) 해시가 달라진다. 키 순서와 첨부 순서는 영향이 없다.
- **같은 문서는 다시 렌더해도 같은 바이트가 나온다.**
  - PDF: reportlab invariant 모드로 그리고, 생성·수정 시각을 `issued_on` 0시(UTC)로 고정한다.
  - DOCX: 문서 속성의 작성·수정·인쇄 시각과 zip 항목 시각을 `issued_on`으로 고정한다.
  - 그래서 미리보기와 발송 사이에 다시 렌더해도 해시가 같다. 테스트로 확인한다.
- Agent 테스트는 mail.md의 함수를 그대로 옮겨 와 우리 결과와 같은지 검사한다(`tests/test_offer_documents.py`).

## 3. API (`X-Internal-Api-Key` 인증, 오류는 다른 내부 API와 같은 형식)

### 생성: `POST /internal/offer-proposals`

요청은 [offer-proposal.md](offer-proposal.md)의 `OfferProposalRequest`와 같다.

- `?format=json`(기본): `{request_id, offer_document, check: {verdict_code, warnings}, internal_briefing}`
  - `offer_document.content_hash`는 PDF·DOCX 두 첨부로 계산한 값이다
  - `internal_briefing`은 협력사에 보내지 않는다
- `?format=pdf` / `?format=docx`: 제안서 파일 하나 (헤더는 아래 render와 같다)
- `?format=briefing_pdf`: 내부 브리핑 PDF (모든 쪽에 "대외비 — 협력사 발송 금지")
- 협상안을 만들 수 없으면 JSON은 `offer_document: null`과 이유를 돌려주고, 파일 형식 요청은 422 `NO_OFFER`

### 편집 후 렌더링: `POST /internal/offer-proposals/render`

```json
{"request_id": "uuid", "document": { "...": "OfferDocument (content_hash는 있어도 무시하고 다시 계산)" }, "files": "both"}
```

- `files`: `pdf` \| `docx` \| `both`(기본). 메일에 붙일 첨부를 고르고, `content_hash`도 이 파일들로 계산한다.
- 고친 조건으로 `check_offer`를 다시 부른다(같은 `as_of`).
  - violation이 있으면 **422 `OFFER_VIOLATION`**, 본문에 `findings`(code, message, law_refs). 제안서를 만들지 않는다
  - warning은 통과시키고 `check.warnings`에만 담는다(문서와 첨부에는 넣지 않는다)
- `?format=json`(기본) 응답:

```json
{
  "request_id": "uuid",
  "document": {"schema_version": "1", "document_id": "NP-RFQ-2026-004-SUP-0055-20261023", "version": 2, "status": "final",
               "reply_by": "2026-10-30", "supplier_id": "SUP-0055", "rfq_id": "RFQ-2026-004", "title": "…",
               "part_name": "구동축 플랜지", "unit_price": 4100, "...": "…",
               "verdict_code": "CHECK_NEEDED", "content_hash": "sha256:…"},
  "attachments": [
    {"filename": "제안서_RFQ-2026-004_SUP-0055_v2.pdf", "content_type": "application/pdf", "data_base64": "…"},
    {"filename": "제안서_RFQ-2026-004_SUP-0055_v2.docx",
     "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "data_base64": "…"}],
  "check": {"verdict_code": "CHECK_NEEDED", "warnings": [{"code": "BUYER_SIZE_UNKNOWN", "message": "…"}]}
}
```

화면은 이 `document`와 `attachments`를 그대로 들고 mail-mcp `preview_offer_mail` → `send_offer_mail`에 넘긴다.

- `?format=pdf` / `?format=docx`: 파일 하나를 바로 내려받는다. 헤더는 다음과 같다.
  - `X-Content-Hash`: 이 파일 하나를 첨부로 본 2절 해시
  - `X-File-Sha256`
  - `X-Document-Version`

### 첨부 파일

| 형식 | Content-Type | 파일 이름 |
| --- | --- | --- |
| 제안서 PDF | `application/pdf` | `제안서_<RFQ>_<SUP>_v<version>.pdf` |
| 제안서 DOCX | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` | `제안서_<RFQ>_<SUP>_v<version>.docx` |
| 내부 브리핑 PDF (첨부 아님) | `application/pdf` | `내부브리핑_<RFQ>_<SUP>.pdf` |

- 파일 이름에 `/`, `\`가 없고, 파일 하나가 10MB 이하다(mail.md 2절).
- 다운로드 응답의 `Content-Disposition`은 `attachment; filename="<ASCII 이름>"; filename*=UTF-8''<퍼센트 인코딩한 한글 이름>`이다.

## 4. 발송(mail-mcp, #237)

발송 규칙은 [mail.md](mail.md)를 따른다. 요약하면 다음과 같다.
- `status == "final"`인 문서만 보낸다.
- `verdict_code == "VIOLATION"`이면 거절한다.
- 받은 문서·첨부로 2절 해시를 다시 계산해 다르면 `[HASH_MISMATCH]`로 거절한다.
- 수신자는 `supplier_contacts`(#239)의 해당 협력사 담당자로만 고른다.

## 5. 발신 정보 (`sender`, #248)

- 발신 담당자는 **로그인한 회원**(Member: `name`, `email`, `department`, `job_title`, [membership.md](membership.md))이다. 협상안 요청의 선택 필드 `sender`(`name`, `department`, `job_title`, `email`, `phone?`)로 받는다.
- **백엔드 프록시(나중에 만들 `/api/offer-proposals`)는 `sender`를 클라이언트가 보낸 값이 아니라 인증된 세션의 회원 정보(`/api/auth/me`와 같은 값)로 채워야 한다.** 사용자가 다른 사람 이름이나 이메일로 제안서를 보내지 못하게 하기 위해서다. 클라이언트가 보낸 `sender`는 버린다.
- 회사명은 회원 정보에 없어서 조직 설정 `NEGOPILOT_BUYER_COMPANY`로 둔다. 비우면 `SKALA 자동차`다.
- 전화번호도 회원 정보에 없다. 요청의 `phone` → `NEGOPILOT_BUYER_PHONE` 순서로 쓰고, 둘 다 없으면 문서에서 전화 줄을 뺀다(자리 표시를 찍지 않는다).
- `sender`가 없으면(CLI, 로컬 시험) `NEGOPILOT_BUYER_*` 환경 변수를 쓰고, 비어 있으면 `[담당자]` 같은 자리 표시가 들어간다.
- 발신 정보는 `document.sender`에 들어가므로 `content_hash`에 포함된다. 담당자가 바뀌면 해시도 바뀐다.
- 엑셀 일괄 입력은 `sender` 칸을 받지 않는다. 일괄 처리도 백엔드가 세션 회원으로 채운다.

## 6. 변경 절차

필드나 해시를 바꾸면 이 문서, mail.md, `document.py`, 테스트를 한 PR에서 같이 고치고, 뜻이 바뀌면 `schema_version`을 올립니다. mail-mcp 담당(기탁) 승인이 필요합니다.
