#!/usr/bin/env python3
"""Explicit HTTP smoke test for the local Compose stack (stdlib only).

Initial mode checks the preapproved local USER and creates one signup member.
It stores a follow-up conversation for each member and checks their isolation.
It never refreshes external laws or changes containers. Verification mode logs
in with the originally saved credentials and checks both histories after a
Compose down/up. State includes test passwords and is written with mode 0600.
No passwords, CSRF tokens or cookies are printed.

    export COMPOSE_FILE=compose.yaml:compose.test.yaml
    export COMPOSE_PROJECT_NAME=negopilot-test TEST_WEB_PORT=5174
    python3 scripts/integration/compose_smoke.py --base-url http://127.0.0.1:5174 --state /tmp/compose-smoke.json
    docker compose down && docker compose up -d --wait
    python3 scripts/integration/compose_smoke.py --base-url http://127.0.0.1:5174 --verify-state /tmp/compose-smoke.json

Use a new state path for each initial run. The stack must use stub chat mode.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import build_opener, HTTPCookieProcessor, HTTPRedirectHandler, Request
import uuid

AUTH = "/api/auth"
CONVERSATIONS = "/api/conversations"
CATALOG = "/api/business/catalog"
AMBIGUOUS_QUERY = "해솔에 대해 알려줘"
FOLLOWUP_QUERY = "첫 번째"
STATE_VERSION = 2


class SmokeFailure(Exception):
    """Contains only a safe check label rather than response bodies or secrets."""


def require(condition: object, label: str) -> None:
    if not condition:
        raise SmokeFailure(label)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.opener = build_opener(NoRedirect(), HTTPCookieProcessor(CookieJar()))
        self.csrf: dict[str, str] = {}

    def request(self, method: str, path: str, body=None, *, status=200, csrf=True, headers=None):
        request_headers = {"Accept": "application/json", "User-Agent": "NegoPilot-Compose-Smoke"}
        if csrf and method not in {"GET", "HEAD"}:
            request_headers.update(self.csrf)
        if headers:
            request_headers.update(headers)
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        request = Request(self.base_url + path, data=data, headers=request_headers, method=method)
        try:
            response = self.opener.open(request, timeout=60)
        except HTTPError as error:
            response = error
        except (URLError, TimeoutError, OSError):
            raise SmokeFailure(f"{method} {path.split('?')[0]} connection failed") from None
        with response:
            actual_status = response.code
            raw = response.read()
        try:
            payload = json.loads(raw) if raw else None
        except (ValueError, UnicodeError):
            raise SmokeFailure(f"{method} {path.split('?')[0]} did not return JSON") from None
        if actual_status != status:
            code = payload.get("error", {}).get("code", "") if isinstance(payload, dict) else ""
            safe_code = code if isinstance(code, str) and re.fullmatch(r"[A-Z_]{1,80}", code) else "UNKNOWN"
            raise SmokeFailure(f"{method} {path.split('?')[0]} expected HTTP {status}; got {actual_status} {safe_code}")
        return payload

    def refresh_csrf(self) -> None:
        token = self.request("GET", AUTH + "/csrf")
        require(isinstance(token.get("header_name"), str) and bool(token.get("token")), "CSRF response is incomplete")
        self.csrf = {token["header_name"]: token["token"]}

    def login(self, email: str, password: str) -> dict:
        self.refresh_csrf()
        user = self.request("POST", AUTH + "/login", {"email": email, "password": password})["user"]
        self.refresh_csrf()  # Login rotates the session and invalidates the old token.
        require(self.request("GET", AUTH + "/me")["user"]["id"] == user["id"], "Authenticated member differs")
        return user

    def logout(self) -> None:
        self.request("POST", AUTH + "/logout", status=204)
        error = self.request("GET", AUTH + "/me", status=401)
        require(error["error"]["code"] == "UNAUTHENTICATED", "Logout did not invalidate the session")
        self.csrf = {}


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def reserve_state(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        raise SmokeFailure("State path already exists. Use --verify-state or choose a new path.") from None
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump({"format_version": STATE_VERSION, "phase": "started"}, stream)
        stream.write("\n")


def save_state(path: Path, state: dict) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".compose-smoke-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_state(path: Path) -> dict:
    mode = path.lstat().st_mode
    require(stat.S_ISREG(mode) and stat.S_IMODE(mode) == 0o600,
            "State must be a regular private file with permissions 0600")
    state = json.loads(path.read_text(encoding="utf-8"))
    require(state.get("format_version") == STATE_VERSION and state.get("phase") == "complete",
            "State must come from a completed two-user smoke")
    for chat in (state, state["default_chat"]):
        uuid.UUID(chat["conversation_id"])
        for entry in chat["responses"]:
            uuid.UUID(entry["response_id"])
    return state


def admin_login(base_url: str) -> Client:
    admin = Client(base_url)
    member = admin.login(os.getenv("LOCAL_ADMIN_EMAIL", "admin@negopilot.local"),
                         os.getenv("LOCAL_ADMIN_PASSWORD", "LocalAdmin2026!"))
    require((member["role"], member["status"]) == ("ADMIN", "ACTIVE"), "Bootstrap admin is not active")
    members = admin.request("GET", "/api/admin/members?size=100")
    require(isinstance(members["items"], list) and members["total"] >= 1, "Admin member listing failed")
    return admin


def live_lookup(client: Client) -> str:
    envelope = client.request("GET", CATALOG)
    require((envelope["schema_version"], envelope["mode"]) == ("0.3", "live"), "Catalog must read live business data")
    processes = envelope["data"]["processes"]
    require(bool(processes) and bool(processes[0]["materials"]), "Seed catalog is empty")
    process = processes[0]
    filters = urlencode({"process": process["process"], "material": process["materials"][0], "limit": 3})
    search = client.request("GET", "/api/suppliers?" + filters)
    require((search["schema_version"], search["mode"]) == ("0.3", "live"), "Search must read live business data")
    items = search["data"]["items"]
    ids = [item["supplier_id"] for item in items]
    require(bool(ids) and ids == sorted(set(ids)) and len(ids) <= 3, "Seed supplier search is empty or unordered")
    require(search["data"]["returned_count"] == len(ids), "Supplier count differs")
    detail = client.request("GET", "/api/suppliers/" + ids[0])
    require((detail["schema_version"], detail["mode"]) == ("0.3", "live"), "Detail must read live business data")
    require(detail["data"]["supplier_id"] == ids[0], "Supplier detail ID differs")
    require(detail["data"]["provenance"]["profile"] in {"DART", "MOCK", "MANUAL"}, "Supplier source is not live")
    return ids[0]


def persisted_snapshot(client: Client, state: dict) -> dict:
    path = CONVERSATIONS + "/" + state["conversation_id"]
    snapshot = client.request("GET", path)
    require((snapshot["schema_version"], snapshot["storage"]) == ("0.4", "database"), "Conversation is not database backed")
    require(snapshot["message_count"] == 4 and len(snapshot["messages"]) == 4, "Expected four stored messages")
    require(snapshot["context"] == {"selected_supplier_ids": []}, "Empty company context changed")
    messages = snapshot["messages"]
    require([message["seq"] for message in messages] == [1, 2, 3, 4], "Stored sequence differs")
    require([message["role"] for message in messages] == ["user", "assistant", "user", "assistant"], "Stored roles differ")
    for index, entry in enumerate(state["responses"]):
        require(messages[index * 2]["content"] == entry["query"], "Stored user question differs")
        require(messages[index * 2 + 1]["response"] == entry["response"], "Stored full response differs")
        require(messages[index * 2 + 1]["message_id"] == entry["response_id"], "Stored response ID differs")
        saved = client.request("GET", path + "/responses/" + entry["response_id"])
        require(saved == {"query": entry["query"], "response": entry["response"]}, "Saved response endpoint differs")
    page = client.request("GET", path + "/messages?size=10")
    require(page["storage"] == "database" and page["messages"] == messages, "Message page differs from snapshot")
    return snapshot


def assert_regular_user(client: Client) -> None:
    denied = client.request("GET", "/api/admin/members", status=403)
    require(denied["error"]["code"] == "FORBIDDEN", "Regular user gained admin access")


def assert_chat_inaccessible(client: Client, chat: dict) -> None:
    path = CONVERSATIONS + "/" + chat["conversation_id"]
    for suffix in ("", "/messages", "/responses/" + chat["responses"][0]["response_id"]):
        denied = client.request("GET", path + suffix, status=404)
        require(denied["error"]["code"] == "CONVERSATION_NOT_FOUND", "Another member could read private chat")
    listing = client.request("GET", CONVERSATIONS + "?size=100")
    require(all(item["conversation_id"] != chat["conversation_id"] for item in listing["items"]),
            "Conversation list exposed another member's chat")


def create_followup_chat(user: Client, title: str) -> dict:
    conversation = user.request("POST", CONVERSATIONS, {"context": {"selected_supplier_ids": []}}, status=201)
    require(conversation["storage"] == "database" and conversation["message_count"] == 0, "New conversation storage differs")
    chat = {"conversation_id": conversation["conversation_id"]}
    conversation_path = CONVERSATIONS + "/" + chat["conversation_id"]
    responses = []
    for query, expected_status, expected_kind in [(AMBIGUOUS_QUERY, "NEEDS_INPUT", "clarify"),
                                                   (FOLLOWUP_QUERY, "COMPLETED", "answer")]:
        result = user.request("POST", conversation_path + "/messages", {"query": query},
                              headers={"Idempotency-Key": str(uuid.uuid4())})
        require((result["schema_version"], result["mode"]) == ("0.3", "stub"), "Chat must use deterministic stub mode")
        require(result["execution_status"] == expected_status and result["answer"]["kind"] == expected_kind,
                "Clarification or numbered follow-up status differs")
        require(result["generation_status"] == "COMPLETED" and bool(result["answer"]["text"].strip()), "Answer is incomplete")
        require(result["conversation_id"] == chat["conversation_id"], "Answer conversation ID differs")
        responses.append({"query": query, "response_id": result["response_id"], "response": result})
    options = responses[0]["response"]["answer"]["options"]
    require(len(options) == 2, "Ambiguous question must provide two choices")
    match = re.search(r"SUP-[0-9]+", options[0])
    require(match is not None, "First choice has no supplier ID")
    followup = responses[1]["response"]
    calls = [call for outcome in followup["routing"]["outcomes"] for call in outcome["calls"]]
    require(any(call["tool"] == "get_supplier" and call["args"].get("supplier") == match.group()
                and call["error"] is None for call in calls), "Follow-up did not resolve the first stored choice")
    require(followup["decomposition"]["original_query"] == FOLLOWUP_QUERY, "Follow-up original query changed")
    chat["responses"] = responses
    persisted_snapshot(user, chat)
    renamed = user.request("PATCH", conversation_path, {"title": title})
    require(renamed["title"] == title and renamed["message_count"] == 4, "Conversation rename failed")
    chat.update(title=title, messages=renamed["messages"])
    return chat


def login_saved_user(base_url: str, saved_user: dict) -> Client:
    client = Client(base_url)
    # Always use the first run's credentials. Bootstrap overrides must not change an existing account.
    member = client.login(saved_user["email"], saved_user["password"])
    require((member["id"], member["role"], member["status"]) == (saved_user["id"], "USER", "ACTIVE"),
            "Member did not persist after restart")
    assert_regular_user(client)
    return client


def verify_saved_chat(client: Client, chat: dict) -> None:
    snapshot = persisted_snapshot(client, chat)
    require(snapshot["title"] == chat["title"] and snapshot["messages"] == chat["messages"],
            "Saved title or complete messages changed after restart")
    listing = client.request("GET", CONVERSATIONS + "?size=100")
    require(listing["storage"] == "database" and any(item["conversation_id"] == chat["conversation_id"]
            and item["title"] == chat["title"] for item in listing["items"]), "Conversation list lost saved chat")


def initial(base_url: str, path: Path) -> None:
    reserve_state(path)
    anonymous = Client(base_url)
    denied = anonymous.request("GET", "/api/admin/members", status=401)
    require(denied["error"]["code"] == "UNAUTHENTICATED", "Anonymous admin access was not blocked")
    denied = anonymous.request("POST", AUTH + "/login", {}, status=403, csrf=False)
    require(denied["error"]["code"] == "CSRF_INVALID", "Login did not require CSRF")
    admin = admin_login(base_url)
    supplier_id = live_lookup(admin)
    laws = admin.request("GET", "/api/admin/laws")
    require(laws["law_count"] > 0 and bool(laws["items"]), "Law catalog is empty")
    jobs = admin.request("GET", "/api/admin/laws/refresh-jobs")
    require("item" in jobs, "Law refresh-job read response is incomplete")

    run_id = uuid.uuid4().hex
    default_user_data = {"email": os.getenv("LOCAL_USER_EMAIL", "user@negopilot.local"),
                         "password": os.getenv("LOCAL_USER_PASSWORD", "LocalUser2026!")}
    default_user = Client(base_url)
    default_member = default_user.login(default_user_data["email"], default_user_data["password"])
    require((default_member["role"], default_member["status"]) == ("USER", "ACTIVE"),
            "Default local USER must be active without approval")
    assert_regular_user(default_user)
    live_lookup(default_user)
    default_chat = create_followup_chat(default_user, "Compose 기본 사용자 검증 " + run_id[:8])
    state = {"format_version": STATE_VERSION, "phase": "default_user_verified", "created_at": timestamp(),
             "base_url": base_url, "default_user": default_user_data | {"id": default_member["id"]},
             "default_chat": default_chat, "lookup_supplier_id": supplier_id}
    save_state(path, state)

    user_data = {"email": f"compose-smoke-{run_id}@example.test", "password": "Smoke1!" + secrets.token_urlsafe(20)}
    user = Client(base_url)
    user.refresh_csrf()
    member = user.request("POST", AUTH + "/signup", user_data | {
        "name": "Compose 검증 회원", "department": "개발검증", "job_title": "테스트",
    }, status=201)["user"]
    require((member["role"], member["status"]) == ("USER", "PENDING"), "Signup must create a pending user")
    require(member["id"] != default_member["id"], "Signup and default user identities must differ")
    state.update(phase="registered", user=user_data | {"id": member["id"]})
    save_state(path, state)
    require(user.login(user_data["email"], user_data["password"])["status"] == "PENDING", "Pending login failed")
    denied = user.request("GET", CATALOG, status=403)
    require(denied["error"]["code"] == "FORBIDDEN", "Pending user could access business data")
    approved = admin.request("PATCH", "/api/admin/members/" + member["id"] + "/status", {"status": "ACTIVE"})
    require(approved["user"]["status"] == "ACTIVE", "Admin approval failed")
    live_lookup(user)  # The existing pending session must immediately gain active access.
    user.refresh_csrf()
    assert_regular_user(user)
    state.update(create_followup_chat(user, "Compose 재시작 검증 " + run_id[:8]))
    require(state["conversation_id"] != default_chat["conversation_id"], "Members must have distinct conversations")
    assert_chat_inaccessible(default_user, state)
    assert_chat_inaccessible(user, default_chat)
    assert_chat_inaccessible(admin, state)
    assert_chat_inaccessible(admin, default_chat)
    user.logout()
    default_user.logout()
    admin.logout()
    state.update(phase="complete", completed_at=timestamp())
    save_state(path, state)
    print("PASS compose smoke: default USER, signup/approval, live lookup, law reads, two stored follow-up chats, isolation, logout")


def verify(base_url: str, path: Path) -> None:
    state = load_state(path)
    admin = admin_login(base_url)
    default_user = login_saved_user(base_url, state["default_user"])
    user = login_saved_user(base_url, state["user"])
    verify_saved_chat(default_user, state["default_chat"])
    verify_saved_chat(user, state)
    assert_chat_inaccessible(default_user, state)
    assert_chat_inaccessible(user, state["default_chat"])
    assert_chat_inaccessible(admin, state)
    assert_chat_inaccessible(admin, state["default_chat"])
    user.logout()
    default_user.logout()
    admin.logout()
    print("PASS compose restart verification: original credentials, both USER IDs, eight messages, full answers, titles, isolation")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://127.0.0.1:5173")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--state", type=Path, help="Check the default USER, create a signup member and save two chats privately")
    mode.add_argument("--verify-state", type=Path, help="Check a completed state file after Compose restart")
    args = parser.parse_args()
    try:
        parsed = urlsplit(args.base_url)
        require(parsed.scheme in {"http", "https"} and parsed.netloc and not parsed.username
                and not parsed.password and not parsed.query and not parsed.fragment and parsed.path in {"", "/"},
                "Base URL must be an HTTP(S) origin without credentials or path")
        base_url = args.base_url.rstrip("/")
        if args.state:
            initial(base_url, args.state.absolute())
        else:
            verify(base_url, args.verify_state.absolute())
        return 0
    except SmokeFailure as failure:
        print("FAIL compose smoke: " + str(failure), file=sys.stderr)
    except Exception as failure:
        # Unexpected library errors may contain credentials. Report only the class.
        print("FAIL compose smoke: unexpected " + type(failure).__name__, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
