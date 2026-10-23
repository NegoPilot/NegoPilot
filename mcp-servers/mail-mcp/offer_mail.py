"""협상 제안서 메일의 검사·작성·발송 로직. server.py의 도구가 이 모듈을 부른다.

흐름: 미리보기(preview) → 사람이 확인 → 확인 토큰으로 발송(send).
- 확정본(status=final)만 보낸다. 제안서 내용·첨부로 다시 계산한 해시가 문서의 content_hash와 같아야 한다
- 확인 토큰은 미리보기에서 보여 준 수신자·제목·본문·해시에 서명한 것이라, 그 뒤 하나라도 바뀌면 보내지 않는다
- 토큰은 한 번만 쓴다. 같은 토큰으로 두 번 보내지 않는다
- 수신자는 business DB의 supplier_contacts에서만 고른다. 주소를 직접 받지 않는다
모델은 부르지 않는다.
"""

import base64
import binascii
import re
import hashlib
import hmac
import json
import os
import secrets
import smtplib
import sqlite3
import ssl
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path

TOKEN_TTL_SECONDS = int(os.getenv("MAIL_CONFIRM_TTL_SECONDS", "900"))   # 미리보기 후 15분 안에 보내야 한다
MAX_ATTACHMENTS = 5
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_MESSAGE_CHARS = 2000
ALLOWED_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
REQUIRED_FIELDS = ("document_id", "version", "status", "supplier_id", "rfq_id", "content_hash")
PLACEHOLDER = re.compile(r"^\s*\[[^\]]*\]\s*$")   # 설정하지 않은 발신 정보 자리표시 (예: [구매사명])
CLIENT_REF = re.compile(r"[A-Za-z0-9_-]{1,64}")


class MailError(Exception):
    """오류 코드를 메시지 앞에 붙인다. 예: [HASH_MISMATCH] …"""

    def __init__(self, code: str, message: str):
        super().__init__(f"[{code}] {message}")
        self.code = code


# ---------- 제안서 확인 ----------

@dataclass(frozen=True)
class Attachment:
    filename: str
    content_type: str
    data: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


def canonical(value) -> bytes:
    """해시 계산용 JSON. 키 정렬, 공백 없음, 한글 그대로 (docs/contracts/mail.md와 같다)."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def content_hash(document: dict, attachments: list[Attachment]) -> str:
    """제안서(content_hash 키 제외)와 첨부(파일 이름·형식·내용 해시)를 함께 묶은 해시."""
    doc = {k: v for k, v in document.items() if k != "content_hash"}
    files = sorted(({"filename": a.filename, "content_type": a.content_type, "sha256": a.sha256}
                    for a in attachments), key=lambda f: f["filename"])
    return "sha256:" + hashlib.sha256(canonical({"document": doc, "attachments": files})).hexdigest()


def decode_attachments(items: list[dict] | None) -> list[Attachment]:
    out = []
    for item in items or []:
        try:
            filename, content_type = item["filename"], item["content_type"]
            data = base64.b64decode(item["data_base64"], validate=True)
        except (KeyError, TypeError, binascii.Error) as e:
            raise MailError("INVALID_ATTACHMENT", f"첨부는 filename, content_type, data_base64가 필요하다: {e}") from None
        if not isinstance(filename, str) or not filename or "/" in filename or "\\" in filename or len(filename) > 120:
            raise MailError("INVALID_ATTACHMENT", f"첨부 파일 이름이 올바르지 않다: {filename!r}")
        if content_type not in ALLOWED_TYPES:
            raise MailError("INVALID_ATTACHMENT", f"PDF와 DOCX만 첨부할 수 있다: {content_type}")
        if not data or len(data) > MAX_ATTACHMENT_BYTES:
            raise MailError("INVALID_ATTACHMENT", f"첨부 크기는 1바이트~10MB여야 한다: {filename}")
        out.append(Attachment(filename, content_type, data))
    if len(out) > MAX_ATTACHMENTS:
        raise MailError("INVALID_ATTACHMENT", f"첨부는 {MAX_ATTACHMENTS}개까지다")
    if len({a.filename for a in out}) != len(out):
        raise MailError("INVALID_ATTACHMENT", "첨부 파일 이름이 겹친다")
    return out


def check_document(document: dict, attachments: list[Attachment]) -> str:
    """보낼 수 있는 확정본인지 확인하고 다시 계산한 해시를 돌려준다."""
    if not isinstance(document, dict) or (missing := [f for f in REQUIRED_FIELDS if f not in document]):
        raise MailError("INVALID_DOCUMENT", f"제안서에 필수 항목이 없다: {missing if isinstance(document, dict) else 'object'}")
    if document["status"] != "final":
        raise MailError("DOCUMENT_NOT_FINAL", f"확정본(status=final)만 보낼 수 있다. 현재 {document['status']!r}")
    if document.get("verdict_code") == "VIOLATION":
        # 렌더 단계(#236)가 위반안을 거절하지만, 발송 직전에도 한 번 더 막는다 (팀 결정: VIOLATION 차단)
        raise MailError("OFFER_VIOLATION", "법규 검사에서 위반 소지가 있는 제안서는 보낼 수 없다")
    sender = document.get("sender")
    if isinstance(sender, dict) and (blank := [k for k, v in sender.items() if not v or PLACEHOLDER.match(str(v))]):
        # 발신 정보가 [구매사명] 같은 자리표시인 제안서는 협력사에 보내지 않는다
        raise MailError("SENDER_INCOMPLETE", f"제안서 발신 정보가 채워지지 않았다: {', '.join(blank)} (NEGOPILOT_BUYER_* 설정)")
    actual = content_hash(document, attachments)
    if not hmac.compare_digest(actual, str(document["content_hash"])):
        raise MailError("HASH_MISMATCH", "제안서나 첨부가 승인된 내용과 다르다. 다시 렌더링하고 확인해야 한다")
    return actual


# ---------- 수신자 ----------

@dataclass(frozen=True)
class Contact:
    contact_id: int
    supplier_id: str
    supplier_name: str
    name: str
    email: str


class ContactDirectory:
    """supplier_contacts 조회 (mail 전용 읽기 계정)."""

    def __init__(self, dsn: str):
        self.dsn = dsn

    def find(self, supplier_id: str, contact_id: int | None) -> Contact:
        import psycopg

        sql = ("SELECT c.contact_id, c.supplier_id, s.name, c.name, c.email FROM supplier_contacts c "
               "JOIN suppliers s USING (supplier_id) WHERE c.supplier_id = %s AND c.status = 'ACTIVE' ")
        params: tuple = (supplier_id,)
        if contact_id is None:
            sql += "AND c.is_primary"
        else:
            sql += "AND c.contact_id = %s"
            params += (contact_id,)
        try:
            with psycopg.connect(self.dsn, connect_timeout=5) as conn:
                row = conn.execute(sql, params).fetchone()
        except psycopg.OperationalError:
            raise MailError("DB_UNAVAILABLE", "담당자 DB에 연결할 수 없다") from None
        if row is None:
            what = "기본 담당자" if contact_id is None else f"담당자 {contact_id}"
            raise MailError("RECIPIENT_MISSING", f"{supplier_id}의 활성 {what} 이메일이 없다. 관리자에게 담당자 등록을 요청한다")
        return Contact(*row)


# ---------- 메일 작성 ----------

@dataclass(frozen=True)
class Settings:
    sender: str
    sender_name: str
    buyer_name: str
    reply_to: str | None

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(sender=os.getenv("MAIL_FROM", "purchasing@negopilot.local"),
                   sender_name=os.getenv("MAIL_FROM_NAME", "NegoPilot 구매팀"),
                   buyer_name=os.getenv("MAIL_BUYER_NAME", "NegoPilot"),
                   reply_to=os.getenv("MAIL_REPLY_TO") or None)


def compose(document: dict, contact: Contact, settings: Settings, message: str | None) -> tuple[str, str]:
    """제목과 본문. 숫자 조건은 본문에 쓰지 않고 첨부 제안서에만 둔다 (본문과 제안서가 어긋나지 않게)."""
    if message is not None and len(message) > MAX_MESSAGE_CHARS:
        raise MailError("INVALID_MESSAGE", f"추가 문구는 {MAX_MESSAGE_CHARS}자까지다")
    sender = document.get("sender") if isinstance(document.get("sender"), dict) else {}
    buyer = sender.get("company") or settings.buyer_name
    team = sender.get("department") or "구매팀"
    title = document.get("title") or "단가 및 거래조건 제안서"
    rfq = document["rfq_id"]
    part = f" {document['part_name']}" if document.get("part_name") else ""
    # 제안서 제목에 RFQ 번호가 이미 있으면(#236 기본 제목) 다시 붙이지 않는다
    about = title if rfq in title else f"{rfq}{part} 건 {title}"
    subject = f"[{buyer}] {about}"
    lines = [f"{contact.supplier_name} {contact.name}님께", "",
             f"안녕하십니까. {buyer} {team}입니다.",
             f"{about} 문서를 첨부와 같이 보내 드립니다."]
    if message and message.strip():
        lines += ["", message.strip()]
    if document.get("reply_by"):
        lines += ["", f"검토하신 뒤 {document['reply_by']}까지 회신 부탁드립니다."]
    else:
        lines += ["", "검토하신 뒤 회신 부탁드립니다."]
    signer = " ".join(v for v in (buyer, team, sender.get("contact_name")) if v) if sender else settings.sender_name
    contact_line = " · ".join(v for v in (sender.get("phone"), sender.get("email")) if v)
    lines += ["", "감사합니다.", signer] + ([contact_line] if contact_line else []) + ["",
              f"(제안서 번호 {document['document_id']} · 버전 {document['version']})"]
    return subject, "\n".join(lines)


def build_message(subject: str, body: str, contact: Contact, settings: Settings,
                  attachments: list[Attachment]) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((settings.sender_name, settings.sender))
    msg["To"] = formataddr((contact.name, contact.email))
    if settings.reply_to:
        msg["Reply-To"] = settings.reply_to
    msg["Message-ID"] = make_msgid(domain=settings.sender.split("@")[-1])
    msg.set_content(body)
    for a in attachments:
        maintype, subtype = a.content_type.split("/", 1)
        msg.add_attachment(a.data, maintype=maintype, subtype=subtype, filename=a.filename)
    return msg


# ---------- 확인 토큰 ----------

def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Confirmations:
    """미리보기 내용에 서명한 1회용 토큰. 비밀값은 MAIL_CONFIRM_SECRET."""

    def __init__(self, secret: str, ttl: int = TOKEN_TTL_SECONDS):
        if len(secret) < 32:
            raise ValueError("MAIL_CONFIRM_SECRET은 32자 이상이어야 한다")
        self.key = secret.encode()
        self.ttl = ttl

    def issue(self, claims: dict, now: float | None = None) -> tuple[str, int]:
        exp = int((now or time.time()) + self.ttl)
        payload = canonical({**claims, "exp": exp, "nonce": secrets.token_hex(16)})
        sig = hmac.new(self.key, payload, hashlib.sha256).digest()
        return f"{_b64(payload)}.{_b64(sig)}", exp

    def verify(self, token: str | None, expected: dict, now: float | None = None) -> dict:
        if not token:
            raise MailError("CONFIRMATION_REQUIRED", "미리보기에서 받은 확인 토큰이 필요하다")
        try:
            body, sig = token.split(".")
            payload = _unb64(body)
            good = hmac.compare_digest(_unb64(sig), hmac.new(self.key, payload, hashlib.sha256).digest())
            claims = json.loads(payload) if good else None
        except (ValueError, binascii.Error):
            claims = None
        if not claims:
            raise MailError("CONFIRMATION_INVALID", "확인 토큰이 올바르지 않다")
        if claims["exp"] < (now or time.time()):
            raise MailError("CONFIRMATION_EXPIRED", "확인 토큰이 만료됐다. 다시 미리보기한다")
        changed = [k for k, v in expected.items() if claims.get(k) != v]
        if changed:
            raise MailError("CONFIRMATION_MISMATCH", f"미리보기 이후 바뀐 항목이 있다: {', '.join(changed)}")
        return claims


# ---------- 발송 기록 ----------

class SendLog:
    """누가, 언제, 누구에게, 어느 문서 버전을 보냈는지 (SQLite, MAIL_RUNTIME_DIR)."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self._conn() as c:
            # 동시에 시작해도 열 확인과 추가가 겹치지 않는다.
            c.execute("BEGIN IMMEDIATE")
            c.execute("""CREATE TABLE IF NOT EXISTS sends (
                send_id TEXT PRIMARY KEY, nonce TEXT NOT NULL UNIQUE, document_id TEXT NOT NULL,
                version TEXT NOT NULL, content_hash TEXT NOT NULL, supplier_id TEXT NOT NULL,
                contact_id INTEGER NOT NULL, recipient TEXT NOT NULL, subject TEXT NOT NULL,
                requested_by TEXT NOT NULL, status TEXT NOT NULL, dry_run INTEGER NOT NULL,
                message_id TEXT, error TEXT, created_at TEXT NOT NULL, client_ref TEXT)""")
            if "client_ref" not in {row[1] for row in c.execute("PRAGMA table_info(sends)")}:
                c.execute("ALTER TABLE sends ADD COLUMN client_ref TEXT")
            c.execute("CREATE INDEX IF NOT EXISTS sends_document ON sends(document_id, created_at)")
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS sends_client_ref ON sends(client_ref) "
                      "WHERE client_ref IS NOT NULL")

    def _conn(self):
        return sqlite3.connect(self.path, timeout=10, isolation_level=None)

    def reserve(self, nonce: str, row: dict, client_ref: str | None = None) -> str:
        """한 번의 INSERT로 토큰과 발송 요청의 중복을 막는다."""
        send_id = str(uuid.uuid4())
        try:
            with self._conn() as c:
                c.execute("INSERT INTO sends (send_id, nonce, document_id, version, content_hash, supplier_id, "
                          "contact_id, recipient, subject, requested_by, status, dry_run, message_id, error, "
                          "created_at, client_ref) VALUES (:send_id,:nonce,:document_id,:version,:content_hash,"
                          ":supplier_id,:contact_id,:recipient,:subject,:requested_by,'SENDING',:dry_run,"
                          "NULL,NULL,:created_at,:client_ref)",
                          {**row, "send_id": send_id, "nonce": nonce, "created_at": now_iso(),
                           "client_ref": client_ref})
        except sqlite3.IntegrityError:
            if client_ref is not None:
                with self._conn() as c:
                    existing = c.execute("SELECT send_id FROM sends WHERE client_ref = ?", (client_ref,)).fetchone()
                if existing is not None:
                    raise MailError("CLIENT_REF_USED", f"이 client_ref로 이미 발송을 예약했다. send_id={existing[0]}") from None
            raise MailError("ALREADY_SENT", "이 확인 토큰으로 이미 발송했다. 다시 보내려면 새로 미리보기한다") from None
        return send_id

    def finish(self, send_id: str, status: str, message_id: str | None = None, error: str | None = None) -> None:
        with self._conn() as c:
            c.execute("UPDATE sends SET status=?, message_id=?, error=? WHERE send_id=?", (status, message_id, error, send_id))

    def history(self, document_id: str, client_ref: str | None = None) -> list[dict]:
        with self._conn() as c:
            c.row_factory = sqlite3.Row
            sql = ("SELECT send_id, document_id, version, content_hash, supplier_id, contact_id, recipient, "
                   "subject, requested_by, status, dry_run, message_id, error, created_at, client_ref FROM sends "
                   "WHERE document_id = ?")
            params: tuple = (document_id,)
            if client_ref is not None:
                sql += " AND client_ref = ?"
                params += (client_ref,)
            rows = c.execute(sql + " ORDER BY created_at", params).fetchall()
        return [{**dict(r), "dry_run": bool(r["dry_run"]), "recipient": mask_email(r["recipient"])} for r in rows]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    return (local[:2] + "***@" + domain) if domain else "***"


# ---------- 발송 ----------

class DryRunTransport:
    """실제로 보내지 않는다. 테스트와 로컬 시연용."""
    dry_run = True

    def send(self, msg: EmailMessage) -> None:
        return None


class SmtpTransport:
    dry_run = False

    def __init__(self, host: str, port: int, username: str | None, password: str | None, security: str):
        if security not in ("starttls", "ssl", "none"):
            raise ValueError("SMTP_SECURITY는 starttls, ssl, none 중 하나다")
        self.host, self.port, self.username, self.password, self.security = host, port, username, password, security

    def send(self, msg: EmailMessage) -> None:
        context = ssl.create_default_context()
        cls = smtplib.SMTP_SSL if self.security == "ssl" else smtplib.SMTP
        kwargs = {"context": context} if self.security == "ssl" else {}
        with cls(self.host, self.port, timeout=30, **kwargs) as smtp:
            if self.security == "starttls":
                smtp.starttls(context=context)
            if self.username:
                smtp.login(self.username, self.password or "")
            smtp.send_message(msg)


def transport_from_env():
    mode = os.getenv("MAIL_TRANSPORT", "dry_run")
    if mode == "dry_run":
        return DryRunTransport()
    if mode == "smtp":
        return SmtpTransport(os.environ["SMTP_HOST"], int(os.getenv("SMTP_PORT", "587")),
                             os.getenv("SMTP_USERNAME"), os.getenv("SMTP_PASSWORD"), os.getenv("SMTP_SECURITY", "starttls"))
    raise ValueError(f"MAIL_TRANSPORT는 dry_run 또는 smtp다: {mode}")


# ---------- 서비스 ----------

class OfferMailer:
    def __init__(self, contacts, confirmations: Confirmations, log: SendLog, transport, settings: Settings):
        self.contacts, self.confirmations, self.log = contacts, confirmations, log
        self.transport, self.settings = transport, settings

    def _prepare(self, document, attachments, contact_id, message):
        files = decode_attachments(attachments)
        actual = check_document(document, files)
        contact = self.contacts.find(document["supplier_id"], contact_id)
        subject, body = compose(document, contact, self.settings, message)
        claims = {"document_id": str(document["document_id"]), "version": str(document["version"]),
                  "content_hash": actual, "contact_id": contact.contact_id, "recipient": contact.email,
                  "subject_sha256": digest(subject), "body_sha256": digest(body)}
        return files, contact, subject, body, claims

    def preview(self, document: dict, attachments: list[dict] | None = None, contact_id: int | None = None,
                message: str | None = None) -> dict:
        files, contact, subject, body, claims = self._prepare(document, attachments, contact_id, message)
        token, exp = self.confirmations.issue(claims)
        return {"to": {"contact_id": contact.contact_id, "name": contact.name, "email": contact.email,
                       "supplier_id": contact.supplier_id, "supplier_name": contact.supplier_name},
                "from": formataddr((self.settings.sender_name, self.settings.sender)),
                "reply_to": self.settings.reply_to, "subject": subject, "body": body,
                "attachments": [{"filename": a.filename, "content_type": a.content_type, "bytes": len(a.data),
                                 "sha256": a.sha256} for a in files],
                "content_hash": claims["content_hash"], "dry_run": self.transport.dry_run,
                "confirmation_token": token,
                "expires_at": datetime.fromtimestamp(exp, timezone.utc).isoformat(timespec="seconds")}

    def send(self, document: dict, confirmation_token: str | None, requested_by: str,
             attachments: list[dict] | None = None, contact_id: int | None = None, message: str | None = None,
             client_ref: str | None = None) -> dict:
        if not requested_by or not requested_by.strip() or len(requested_by) > 200:
            raise MailError("REQUESTER_REQUIRED", "발송을 승인한 사람(requested_by)이 필요하다")
        files, contact, subject, body, claims = self._prepare(document, attachments, contact_id, message)
        verified = self.confirmations.verify(confirmation_token, claims)
        if client_ref is not None and (not isinstance(client_ref, str) or CLIENT_REF.fullmatch(client_ref) is None):
            raise MailError("INVALID_CLIENT_REF", "client_ref는 영문자와 숫자 및 _ 또는 -로 구성한 1~64자여야 한다")
        msg = build_message(subject, body, contact, self.settings, files)
        send_id = self.log.reserve(verified["nonce"], {
            "document_id": claims["document_id"], "version": claims["version"], "content_hash": claims["content_hash"],
            "supplier_id": contact.supplier_id, "contact_id": contact.contact_id, "recipient": contact.email,
            "subject": subject, "requested_by": requested_by.strip(), "dry_run": int(self.transport.dry_run)}, client_ref)
        try:
            self.transport.send(msg)
        except (smtplib.SMTPException, OSError) as e:
            self.log.finish(send_id, "FAILED", error=type(e).__name__)
            raise MailError("MAIL_SEND_FAILED", f"메일 서버가 발송을 거절했거나 연결할 수 없다 ({type(e).__name__})") from None
        status = "DRY_RUN" if self.transport.dry_run else "SENT"
        self.log.finish(send_id, status, message_id=msg["Message-ID"])
        result = {"send_id": send_id, "status": status, "dry_run": self.transport.dry_run,
                  "message_id": msg["Message-ID"], "to": mask_email(contact.email), "subject": subject,
                  "document_id": claims["document_id"], "version": claims["version"],
                  "content_hash": claims["content_hash"], "sent_at": now_iso()}
        if self.transport.dry_run:
            result["mail"] = {"from": msg["From"], "to": msg["To"], "reply_to": msg["Reply-To"], "subject": subject,
                              "body": body, "attachments": [a.filename for a in files]}
        return result
