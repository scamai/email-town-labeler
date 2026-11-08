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
    reader.cache, reader.history_id, reader.folder_ids = {}, None, {}
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
        with pytest.raises(p.ProviderError, match="pagination"):
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
    with pytest.raises(p.ProviderError, match="pagination"):
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
        data = []
        for seq, uid in enumerate(args[1].split(b","), 1):
            # A real UID FETCH always reports UID; FLAGS may follow the literal.
            data += [
                (
                    b'%d (UID %s INTERNALDATE "30-Oct-2026 12:00:00 +0000" BODY[] {99}' % (seq, uid),
                    b"Subject: Fixture\r\nFrom: sender@example.test\r\nDate: Thu, 1 Jan 1970 00:00:00 +0000\r\n\r\nhello",
                ),
                b" FLAGS (\\Seen))" if uid == b"1" else b" FLAGS ())",
            ]
        return "OK", data

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
    with pytest.raises(p.ProviderError, match="rebuilt"):
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


def test_imap_list_reads_every_header_in_one_round_trip_and_bodies_on_one_login(monkeypatch):
    reader = p.ImapReader({"id": "a", "settings": {}})
    conn = FakeIMAP()
    logins = []
    monkeypatch.setattr(reader, "connect", lambda: (logins.append(1), (conn, conn.validity))[1])
    rows, truncated = reader.list(10)
    fetches = [c for c in conn.calls if c[0] == "fetch"]
    assert len(fetches) == 1 and fetches[0][1] == b"2,1" and not truncated
    unread = {row["provider_id"]: "UNREAD" in row["label_ids"] for row in rows}
    assert unread == {"7:2": True, "7:1": False}  # FLAGS after the header literal are honoured
    logins.clear()
    found = reader.details(["7:2", "7:1", "7:99", "8:1"])
    assert set(found) == {"7:2", "7:1"} and len(logins) == 1


def test_gmail_cache_survives_folder_switch_and_history_rereads_only_changes():
    client = FakeGmailClient()
    reader = p.GmailReader.__new__(p.GmailReader)
    reader.client, reader.cfg = client, Config()
    reader.address, reader.identity = "fixture@example.test", "identity"
    reader.cache, reader.history_id, reader.folder_ids = {}, None, {}
    inbox = client.add_message()
    sent = client.add_message(labels=("SENT",))

    def metadata_reads():
        return [call[1][0] for call in client.calls if call[0] == "get_metadata"]

    reader.list(10)
    reader.list(10, folder="sent")
    reader.list(10)
    assert metadata_reads() == [(inbox,), (sent,)]  # returning to Inbox re-read nothing
    client.labels["star"] = {"id": "star", "name": "Starred"}
    client.user_add_label(sent, "star")
    reader.list(10)
    rows, _ = reader.list(10, folder="sent")
    assert metadata_reads()[-1] == (sent,) and "star" in rows[0]["label_ids"]
    found = reader.details([f"identity:{inbox}", f"identity:{sent}", "identity:gone", "other:x"])
    assert set(found) == {f"identity:{inbox}", f"identity:{sent}"}
    assert [c[0] for c in client.calls].count("get_records") == 1


def test_gmail_reader_uses_interactive_pacing_not_the_background_service_pace(monkeypatch, tmp_path):
    from email_town_labeler.config import Paths

    paths = Paths.default(tmp_path).ensure()
    paths.token.write_text(
        '{"token": "t", "refresh_token": "r", "client_id": "c", "client_secret": "s",'
        ' "expiry": "2099-01-01T00:00:00Z", "scopes": ["https://www.googleapis.com/auth/gmail.modify"]}'
    )
    seen = {}

    class Client:
        def __init__(self, service, **kwargs):
            seen.update(kwargs)

        def profile(self):
            return {"emailAddress": "fixture@example.test"}

    monkeypatch.setattr(p, "GmailClient", Client)
    reader = p.GmailReader({"config_dir": str(paths.config_dir), "data_dir": str(paths.data_dir)})
    assert reader.read_ahead == 0  # bodies cost 60 units each; Gmail opens are fast enough without
    assert seen["batch_size"] == p.GMAIL_BATCH
    assert seen["max_retries"] == p.GMAIL_RETRIES >= 4  # a throttled batch must not fail the sync
    # A cold 100-row list (20 units a row) fits in the burst; the CLI service keeps a fifth of 6,000.
    assert 100 * 20 <= seen["units_per_minute"] <= 6000 * 0.8


def test_gmail_cold_list_reports_rows_batch_by_batch():
    client = FakeGmailClient()
    reader = p.GmailReader.__new__(p.GmailReader)
    reader.client, reader.cfg = client, Config()
    reader.address, reader.identity = "fixture@example.test", "identity"
    reader.cache, reader.history_id, reader.folder_ids = {}, None, {}
    batch = p.GMAIL_BATCH
    total = 2 * batch + batch // 2
    newest_first = [client.add_message(age_days=total - i) for i in range(total)][::-1]
    seen = []
    rows, _ = reader.list(total, progress=lambda partial: seen.append([r["id"] for r in partial]))
    # Newest rows first, one update per completed batch, never a duplicate of the final list.
    assert seen == [newest_first[:batch], newest_first[: 2 * batch]]
    assert [r["id"] for r in rows] == newest_first
    seen.clear()
    reader.list(total, progress=seen.append)
    assert seen == []  # nothing to read: no partial updates


def test_gmail_consecutive_writes_on_one_message_and_fresh_rows_after():
    """Mark read then unread (or trash then undo) before the next sync: both writes reach Gmail,
    and the next list still re-reads the message, because history reports our own label changes."""
    client = FakeGmailClient()
    reader = p.GmailReader.__new__(p.GmailReader)
    reader.client, reader.cfg = client, Config()
    reader.address, reader.identity = "fixture@example.test", "identity"
    reader.cache, reader.history_id, reader.folder_ids = {}, None, {}
    reader.scopes = {"https://www.googleapis.com/auth/gmail.modify"}

    def write(path, payload):  # the real write is a one-shot POST; apply it to the fake mailbox
        _, mid, verb = path.split("/")
        if verb == "modify":
            client.modify(mid, add=payload.get("addLabelIds", []), remove=payload.get("removeLabelIds", []))
        else:
            client.modify(
                mid, add=["TRASH"] if verb == "trash" else [], remove=[] if verb == "trash" else ["TRASH"]
            )
        return {}

    reader.write = write
    mid = client.add_message()
    rows, _ = reader.list(10)
    pid = rows[0]["provider_id"]
    reader.mutate(pid, "read")
    reader.mutate(pid, "unread")  # used to fail: "no longer in this account's current list"
    reader.mutate(pid, "trash")
    reader.mutate(pid, "restore")
    assert [m[0] for m in client.modifies] == [mid] * 5
    assert "UNREAD" in client.messages[mid]["label_ids"] and "TRASH" not in client.messages[mid]["label_ids"]
    reader.mutate(pid, "read")
    fetched = sum(1 for call in client.calls if call[0] == "get_metadata")
    rows, _ = reader.list(10)
    assert sum(1 for call in client.calls if call[0] == "get_metadata") == fetched + 1  # re-read via history
    assert "UNREAD" not in rows[0]["label_ids"]
