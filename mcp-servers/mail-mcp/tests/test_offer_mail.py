"""미리보기 → 확인 토큰 → 발송 흐름과 거절 사유 (DB 없이, dry-run)."""

import asyncio
import base64
import copy
import smtplib

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

import offer_mail as om
import server

SECRET = "test-secret-" + "x" * 32
PDF = base64.b64encode(b"%PDF-1.7 proposal").decode()


class FakeContacts:
    def __init__(self):
        self.rows = {("SUP-0060", None): om.Contact(7, "SUP-0060", "온새프레스", "김영업", "sales@onsae.example.test"),
                     ("SUP-0060", 8): om.Contact(8, "SUP-0060", "온새프레스", "이구매", "buy@onsae.example.test")}

    def find(self, supplier_id, contact_id):
        try:
            return self.rows[(supplier_id, contact_id)]
        except KeyError:
            raise om.MailError("RECIPIENT_MISSING", "담당자 없음") from None


class FailingTransport:
    dry_run = False

    def send(self, msg):
        raise smtplib.SMTPRecipientsRefused({})


def attachments():
    return [{"filename": "제안서.pdf", "content_type": "application/pdf", "data_base64": PDF}]


def document(**updates):
    doc = {"document_id": "OFR-2026-0001", "version": 2, "status": "final", "supplier_id": "SUP-0060",
           "rfq_id": "RFQ-2026-002", "part_name": "시트 레일 보강판", "title": "단가 및 거래조건 제안서",
           "reply_by": "2026-11-06", "terms": {"unit_price": 1948, "payment_days": 60, "price_linkage": True}}
    doc.update(updates)
    files = om.decode_attachments(attachments())
    doc["content_hash"] = om.content_hash(doc, files)
    return doc


@pytest.fixture
def mailer(tmp_path):
    return om.OfferMailer(FakeContacts(), om.Confirmations(SECRET), om.SendLog(tmp_path / "log.sqlite3"),
                          om.DryRunTransport(), om.Settings("purchasing@buyer.example.test", "바이어 구매팀",
                                                            "바이어", "reply@buyer.example.test"))


def code(excinfo):
    return excinfo.value.code


# ---------- 정상 흐름 ----------

def test_preview_then_send_with_token_dry_run(mailer):
    doc = document()
    preview = mailer.preview(doc, attachments(), message="일정 협의도 함께 부탁드립니다.")
    assert preview["to"]["email"] == "sales@onsae.example.test" and preview["dry_run"]
    assert preview["subject"] == "[바이어] RFQ-2026-002 시트 레일 보강판 건 단가 및 거래조건 제안서"
    assert "일정 협의도 함께" in preview["body"] and "2026-11-06까지" in preview["body"]
    assert "1948" not in preview["body"] and "1,948" not in preview["body"]   # 숫자 조건은 첨부에만

    sent = mailer.send(doc, preview["confirmation_token"], "user-42", attachments(),
                       message="일정 협의도 함께 부탁드립니다.")
    assert sent["status"] == "DRY_RUN" and sent["mail"]["attachments"] == ["제안서.pdf"]
    assert sent["mail"]["reply_to"] == "reply@buyer.example.test"
    [log] = mailer.log.history("OFR-2026-0001")
    assert log["requested_by"] == "user-42" and log["version"] == "2" and log["status"] == "DRY_RUN"
    assert log["recipient"] == "sa***@onsae.example.test" and log["content_hash"] == doc["content_hash"]


SENDER = {"company": "한빛모터스", "department": "구매1팀", "contact_name": "박구매", "phone": "02-000-0000",
          "email": "buy@hanbit.example.test"}


def test_document_sender_and_title_from_render_api(mailer):
    # #236 렌더 결과: 제목에 RFQ·품목이 이미 있고 발신 정보가 문서에 있다
    doc = document(title="RFQ-2026-002 시트 레일 보강판 단가 및 거래조건 제안", sender=SENDER)
    preview = mailer.preview(doc, attachments())
    assert preview["subject"] == "[한빛모터스] RFQ-2026-002 시트 레일 보강판 단가 및 거래조건 제안"
    assert preview["body"].count("RFQ-2026-002") == 1
    assert "안녕하십니까. 한빛모터스 구매1팀입니다." in preview["body"]
    assert "한빛모터스 구매1팀 박구매\n02-000-0000 · buy@hanbit.example.test" in preview["body"]


@pytest.mark.parametrize("field", ["company", "email"])
def test_placeholder_sender_is_rejected(mailer, field):
    with pytest.raises(om.MailError) as e:
        mailer.preview(document(sender=SENDER | {field: "[구매사명]"}), attachments())
    assert code(e) == "SENDER_INCOMPLETE"


def test_non_primary_contact_can_be_chosen(mailer):
    preview = mailer.preview(document(), attachments(), contact_id=8)
    assert preview["to"]["email"] == "buy@onsae.example.test"


def test_hash_ignores_key_order_and_attachment_order():
    files = om.decode_attachments(attachments() + [{"filename": "a.docx", "data_base64": PDF,
        "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}])
    doc = {"b": 1, "a": {"y": "한글", "x": 2}}
    assert om.content_hash(doc, files) == om.content_hash({"a": {"x": 2, "y": "한글"}, "b": 1}, files[::-1])


# ---------- 거절 ----------

def test_draft_is_rejected(mailer):
    with pytest.raises(om.MailError) as e:
        mailer.preview(document(status="draft"), attachments())
    assert code(e) == "DOCUMENT_NOT_FINAL"


def test_violation_is_rejected(mailer):
    with pytest.raises(om.MailError) as e:
        mailer.preview(document(verdict_code="VIOLATION"), attachments())
    assert code(e) == "OFFER_VIOLATION"


def test_check_needed_is_allowed(mailer):
    # 팀 결정: 확인 필요는 화면에서 경고를 확인한 뒤 보낼 수 있다
    assert mailer.preview(document(verdict_code="CHECK_NEEDED"), attachments())["confirmation_token"]


@pytest.mark.parametrize("tamper", ["document", "attachment"])
def test_changed_content_fails_hash(mailer, tamper):
    doc, files = document(), attachments()
    if tamper == "document":
        doc["terms"]["unit_price"] = 1500
    else:
        files[0]["data_base64"] = base64.b64encode(b"%PDF-1.7 other").decode()
    with pytest.raises(om.MailError) as e:
        mailer.preview(doc, files)
    assert code(e) == "HASH_MISMATCH"


def test_send_without_token_is_rejected(mailer):
    with pytest.raises(om.MailError) as e:
        mailer.send(document(), None, "user-42", attachments())
    assert code(e) == "CONFIRMATION_REQUIRED"


def test_forged_token_is_rejected(mailer):
    token = mailer.preview(document(), attachments())["confirmation_token"]
    body, sig = token.split(".")
    with pytest.raises(om.MailError) as e:
        mailer.send(document(), f"{body}.{sig[:-2]}AA", "user-42", attachments())
    assert code(e) == "CONFIRMATION_INVALID"


def test_token_from_other_secret_is_rejected(mailer):
    other = om.Confirmations("other-" + "y" * 40)
    token, _ = other.issue({"document_id": "OFR-2026-0001"})
    with pytest.raises(om.MailError) as e:
        mailer.send(document(), token, "user-42", attachments())
    assert code(e) == "CONFIRMATION_INVALID"


@pytest.mark.parametrize("change", ["message", "contact", "version"])
def test_anything_changed_after_preview_is_rejected(mailer, change):
    doc = document()
    token = mailer.preview(doc, attachments(), message="원래 문구")["confirmation_token"]
    kwargs = {"message": "원래 문구"}
    if change == "message":
        kwargs["message"] = "바뀐 문구"
    elif change == "contact":
        kwargs["contact_id"] = 8
    else:
        doc = document(version=3)
    with pytest.raises(om.MailError) as e:
        mailer.send(doc, token, "user-42", attachments(), **kwargs)
    assert code(e) == "CONFIRMATION_MISMATCH"


def test_expired_token_is_rejected(mailer):
    claims = {"document_id": "x"}
    token, exp = mailer.confirmations.issue(claims, now=1000)
    with pytest.raises(om.MailError) as e:
        mailer.confirmations.verify(token, claims, now=exp + 1)
    assert code(e) == "CONFIRMATION_EXPIRED"


def test_token_is_single_use(mailer):
    doc = document()
    token = mailer.preview(doc, attachments())["confirmation_token"]
    mailer.send(doc, token, "user-42", attachments())
    with pytest.raises(om.MailError) as e:
        mailer.send(doc, token, "user-42", attachments())
    assert code(e) == "ALREADY_SENT"
    assert len(mailer.log.history("OFR-2026-0001")) == 1


def test_missing_recipient(mailer):
    with pytest.raises(om.MailError) as e:
        mailer.preview(document(supplier_id="SUP-0001"), attachments())
    assert code(e) == "RECIPIENT_MISSING"


def test_requester_required(mailer):
    token = mailer.preview(document(), attachments())["confirmation_token"]
    with pytest.raises(om.MailError) as e:
        mailer.send(document(), token, "  ", attachments())
    assert code(e) == "REQUESTER_REQUIRED"


def test_smtp_failure_is_recorded(tmp_path):
    m = om.OfferMailer(FakeContacts(), om.Confirmations(SECRET), om.SendLog(tmp_path / "l.sqlite3"),
                       FailingTransport(), om.Settings("a@b.example.test", "구매팀", "바이어", None))
    doc = document()
    token = m.preview(doc, attachments())["confirmation_token"]
    with pytest.raises(om.MailError) as e:
        m.send(doc, token, "user-42", attachments())
    assert code(e) == "MAIL_SEND_FAILED"
    [log] = m.log.history("OFR-2026-0001")
    assert log["status"] == "FAILED" and log["error"] == "SMTPRecipientsRefused"


@pytest.mark.parametrize("bad", [
    {"filename": "x.exe", "content_type": "application/x-msdownload", "data_base64": PDF},
    {"filename": "../x.pdf", "content_type": "application/pdf", "data_base64": PDF},
    {"filename": "x.pdf", "content_type": "application/pdf", "data_base64": "not base64!"},
    {"filename": "x.pdf", "content_type": "application/pdf"},
])
def test_bad_attachment(mailer, bad):
    with pytest.raises(om.MailError) as e:
        mailer.preview(document(), [bad])
    assert code(e) == "INVALID_ATTACHMENT"


def test_missing_required_field(mailer):
    doc = document()
    del doc["supplier_id"]
    with pytest.raises(om.MailError) as e:
        mailer.preview(doc, attachments())
    assert code(e) == "INVALID_DOCUMENT"


def test_short_secret_is_refused():
    with pytest.raises(ValueError):
        om.Confirmations("short")


def test_built_message_has_attachment_and_headers(mailer):
    contact = FakeContacts().find("SUP-0060", None)
    msg = om.build_message("제목", "본문", contact, mailer.settings, om.decode_attachments(attachments()))
    assert msg["To"].endswith("<sales@onsae.example.test>") and msg["Reply-To"] == "reply@buyer.example.test"
    [part] = list(msg.iter_attachments())
    assert part.get_filename() == "제안서.pdf" and part.get_content() == b"%PDF-1.7 proposal"


# ---------- MCP 도구 ----------

@pytest.fixture
def tools(mailer, monkeypatch):
    monkeypatch.setattr(server, "mailer", lambda: mailer)


async def call(tool, args):
    async with Client(server.mcp) as client:
        result = await client.call_tool(tool, args)
        return result.data if result.data is not None else result.structured_content


def test_tools_preview_send_history(tools):
    doc = document()
    preview = asyncio.run(call("preview_offer_mail", {"document": doc, "attachments": attachments()}))
    sent = asyncio.run(call("send_offer_mail", {"document": doc, "attachments": attachments(), "requested_by": "u1",
                                                "confirmation_token": preview["confirmation_token"]}))
    assert sent["status"] == "DRY_RUN"
    history = asyncio.run(call("list_offer_mail_sends", {"document_id": "OFR-2026-0001"}))
    assert [h["requested_by"] for h in history] == ["u1"]


def test_tool_errors_carry_codes(tools):
    with pytest.raises(ToolError, match=r"^\[DOCUMENT_NOT_FINAL\]"):
        asyncio.run(call("preview_offer_mail", {"document": document(status="draft"), "attachments": attachments()}))


def test_tool_annotations():
    async def go():
        async with Client(server.mcp) as client:
            return {t.name: t for t in await client.list_tools()}
    tools = asyncio.run(go())
    assert tools["preview_offer_mail"].annotations.read_only_hint
    assert tools["list_offer_mail_sends"].annotations.read_only_hint
    assert not tools["send_offer_mail"].annotations.read_only_hint


def test_unconfigured_server_refuses(monkeypatch):
    server.mailer.cache_clear()
    monkeypatch.delenv("MAIL_CONFIRM_SECRET", raising=False)
    with pytest.raises(ToolError, match="MAIL_NOT_CONFIGURED"):
        server.mailer()
    server.mailer.cache_clear()


# ---------- 회신 주소 (Reply-To) ----------

def test_reply_to_is_the_document_sender(mailer):
    doc = document(sender=SENDER)
    preview = mailer.preview(doc, attachments())
    assert preview["reply_to"] == "박구매 <buy@hanbit.example.test>"
    assert preview["from"].endswith("<purchasing@buyer.example.test>")        # From은 그대로 공용 주소
    sent = mailer.send(doc, preview["confirmation_token"], "user-42", attachments())
    assert sent["mail"]["reply_to"] == "박구매 <buy@hanbit.example.test>"


def test_reply_to_falls_back_without_a_valid_sender_email(mailer):
    assert mailer.preview(document(), attachments())["reply_to"] == "reply@buyer.example.test"
    bad = document(sender=SENDER | {"email": "not-an-email\r\nBcc: x@evil.test"})
    assert mailer.preview(bad, attachments())["reply_to"] == "reply@buyer.example.test"
    no_env = om.OfferMailer(FakeContacts(), mailer.confirmations, mailer.log, om.DryRunTransport(),
                            om.Settings("purchasing@buyer.example.test", "바이어 구매팀", "바이어", None))
    preview = no_env.preview(document(), attachments())
    assert preview["reply_to"] is None
    sent = no_env.send(document(), preview["confirmation_token"], "user-42", attachments())
    assert sent["mail"]["reply_to"] is None


def test_changing_sender_email_invalidates_the_token(mailer):
    preview = mailer.preview(document(sender=SENDER), attachments())
    other = document(sender=SENDER | {"email": "attacker@evil.example.test"})
    with pytest.raises(om.MailError):
        mailer.send(other, preview["confirmation_token"], "user-42", attachments())


def test_built_message_uses_given_reply_to(mailer):
    contact = FakeContacts().find("SUP-0060", None)
    msg = om.build_message("제목", "본문", contact, mailer.settings, om.decode_attachments(attachments()),
                           "박구매 <buy@hanbit.example.test>")
    assert msg["Reply-To"].addresses[0].addr_spec == "buy@hanbit.example.test"
    assert b"buy@hanbit.example.test" in msg.as_bytes()
