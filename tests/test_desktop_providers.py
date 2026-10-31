"""Read-only provider contract and credential-boundary tests; synthetic mail only."""

from types import SimpleNamespace

import pytest

from email_town_labeler.config import Config
from email_town_labeler.desktop import providers as p
from tests.fakes.fake_client import FakeGmailClient


def test_gmail_metadata_history_and_full_detail_are_read_only():
    client = FakeGmailClient()
    reader = p.GmailReader.__new__(p.GmailReader)
    reader.client, reader.cfg = client, Config()
    reader.address, reader.identity = "fixture@example.test", "identity"
    reader.cache, reader.history_id = {}, None
    mid = client.add_message()
    name = f"{reader.cfg.label_prefix}/{reader.cfg.display_names()['orders']}"
    client.labels["orders-id"] = {"id": "orders-id", "name": name}
    rows, truncated = reader.list(10)
    assert rows[0]["category"] is None and not truncated
    assert not any(call[0] == "get_records" for call in client.calls)
    client.user_add_label(mid, "orders-id")
    rows, _ = reader.list(10)
    assert rows[0]["category"] == "orders"
    assert reader.detail(rows[0]["provider_id"])["body_text"] == "made-up body"
    with pytest.raises(p.ProviderError):
        reader.detail("wrong:" + mid)
    assert not client.modifies


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "https://evil.test/",
        "https://mail.google.com@evil.test/",
        "https://mail.google.com:bad/",
        "https://mail.google.com:444/",
        "https://[bad",
    ],
)
def test_original_link_rejects_untrusted_or_malformed(url):
    assert p.safe_link(url) == ""


def test_microsoft_next_link_rejected_before_credentials(monkeypatch, tmp_path):
    reader = p.MicrosoftReader({"token_path": str(tmp_path / "absent"), "client_id": "fixture"})
    for url in [
        "https://evil.test/v1.0/me",
        "https://login.microsoftonline.com/v1.0/me",
        "https://graph.microsoft.com.evil.test/v1.0/me",
    ]:
        with pytest.raises(p.ProviderError, match="分页"):
            reader.get(url)
    with pytest.raises(p.ProviderError):
        p.request_json("http://graph.microsoft.com/v1.0/me", token="fixture")
    with pytest.raises(p.ProviderError):
        p.NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.test")


def test_microsoft_pagination_duplicate_cursor_and_text_body(monkeypatch, tmp_path):
    reader = p.MicrosoftReader({"token_path": str(tmp_path / "absent")})
    message = {
        "id": "opaque/one",
        "receivedDateTime": "2026-10-30T12:00:00Z",
        "subject": "fixture",
        "isRead": False,
    }
    reader.identity = "identity"
    calls = []

    def get(url):
        calls.append(url)
        return {"value": [message], "@odata.nextLink": "https://graph.microsoft.com/v1.0/repeated"}

    monkeypatch.setattr(reader, "get", get)
    with pytest.raises(p.ProviderError, match="分页"):
        reader.list(10)
    assert len(calls) == 2
    reader.cache = {message["id"]: message}
    monkeypatch.setattr(
        reader,
        "get",
        lambda url: {
            "body": {"contentType": "html", "content": "<p>Hello</p>"},
            "webLink": "https://evil.test/",
        },
    )
    detail = reader.detail("identity:opaque/one")
    assert detail["body_text"].strip() == "Hello" and not detail["url"]


class FakeIMAP:
    def __init__(self, validity="7"):
        self.validity, self.calls = validity, []

    def uid(self, *args):
        self.calls.append(args)
        if args[0] == "search":
            return "OK", [b"1 2"]
        assert "BODY.PEEK" in args[2] and "INTERNALDATE" in args[2]
        return "OK", [
            (
                b'1 (FLAGS () INTERNALDATE "30-Oct-2026 12:00:00 +0000")',
                b"Subject: Fixture\r\nFrom: sender@example.test\r\nDate: Thu, 1 Jan 1970 00:00:00 +0000\r\n\r\nhello",
            )
        ]

    def logout(self):
        self.calls.append(("logout",))


def test_imap_uidvalidity_peek_and_received_time(monkeypatch):
    reader = p.ImapReader({"id": "a", "settings": {}})
    conn = FakeIMAP()
    monkeypatch.setattr(reader, "connect", lambda: (conn, conn.validity))
    rows, truncated = reader.list(1)
    assert truncated and rows[0]["provider_id"] == "7:2"
    assert rows[0]["internal_date"] > 1_700_000_000_000
    assert reader.detail("7:2")["body_text"] == "hello"
    conn.validity = "8"
    with pytest.raises(p.ProviderError, match="重建"):
        reader.detail("7:2")
    assert all(c[0] in {"search", "fetch", "logout"} for c in conn.calls)


def test_imap_select_is_readonly_and_transient_password_not_saved(monkeypatch):
    conn = SimpleNamespace(
        login=lambda *args: None,
        select=lambda folder, readonly: ("OK", []) if readonly else None,
        response=lambda name: (name, [b"7"]),
        logout=lambda: None,
    )
    monkeypatch.setattr(p.imaplib, "IMAP4_SSL", lambda *args, **kwargs: conn)
    monkeypatch.setattr(
        p, "password_store", lambda *args: pytest.fail("must not read/write keyring during test login")
    )
    reader = p.ImapReader(
        {"id": "a", "settings": {"host": "imap.example.test", "port": 993, "username": "fixture"}},
        password="temporary",
    )
    assert reader.connect() == (conn, "7")


def test_microsoft_null_sender_is_a_valid_list_row():
    row = p.MicrosoftReader.record(
        {"id": "fixture", "receivedDateTime": "2026-10-30T12:00:00Z", "from": None}
    )
    assert row["headers"]["from"] == ""
