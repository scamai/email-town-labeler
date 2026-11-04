"""Mail-client failure paths use synthetic providers; never send or modify real mail."""

import base64
import json
import threading
import time
import uuid
from email import policy
from email.parser import BytesParser
from types import SimpleNamespace

import pytest

from email_town_labeler.desktop import providers
from email_town_labeler.desktop.accounts import imap_settings
from email_town_labeler.desktop.mail import MailStore, compose_data, mime_details, mime_message
from email_town_labeler.desktop.server import LocalServer
from tests.test_desktop_manager import FakeReader, _record, _workbench
from tests.test_desktop_server import request


def composition(**kw):
    return {
        "account_id": "account",
        "to": "Recipient <recipient@example.test>",
        "cc": "",
        "bcc": "hidden@example.test",
        "subject": "Fixture",
        "body": "Hello",
        "attachments": [
            {
                "name": "note.txt",
                "type": "text/plain",
                "data": base64.b64encode(b"fixture attachment").decode(),
            }
        ],
        **kw,
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("to", "a@example.test\r\nBcc: stolen@example.test"),
        ("subject", "bad\nheader"),
        ("to", "broken"),
        ("body", 5),
        ("attachments", [{"name": "x", "data": "not-base64!"}]),
        ("reply_to_id", "not-a-message"),
        ("attachments", [{}] * 21),
    ],
)
def test_compose_rejects_invalid_input(field, value):
    with pytest.raises(ValueError):
        compose_data(composition(**{field: value}))


def test_mime_bcc_envelope_only_and_reply_headers_and_attachments():
    data = compose_data(composition())
    msg = mime_message(
        data,
        "owner@example.test",
        {"headers": {"message-id": "<parent@example.test>", "references": "<older@example.test>"}},
    )
    parsed = BytesParser(policy=policy.default).parsebytes(msg.as_bytes())
    assert "Bcc" not in parsed and parsed["In-Reply-To"] == "<parent@example.test>"
    assert "<older@example.test>" in parsed["References"]
    assert next(parsed.iter_attachments()).get_payload(decode=True) == b"fixture attachment"
    assert mime_details(parsed)["attachments"][0]["name"] == "note.txt"
    with pytest.raises(ValueError, match="limit"):
        compose_data(composition(), max_bytes=2)


def test_durable_drafts_and_send_receipts_survive_restart(tmp_path):
    store = MailStore(tmp_path)
    draft = store.save_draft(composition())
    assert store.draft(draft["id"])["attachments"][0]["name"] == "note.txt"
    key = uuid.uuid4().hex
    assert store.reserve(key, {"synthetic": 1})
    assert not store.reserve(key, {"synthetic": 1})
    with pytest.raises(ValueError, match="different"):
        store.reserve(key, {"synthetic": 2})
    restarted = MailStore(tmp_path)
    assert restarted.receipt(key)["status"] == "unknown"
    assert restarted.drafts()["drafts"][0]["id"] == draft["id"]
    restarted.delete_draft(draft["id"])
    assert not restarted.drafts()["drafts"]


class MailReader(FakeReader):
    address = "owner@example.test"

    def __init__(self, records, *, fail_write=False, fail_send=False):
        super().__init__(records)
        self.writes, self.sent = [], []
        self.fail_write, self.fail_send = fail_write, fail_send

    def capabilities(self):
        return {
            "write": True,
            "send": True,
            "attachments": True,
            "max_attachment_bytes": 1024 * 1024,
            "reason": "",
        }

    def mutate(self, mid, action):
        self.writes.append((mid, action))
        if self.fail_write:
            raise providers.ProviderError("Fixture failure")
        if action in ("trash", "archive", "restore"):
            self.records = [r for r in self.records if r["provider_id"] != mid]

    def send(self, data, original):
        self.sent.append((data, original))
        if self.fail_send:
            raise TimeoutError("private transport details")
        return {"message": "Fixture accepted"}

    def attachment(self, mid, aid):
        return {"name": "test\r\nbad.txt"}, b"fixture"


def finished(w, key):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        result = w.mail_store.receipt(key)
        if result["status"] != "pending":
            return result
        time.sleep(0.01)
    pytest.fail("mail operation did not finish")


def setup_mail(tmp_path):
    w, readers, first, second = _workbench(tmp_path)
    readers[first] = MailReader([_record("same")])
    readers[second] = MailReader([_record("same")])
    w._refresh()
    # Fixture operations do not need another classifier/poll thread.
    w.refresh = lambda **kwargs: None
    return w, readers, first, second


def test_multiaccount_partial_result_and_duplicate_request_do_not_repeat(tmp_path):
    w, readers, first, second = setup_mail(tmp_path)
    readers[second].fail_write = True
    ids = [m["id"] for m in w.snapshot()["messages"]]
    data = {"request_id": uuid.uuid4().hex, "ids": ids, "action": "trash"}
    job = w.mail_action(data)
    result = finished(w, job["job_id"])
    assert result["status"] == "partial" and len(result["succeeded"]) == len(result["failed"]) == 1
    assert w.mail_action(data) == job
    assert len(readers[first].writes) == len(readers[second].writes) == 1
    assert len(w.snapshot()["messages"]) == 1
    w.close()


def test_send_timeout_retains_draft_and_never_auto_resends(tmp_path):
    w, readers, first, second = setup_mail(tmp_path)
    readers[first].fail_send = True
    draft = w.mail_store.save_draft(composition(account_id=first))
    data = composition(account_id=first, request_id=uuid.uuid4().hex, draft_id=draft["id"])
    key = w.send_mail(data)["job_id"]
    assert finished(w, key)["status"] == "unknown"
    assert w.mail_store.draft(draft["id"])
    w.send_mail(data)
    assert len(readers[first].sent) == 1 and not readers[second].sent
    assert "private transport" not in json.dumps(w.mail_store.receipt(key))
    w.close()


def test_cross_account_reply_rejected_before_any_send(tmp_path):
    w, readers, first, second = setup_mail(tmp_path)
    parent = next(m["id"] for m in w.snapshot()["messages"] if m["account_id"] == first)
    with pytest.raises(ValueError, match="account"):
        w.send_mail(composition(account_id=second, reply_to_id=parent, request_id=uuid.uuid4().hex))
    assert not readers[first].sent and not readers[second].sent
    w.close()


def test_folder_switch_drops_stale_refresh_before_publishing(tmp_path):
    w, readers, first, _second = setup_mail(tmp_path)
    started, release = threading.Event(), threading.Event()

    def delayed(limit):
        started.set()
        assert release.wait(3)
        return [_record("stale")], False

    readers[first].list = delayed
    thread = threading.Thread(target=w._account, args=(w.accounts.get(first),))
    thread.start()
    assert started.wait(3)
    w.change_folder("trash")
    release.set()
    thread.join(3)
    assert not w.snapshot()["messages"] and w.refresh_again
    w.close()


def test_new_routes_enforce_auth_origin_download_safety_and_drafts(tmp_path):
    w, _readers, first, _second = setup_mail(tmp_path)
    server = LocalServer(w)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        data = composition(account_id=first)
        assert request(server, "/api/send", method="POST", data=data)[0] == 401
        assert (
            request(server, "/api/draft", method="POST", data=data, auth=True, headers={"Origin": "null"})[0]
            == 403
        )
        status, _, raw = request(server, "/api/draft", method="POST", data=data, auth=True)
        assert status == 200
        draft_id = json.loads(raw)["id"]
        assert request(server, "/api/draft?id=" + draft_id, auth=True)[0] == 200
        assert request(server, "/api/draft?id=" + draft_id)[0] == 401
        key = w.snapshot()["messages"][0]["id"]
        status, headers, raw = request(server, f"/api/attachment?id={key}&attachment_id=x", auth=True)
        assert status == 200 and raw == b"fixture"
        assert headers["Content-Type"] == "application/octet-stream"
        assert headers["Content-Disposition"] == "attachment; filename*=UTF-8''attachment"
        assert request(server, "/mail.js")[0] == 200
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
        w.close()


class API:
    def __init__(self):
        self.calls = []

    def users(self):
        return self

    def messages(self):
        return self

    def __getattr__(self, method):
        def call(**kw):
            self.calls.append((method, kw))
            return SimpleNamespace(execute=lambda **opts: {"id": "fixture"})

        return call


def test_gmail_send_and_safe_trash_use_expected_api_without_retries():
    api = API()
    r = providers.GmailReader.__new__(providers.GmailReader)
    r.scopes = {"https://www.googleapis.com/auth/gmail.modify"}
    r.client = SimpleNamespace(service=api)
    r.write = lambda path, payload: api.calls.append((path, {"body": payload})) or {}
    r.address, r.identity, r.cache = "owner@example.test", "account", {"m": {}}
    r.send(
        compose_data(composition()), {"thread_id": "thread", "headers": {"message-id": "<x@example.test>"}}
    )
    payload = api.calls[0][1]["body"]
    mime = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(payload["raw"]))
    assert mime["Bcc"] == "hidden@example.test" and payload["threadId"] == "thread"
    r.mutate("account:m", "trash")
    assert api.calls[-1] == ("messages/m/trash", {"body": {}})
    assert not any(name == "delete" for name, _ in api.calls)
    r.scopes = {"https://www.googleapis.com/auth/gmail.readonly"}
    with pytest.raises(providers.ProviderError, match="Reconnect"):
        r.send(compose_data(composition()))


def test_microsoft_permissions_and_native_reply_sequence(tmp_path):
    token = tmp_path / "token.json"
    token.write_text(json.dumps({"scope": "Mail.ReadWrite Mail.Send"}))
    r = providers.MicrosoftReader({"token_path": str(token)})
    r.cache, r.identity = {"original": {}}, "account"
    calls = []
    r.write = lambda path, payload, method="POST": calls.append((path, payload, method)) or {"id": "draft"}
    r.send(compose_data(composition()), {"provider_id": "account:original"})
    assert [c[0] for c in calls] == [
        "messages/original/createReply",
        "messages/draft",
        "messages/draft/attachments",
        "messages/draft/send",
    ]
    assert calls[1][1]["bccRecipients"] == [{"emailAddress": {"address": "hidden@example.test"}}]
    token.write_text(json.dumps({"scope": "Mail.Read"}))
    assert not r.capabilities()["send"] and not r.capabilities()["write"]


class IMAPWrite:
    def __init__(self, *, move=True):
        self.capabilities = (b"IMAP4rev1", b"MOVE") if move else (b"IMAP4rev1",)
        self.calls = []

    def list(self):
        return "OK", [b'(\\Trash) "/" "Deleted Messages"', b'(\\Sent) "/" "Sent Messages"']

    def uid(self, *args):
        self.calls.append(args)
        return "OK", []

    def logout(self):
        pass


def test_imap_safe_uid_move_never_expunges_other_mail(monkeypatch):
    r = providers.ImapReader({"id": "a", "settings": {}})
    r.validity, r.ids = "7", {"2"}
    conn = IMAPWrite()
    monkeypatch.setattr(r, "connect", lambda **kw: (conn, "7"))
    r.mutate("7:2", "trash")
    assert conn.calls == [("MOVE", "2", b'"Deleted Messages"')]
    conn.capabilities = (b"IMAP4rev1",)
    with pytest.raises(providers.ProviderError, match="safe UID MOVE"):
        r.mutate("7:2", "trash")
    assert len(conn.calls) == 1
    monkeypatch.setattr(r, "connect", lambda **kw: (conn, "8"))
    with pytest.raises(providers.ProviderError, match="rebuilt"):
        r.mutate("7:2", "read")
    assert len(conn.calls) == 1


def test_smtp_starttls_bcc_and_sent_copy_failure_does_not_resend(monkeypatch):
    calls = []

    class SMTP:
        def __init__(self, *args, **kwargs):
            calls.append("connect")

        def ehlo(self):
            calls.append("ehlo")

        def starttls(self, context):
            calls.append("tls")

        def login(self, user, password):
            calls.append("login")

        def sendmail(self, sender, recipients, raw):
            calls.append("send")
            assert "hidden@example.test" in recipients and b"Bcc:" not in raw
            return {}

        def quit(self):
            calls.append("quit")

    monkeypatch.setattr(providers.smtplib, "SMTP", SMTP)
    monkeypatch.setattr(providers, "password_store", lambda *_: "synthetic")
    settings = imap_settings(
        {"host": "imap.example.test", "username": "owner@example.test", "smtp_host": "smtp.example.test"}
    )
    r = providers.ImapReader({"id": "a", "settings": settings})
    monkeypatch.setattr(r, "connect", lambda: (_ for _ in ()).throw(OSError("fixture")))
    result = r.send(compose_data(composition()))
    assert calls == ["connect", "ehlo", "tls", "ehlo", "login", "send", "quit"]
    assert "Do not resend" in result["message"]
    with pytest.raises(ValueError, match="TLS"):
        imap_settings({**settings, "smtp_security": "none"})


def test_gmail_write_transport_does_not_retry_an_accepted_post_with_lost_response(monkeypatch):
    """Observe the write boundary, not Google's configurable outer retry count."""
    import http.client

    r = providers.GmailReader.__new__(providers.GmailReader)
    r.credentials = SimpleNamespace(valid=True, token="synthetic")
    calls = []

    class OneShot:
        def open(self, request, timeout):
            calls.append(request)
            raise http.client.RemoteDisconnected("response lost after acceptance")

    monkeypatch.setattr(providers, "build_opener", lambda *_: OneShot())
    with pytest.raises(http.client.RemoteDisconnected):
        r.write("messages/send", {"raw": "fixture"})
    assert len(calls) == 1
    assert calls[0].get_method() == "POST"
    assert calls[0].full_url == "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"


def test_partial_send_keeps_draft_and_identifies_rejected_recipient(tmp_path):
    w, readers, first, _second = setup_mail(tmp_path)
    readers[first].send = lambda data, original: {
        "status": "partial",
        "accepted": ["ok@example.test"],
        "refused": ["bad@example.test"],
        "message": "Only some recipients accepted",
    }
    data = composition(account_id=first)
    draft = w.mail_store.save_draft(data)
    key = w.send_mail({**data, "draft_id": draft["id"], "request_id": uuid.uuid4().hex})["job_id"]
    result = finished(w, key)
    assert result["status"] == "partial"
    assert result["failed"][0]["id"] == "bad@example.test"
    assert result["succeeded"] == ["ok@example.test"]
    assert w.mail_store.draft(draft["id"])
    w.close()


def test_atomic_draft_cleanup_preserves_newer_edits(tmp_path):
    store = MailStore(tmp_path)
    old = compose_data(composition())
    key = store.save_draft(old)["id"]
    store.save_draft({**old, "id": key, "body": "newer edits"})
    store.delete_matching_draft(key, old)
    assert store.draft(key)["body"] == "newer edits"
    store.delete_matching_draft(key, {**old, "body": "newer edits"})
    assert not store.drafts()["drafts"]


def test_provider_star_flags_survive_read_refresh():
    result = providers.MicrosoftReader.record(
        {
            "id": "fixture",
            "receivedDateTime": "2026-11-01T10:00:00Z",
            "isRead": True,
            "flag": {"flagStatus": "flagged"},
        }
    )
    assert result["label_ids"] == ["STARRED"]
    result = providers.ImapReader.parse(
        b"From: fixture@example.test\r\n\r\nfixture", "1", b"FLAGS (\\Seen \\Flagged)"
    )
    assert result["label_ids"] == ["STARRED"]


def test_reply_defaults_never_extract_addresses_from_display_names():
    from email_town_labeler.desktop.mail import recipient_headers

    result = recipient_headers(
        {
            "from": '"intruder@outside.test" <sender@inside.test>',
            "reply-to": "Reply (intruder@outside.test) <reply@inside.test>",
            "to": "Owner <owner@inside.test>",
        }
    )
    assert result["from"] == ["sender@inside.test"]
    assert result["reply-to"] == ["reply@inside.test"]
    assert result["to"] == ["owner@inside.test"]
