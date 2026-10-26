# mail-mcp

담당자가 확정한 **협력사 발송용 제안서**를 협력사 담당자에게 메일로 보내는 MCP Server입니다 (#237).
사람이 미리보기로 확인한 확정본만, 그 내용 그대로 보냅니다. 챗봇은 이 서버를 스스로 부르지 않습니다.
형식과 오류 코드는 [메일 계약](../../docs/contracts/mail.md)에 있습니다.

## 도구

| 이름 | 설명 |
| --- | --- |
| `preview_offer_mail(document, attachments?, contact_id?, message?)` | 확정본·해시 확인, 수신자 조회, 메일 작성. **보내지 않고** 미리보기와 확인 토큰(15분, 1회)을 준다 |
| `send_offer_mail(document, confirmation_token, requested_by, attachments?, contact_id?, message?)` | 미리보기와 같은 입력 + 토큰으로 발송하고 기록한다 |
| `list_offer_mail_sends(document_id)` | 발송 기록 (수신 주소 일부 가림) |

- 확정본(`status=final`)만, 다시 계산한 내용 해시가 같을 때만, 미리보기 뒤 아무것도 바뀌지 않았을 때만 보낸다
- 법규 검사 `VIOLATION`은 거절한다. `CHECK_NEEDED`는 화면에서 경고를 확인한 뒤 보낸다
- 수신자는 business DB `supplier_contacts`에서만 고른다. 주소를 직접 받지 않는다
- 메일 본문에는 숫자 조건을 쓰지 않는다. 조건은 첨부 제안서에만 있다
- 모델을 부르지 않는다

## 실행

```bash
uv sync
export MAIL_CONFIRM_SECRET='32자 이상 비밀값'
export DATABASE_URL='postgresql://negopilot_mail_reader:...@localhost:5432/negopilot_business'
uv run python server.py --http 9104   # http://localhost:9104/mcp
uv run python -m pytest -q            # DB 테스트는 DATABASE_URL에 연결할 수 없으면 건너뜀
```

## 환경 변수

| 이름 | 기본 | 설명 |
| --- | --- | --- |
| `MAIL_CONFIRM_SECRET` | (필수) | 확인 토큰 서명 키, 32자 이상 |
| `MAIL_TRANSPORT` | `dry_run` | `dry_run`: 보내지 않고 보낼 메일을 돌려준다. `smtp`: 실제 발송 |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_SECURITY` | -, 587, `starttls` | `starttls`(587), `ssl`(465), `none`(로컬 Mailpit 1025) |
| `SMTP_USERNAME`, `SMTP_PASSWORD` | | 비밀 설정으로 주입한다. 저장소에 넣지 않는다 |
| `MAIL_FROM`, `MAIL_FROM_NAME`, `MAIL_BUYER_NAME`, `MAIL_REPLY_TO` | | 보내는 주소·이름, 제목의 회사명, 기본 회신 주소 (문서 `sender.email`이 있으면 그 주소를 Reply-To로 쓴다) |
| `MAIL_CONFIRM_TTL_SECONDS` | 900 | 토큰 유효 시간 |
| `MAIL_RUNTIME_DIR` | `./runtime` | 발송 기록 SQLite 위치 (Compose는 `mail-data` 볼륨) |
| `DATABASE_URL` | `dbname=negopilot_business` | 담당자 조회. Compose는 `negopilot_mail_reader` 계정 |

## 로컬에서 실제 SMTP로 확인

Mailpit은 받은 메일을 배달하지 않고 웹 화면에 보여 주는 테스트용 SMTP 서버입니다.

```bash
docker compose --profile mailpit up -d mailpit
MAIL_TRANSPORT=smtp SMTP_HOST=mailpit SMTP_PORT=1025 SMTP_SECURITY=none docker compose up -d mail-mcp
# 받은 메일: http://localhost:8025
```

## 폴더 구조

```
server.py         MCP 도구
offer_mail.py     해시·확인 토큰·수신자 조회·메일 작성·발송·기록
tests/            dry-run 흐름·거절 사유(DB 없이), 담당자 조회(PostgreSQL)
```
