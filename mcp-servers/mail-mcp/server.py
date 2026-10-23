"""협상 제안서를 협력사 담당자에게 메일로 보내는 MCP Server (mail-mcp, #237).

사람이 미리보기로 확인한 확정본만, 그 내용 그대로 보낸다. 챗봇(Agent)이 스스로 부르지 않는다.
발송 도구는 미리보기가 돌려준 확인 토큰이 있어야 동작하고, 토큰은 한 번만 쓸 수 있다.
검사·작성·발송 로직은 offer_mail.py에 있다.

실행:
    uv run python server.py                 # stdio
    uv run python server.py --http 9104     # Streamable HTTP (http://localhost:9104/mcp)

환경 변수: README.md 참고 (MAIL_CONFIRM_SECRET 필수, 기본 MAIL_TRANSPORT=dry_run)
"""

import argparse
import os
from functools import lru_cache
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from offer_mail import (Confirmations, ContactDirectory, MailError, OfferMailer, SendLog, Settings,
                        transport_from_env)

mcp = FastMCP("mail-mcp")


@lru_cache(maxsize=1)
def mailer() -> OfferMailer:
    secret = os.getenv("MAIL_CONFIRM_SECRET", "")
    if not secret:
        raise ToolError("[MAIL_NOT_CONFIGURED] MAIL_CONFIRM_SECRET이 설정되지 않았다")
    runtime = Path(os.getenv("MAIL_RUNTIME_DIR", Path(__file__).resolve().parent / "runtime"))
    return OfferMailer(contacts=ContactDirectory(os.getenv("DATABASE_URL", "dbname=negopilot_business")),
                       confirmations=Confirmations(secret), log=SendLog(runtime / "mail_log.sqlite3"),
                       transport=transport_from_env(), settings=Settings.from_env())


def run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except MailError as e:
        raise ToolError(str(e)) from None


@mcp.tool(annotations={"readOnlyHint": True})
def preview_offer_mail(document: dict, attachments: list[dict] | None = None, contact_id: int | None = None,
                       message: str | None = None) -> dict:
    """확정 제안서로 보낼 메일을 만들어 보여 주고 확인 토큰을 준다. 보내지 않는다.

    document: OfferDocument 확정본 (status=final, content_hash 포함. docs/contracts/mail.md)
    attachments: [{filename, content_type(PDF/DOCX), data_base64}] — 렌더 API가 만든 그대로
    contact_id: 받을 담당자. 생략하면 그 협력사의 기본 담당자
    message: 본문에 넣을 추가 문구(2,000자 이하). 숫자 조건은 첨부 제안서에만 있다
    돌려주는 confirmation_token을 send_offer_mail에 같은 입력과 함께 넘겨야 보낼 수 있다 (15분, 1회).
    """
    return run(mailer().preview, document, attachments, contact_id, message)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False,
                       "openWorldHint": True})
def send_offer_mail(document: dict, confirmation_token: str, requested_by: str,
                    attachments: list[dict] | None = None, contact_id: int | None = None,
                    message: str | None = None) -> dict:
    """사람이 미리보기를 확인한 뒤에만 부른다. 미리보기와 같은 입력 + 확인 토큰으로 메일을 보낸다.

    requested_by: 발송을 승인한 사용자 ID (발송 기록에 남는다)
    미리보기 이후 문서·첨부·수신자·추가 문구가 하나라도 바뀌면 [CONFIRMATION_MISMATCH]로 거절한다.
    MAIL_TRANSPORT=dry_run이면 실제로 보내지 않고 보낼 메일을 돌려준다.
    """
    return run(mailer().send, document, confirmation_token, requested_by, attachments, contact_id, message)


@mcp.tool(annotations={"readOnlyHint": True})
def list_offer_mail_sends(document_id: str) -> list[dict]:
    """제안서 하나의 발송 기록 (누가, 언제, 누구에게, 어느 버전). 수신 주소는 일부 가린다."""
    return run(mailer().log.history, document_id)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--http", type=int, metavar="PORT", help="Streamable HTTP로 실행할 포트 (생략하면 stdio)")
    args = parser.parse_args()
    if args.http:
        mcp.run(transport="http", host="0.0.0.0", port=args.http)
    else:
        mcp.run()
