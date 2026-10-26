# 협상 제안서 메일 계약 (#237)

담당자가 확정한 **협력사 발송용 제안서**(#236 `OfferDocument`)를 협력사 담당자에게 메일로 보내는 mail-mcp의 형식입니다.
내부 협상 브리핑(목표 합의가·최대 수용가·양보 순서 등)은 보내지 않습니다. 제안서 문서·렌더링은 #236, 발송은 이 문서입니다.

| 파일 | 내용 |
| --- | --- |
| [`mcp-servers/mail-mcp/server.py`](../../mcp-servers/mail-mcp/server.py) | MCP 도구 3개 |
| [`mcp-servers/mail-mcp/offer_mail.py`](../../mcp-servers/mail-mcp/offer_mail.py) | 해시·토큰·수신자·작성·발송·기록 |
| [`backend/.../db/business/V2__supplier_contacts.sql`](../../backend/src/main/resources/db/business/V2__supplier_contacts.sql) | 수신자 테이블 |

## 1. 흐름

```text
제안서 렌더(#236, status=final + content_hash + PDF/DOCX)
  → preview_offer_mail: 확정본·해시 확인, 수신자 조회, 메일 작성 → 미리보기 + 확인 토큰
  → 사람이 화면에서 수신자·제목·본문·첨부를 확인 (CHECK_NEEDED면 경고 확인 체크)
  → send_offer_mail: 같은 입력 + 확인 토큰 + 승인자 → 발송, 기록
```

- **챗봇(Agent)은 이 도구를 스스로 부르지 않는다.** intents에 연결하지 않고, 화면의 발송 버튼에서만 부른다.
- 법규 판정: `VIOLATION`은 보내지 않는다(`[OFFER_VIOLATION]`). `CHECK_NEEDED`는 화면에서 경고를 보여 주고 사용자가 확인한 뒤 보낸다 (팀 결정 2026-10-23). 경고 확인 여부는 화면·백엔드가 기록한다.

## 2. 제안서 입력 (`document`)

mail-mcp가 읽는 필드만 정한다. 나머지 필드는 그대로 해시에 들어간다.

| 필드 | 필수 | 뜻 |
| --- | --- | --- |
| `document_id` | O | 제안서 번호 (예: `OFR-2026-0001`) |
| `version` | O | 버전. 고칠 때마다 바뀐다 |
| `status` | O | `final`만 보낸다. `draft`는 `[DOCUMENT_NOT_FINAL]` |
| `supplier_id` | O | 수신 협력사. 수신자는 이 협력사의 담당자에서만 고른다 |
| `rfq_id` | O | 제목·본문에 쓴다 |
| `content_hash` | O | 아래 3의 해시 (`sha256:` + 16진수 64자) |
| `title` | | 메일 제목에 쓴다. RFQ 번호가 없으면 앞에 `RFQ 품목 건`을 붙인다. 기본 "단가 및 거래조건 제안서" |
| `sender` | | `{company, department, contact_name, phone, email}`. 인사말·서명에 쓴다. 값이 비었거나 `[구매사명]` 같은 자리표시면 `[SENDER_INCOMPLETE]`로 보내지 않는다 (#236 `NEGOPILOT_BUYER_*` 설정). `sender.email`이 올바른 주소면 메일의 **Reply-To**로 써서 협력사 회신이 그 담당자에게 간다 (From은 공용 주소 그대로) |
| `part_name` | | 품목명. 제목·본문에 쓴다 |
| `reply_by` | | 회신 기한 (`YYYY-MM-DD`). 본문에 쓴다 |
| `verdict_code` | | `VIOLATION`이면 거절 |

첨부(`attachments`)는 `[{filename, content_type, data_base64}]`, PDF·DOCX만, 5개·각 10MB 이하, 파일 이름에 경로 문자 금지.

**메일 본문에는 단가·지급 조건 같은 숫자를 쓰지 않는다.** 숫자 조건은 첨부 제안서에만 있어서 본문과 제안서가 어긋날 수 없다. 담당자가 본문에 넣을 말은 `message`(2,000자 이하)로 받는다.

## 3. 내용 해시 (`content_hash`) — #236 렌더 API와 같은 계산

```python
doc = {k: v for k, v in document.items() if k != "content_hash"}
files = sorted(({"filename": a.filename, "content_type": a.content_type,
                 "sha256": hashlib.sha256(a.data).hexdigest()} for a in attachments),
               key=lambda f: f["filename"])
payload = json.dumps({"document": doc, "attachments": files},
                     ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
content_hash = "sha256:" + hashlib.sha256(payload).hexdigest()
```

- 렌더 API가 이 값을 `document.content_hash`에 넣어 돌려준다. mail-mcp는 받은 문서·첨부로 다시 계산해 다르면 `[HASH_MISMATCH]`로 거절한다.
- 키 순서·첨부 순서는 결과에 영향이 없다. 숫자·문자열 값이 하나라도 바뀌면 해시가 바뀐다.

## 4. 도구

### `preview_offer_mail(document, attachments?, contact_id?, message?)` (조회 전용)

```json
{
  "to": {"contact_id": 7, "name": "김영업", "email": "sales.sup-0060@example.test",
         "supplier_id": "SUP-0060", "supplier_name": "온새프레스"},
  "from": "NegoPilot 구매팀 <purchasing@negopilot.local>", "reply_to": "김가연 <gayeon@skala.example.test>",
  "subject": "[NegoPilot] 단가 및 거래조건 제안서 (RFQ-2026-002 시트 레일 보강판)",
  "body": "온새프레스 김영업님께\n\n…",
  "attachments": [{"filename": "제안서.pdf", "content_type": "application/pdf", "bytes": 52311, "sha256": "…"}],
  "content_hash": "sha256:…", "dry_run": true,
  "confirmation_token": "…", "expires_at": "2026-10-23T08:15:00+00:00"
}
```

- `contact_id`를 생략하면 그 협력사의 활성 기본 담당자. 다른 협력사의 담당자 ID는 받지 않는다.
- `reply_to`: 문서 `sender.email`(담당자 이름 포함). 없거나 형식이 틀리면 `MAIL_REPLY_TO`, 그것도 없으면 `null`(Reply-To 헤더 없음). `sender`는 해시에 들어가므로 토큰이 회신 주소까지 묶는다.
- 확인 토큰은 문서 번호·버전·해시·수신자·제목·본문에 서명한 값이다 (HMAC-SHA256, `MAIL_CONFIRM_SECRET`). 15분 동안 유효하고 한 번만 쓴다.

### `send_offer_mail(document, confirmation_token, requested_by, attachments?, contact_id?, message?, client_ref?)`

미리보기와 **같은 입력**을 다시 넘긴다. 성공하면:

```json
{"send_id": "…", "status": "SENT", "dry_run": false, "message_id": "<…@negopilot.local>",
 "to": "sa***@example.test", "subject": "…", "document_id": "OFR-2026-0001", "version": "2",
 "content_hash": "sha256:…", "sent_at": "2026-10-23T08:01:02+00:00"}
```

`MAIL_TRANSPORT=dry_run`이면 `status: DRY_RUN`이고 `mail`(보낼 헤더·본문·첨부 이름)을 함께 돌려준다.

`client_ref`는 호출자가 정한 발송 요청 식별자다. 생략할 수 있다. 값은 1~64자이며 `^[A-Za-z0-9_-]+$` 형식이어야 한다. 형식이 다르면 `INVALID_CLIENT_REF`로 거절한다.

확인 토큰을 검증한 뒤 발송을 예약할 때 `client_ref`를 함께 저장한다. 값이 있는 요청에는 부분 고유 인덱스를 적용한다. 같은 값이 이미 있으면 `CLIENT_REF_USED`로 거절하고 오류 메시지에 기존 `send_id`를 넣는다. 이 요청은 SMTP를 호출하지 않는다. 실패한 발송 기록도 같은 식별자를 다시 쓸 수 없다. 식별자를 생략한 기존 호출은 기존 토큰 중복 검사대로 동작한다.

### `list_offer_mail_sends(document_id, client_ref?)` (조회 전용)

발송 기록: `send_id, version, content_hash, supplier_id, contact_id, recipient(일부 가림), subject, requested_by, status(SENDING/SENT/DRY_RUN/FAILED), dry_run, message_id, error, created_at, client_ref`.

`client_ref`를 넘기면 해당 문서에서 그 값과 정확히 일치하는 기록만 반환한다. 생략하면 해당 문서의 모든 발송 기록을 반환한다. 기존 기록과 식별자를 생략한 새 기록의 `client_ref`는 `null`이다. 시작 시 기존 SQLite 테이블에 열이 없으면 `ALTER TABLE`로 추가하며 기존 기록을 유지한다.

## 5. 오류 코드

| 코드 | 언제 |
| --- | --- |
| `INVALID_DOCUMENT` | 필수 필드 없음 |
| `DOCUMENT_NOT_FINAL` | status가 final이 아님 |
| `OFFER_VIOLATION` | verdict_code가 VIOLATION |
| `HASH_MISMATCH` | 다시 계산한 해시가 content_hash와 다름 |
| `SENDER_INCOMPLETE` | 제안서 발신 정보가 비었거나 자리표시 |
| `INVALID_ATTACHMENT` | 형식·크기·이름·개수 오류 |
| `INVALID_MESSAGE` | 추가 문구가 2,000자 초과 |
| `INVALID_CLIENT_REF` | 발송 요청 식별자가 1~64자의 영문자와 숫자 및 `_` 또는 `-` 형식이 아님 |
| `CLIENT_REF_USED` | 같은 발송 요청 식별자가 이미 있음. 메시지에 기존 `send_id` 포함 |
| `RECIPIENT_MISSING` | 활성 담당자(기본 또는 지정)가 없음 |
| `CONFIRMATION_REQUIRED` | 토큰 없음 |
| `CONFIRMATION_INVALID` | 서명이 맞지 않음 |
| `CONFIRMATION_EXPIRED` | 15분 지남 |
| `CONFIRMATION_MISMATCH` | 미리보기 이후 문서·첨부·수신자·문구가 바뀜 |
| `ALREADY_SENT` | 이미 쓴 토큰 |
| `REQUESTER_REQUIRED` | 승인자 없음 |
| `MAIL_SEND_FAILED` | SMTP 거절·연결 실패 (기록에 FAILED). 다시 보내려면 새로 미리보기 |
| `DB_UNAVAILABLE` | 담당자 DB 연결 실패 |
| `MAIL_NOT_CONFIGURED` | `MAIL_CONFIRM_SECRET` 없음 |

## 6. 수신자 (`supplier_contacts`)

business DB V2. 협력사마다 담당자 여러 명, 활성 기본 담당자 1명. mail-mcp는 전용 읽기 계정(`negopilot_mail_reader`)으로 `suppliers`, `supplier_contacts`만 읽는다. business MCP 계정에는 담당자 이메일 권한이 없다(모델이 보는 결과에 개인 연락처가 섞이지 않게). 로컬 시연 담당자는 `sales.sup-0001@example.test` 형식이다.

## 7. 아직 하지 않은 것

- 화면·백엔드 연결(미리보기 표시, CHECK_NEEDED 경고 확인, 발송 버튼). 화면 담당과 정한다
- 관리자 화면의 담당자 관리 (#238과 맞춘다)
- 실제 발송 환경의 SMTP 계정·SPF/DKIM
