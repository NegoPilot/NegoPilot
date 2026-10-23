"""발송 요청 식별자의 호환성과 중복 차단을 임시 SQLite로 검사한다."""

import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock

import pytest
from fastmcp.exceptions import ToolError

import offer_mail as om
from tests.test_offer_mail import attachments, call, document, mailer, tools


class CountingTransport:
    dry_run = False

    def __init__(self):
        self.messages = []
        self.lock = Lock()

    def send(self, message):
        with self.lock:
            self.messages.append(message)


def test_existing_log_migrates_and_preserves_records(tmp_path, mailer):
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE sends (
            send_id TEXT PRIMARY KEY, nonce TEXT NOT NULL UNIQUE, document_id TEXT NOT NULL,
            version TEXT NOT NULL, content_hash TEXT NOT NULL, supplier_id TEXT NOT NULL,
            contact_id INTEGER NOT NULL, recipient TEXT NOT NULL, subject TEXT NOT NULL,
            requested_by TEXT NOT NULL, status TEXT NOT NULL, dry_run INTEGER NOT NULL,
            message_id TEXT, error TEXT, created_at TEXT NOT NULL)""")
        conn.execute("INSERT INTO sends VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            "old-send", "old-token", "OFR-2026-0001", "1", "sha256:old", "SUP-0060", 7,
            "sales@onsae.example.test", "기존 발송", "user-42", "SENT", 0, "old-message", None,
            "2026-10-22T00:00:00+00:00"))

    mailer.log = om.SendLog(path)
    [old] = mailer.log.history("OFR-2026-0001")
    assert old["send_id"] == "old-send" and old["client_ref"] is None
    assert old["status"] == "SENT" and old["message_id"] == "old-message"
    mailer.log = om.SendLog(path)
    doc = document()
    token = mailer.preview(doc, attachments())["confirmation_token"]
    sent = mailer.send(doc, token, "user-42", attachments(), client_ref="new-request")
    [new] = mailer.log.history(doc["document_id"], "new-request")
    assert new["send_id"] == sent["send_id"]
    with sqlite3.connect(path) as conn:
        index = next(row for row in conn.execute("PRAGMA index_list(sends)") if row[1] == "sends_client_ref")
    assert index[2] == 1 and index[4] == 1


def test_duplicate_client_ref_sends_only_once(mailer):
    mailer.transport = CountingTransport()
    doc = document()
    first_token = mailer.preview(doc, attachments())["confirmation_token"]
    first = mailer.send(doc, first_token, "user-42", attachments(), client_ref="request-42")
    second_token = mailer.preview(doc, attachments())["confirmation_token"]
    with pytest.raises(om.MailError, match=first["send_id"]) as exc:
        mailer.send(doc, second_token, "user-42", attachments(), client_ref="request-42")
    assert exc.value.code == "CLIENT_REF_USED"
    assert len(mailer.transport.messages) == 1
    assert len(mailer.log.history(doc["document_id"])) == 1


def test_concurrent_client_ref_reservation_sends_only_once(mailer):
    mailer.transport = CountingTransport()
    doc = document()
    tokens = [mailer.preview(doc, attachments())["confirmation_token"] for _ in range(2)]
    barrier = Barrier(2)

    def send(token):
        barrier.wait(timeout=5)
        try:
            return mailer.send(doc, token, "user-42", attachments(), client_ref="concurrent")
        except om.MailError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(send, tokens))
    [sent] = [result for result in results if isinstance(result, dict)]
    [error] = [result for result in results if isinstance(result, om.MailError)]
    assert error.code == "CLIENT_REF_USED" and sent["send_id"] in str(error)
    assert len(mailer.transport.messages) == 1
    assert len(mailer.log.history(doc["document_id"], "concurrent")) == 1


@pytest.mark.parametrize("bad", ["", "a" * 65, "공백", "has space", "a/b", "a.b", "valid\n", 42])
def test_invalid_client_ref_rejected_without_send(mailer, bad):
    mailer.transport = CountingTransport()
    doc = document()
    token = mailer.preview(doc, attachments())["confirmation_token"]
    with pytest.raises(om.MailError) as exc:
        mailer.send(doc, token, "user-42", attachments(), client_ref=bad)
    assert exc.value.code == "INVALID_CLIENT_REF"
    assert not mailer.transport.messages and not mailer.log.history(doc["document_id"])


@pytest.mark.parametrize("token, expected", [(None, "CONFIRMATION_REQUIRED"), ("forged", "CONFIRMATION_INVALID")])
def test_token_validation_precedes_client_ref_lookup(mailer, token, expected):
    doc = document()
    valid = mailer.preview(doc, attachments())["confirmation_token"]
    mailer.send(doc, valid, "user-42", attachments(), client_ref="already-used")
    with pytest.raises(om.MailError) as exc:
        mailer.send(doc, token, "user-42", attachments(), client_ref="already-used")
    assert exc.value.code == expected


def test_client_ref_filter_and_omitted_argument_compatibility(mailer):
    doc = document()
    expected = []
    for ref in [None, None, "a", "A_" + "x" * 61 + "-"]:
        token = mailer.preview(doc, attachments())["confirmation_token"]
        kwargs = {} if ref is None else {"client_ref": ref}
        expected.append(mailer.send(doc, token, "user-42", attachments(), **kwargs)["send_id"])
    rows = mailer.log.history(doc["document_id"])
    assert [row["send_id"] for row in rows] == expected
    assert [row["client_ref"] for row in rows[:2]] == [None, None]
    [filtered] = mailer.log.history(doc["document_id"], "a")
    assert filtered["send_id"] == expected[2] and filtered["client_ref"] == "a"
    assert mailer.log.history(doc["document_id"], "missing") == []
    assert mailer.log.history("another-document", "a") == []


def test_tools_pass_client_ref_and_return_duplicate_error(tools):
    doc = document()
    preview = asyncio.run(call("preview_offer_mail", {"document": doc, "attachments": attachments()}))
    arguments = {"document": doc, "attachments": attachments(), "requested_by": "u1",
                 "confirmation_token": preview["confirmation_token"], "client_ref": "backend-request"}
    sent = asyncio.run(call("send_offer_mail", arguments))
    [record] = asyncio.run(call("list_offer_mail_sends", {"document_id": doc["document_id"],
                                                        "client_ref": "backend-request"}))
    assert record["client_ref"] == "backend-request" and record["send_id"] == sent["send_id"]
    with pytest.raises(ToolError, match=r"^\[CLIENT_REF_USED\].*" + sent["send_id"]):
        asyncio.run(call("send_offer_mail", arguments))
