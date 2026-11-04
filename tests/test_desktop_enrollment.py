import os
from pathlib import Path

import pytest

from email_town_labeler.config import Paths
from email_town_labeler.desktop import manager as desktop_manager
from email_town_labeler.desktop.manager import Workbench
from email_town_labeler.desktop.providers import ProviderError


class NoWait:
    def is_set(self):
        return False

    def wait(self, _seconds):
        return False

    def set(self):
        return None


class IdentityReader:
    def __init__(self, identity="verified-mailbox"):
        self.identity = identity

    def list(self, _limit):
        return [], False


def make_workbench(tmp_path: Path, reader=None):
    paths = Paths.default(tmp_path / "config").ensure()
    return Workbench(
        tmp_path / "workbench",
        paths,
        reader_factory=lambda _account: reader or IdentityReader(),
        classifier=object(),
    )


def test_microsoft_device_flow_handles_pending_slow_down_and_saves_private_token(tmp_path, monkeypatch):
    workbench = make_workbench(tmp_path)
    calls = []
    replies = iter(
        [
            {"error": "authorization_pending"},
            {"error": "slow_down"},
            {"access_token": "access-secret", "refresh_token": "refresh-secret", "expires_in": 3600},
        ]
    )
    monkeypatch.setattr(
        desktop_manager,
        "microsoft_device",
        lambda client_id: {
            "client_id": client_id,
            "device_code": "device-secret",
            "user_code": "ABCD-EFGH",
            "verification_uri": "https://microsoft.com/devicelogin",
            "expires_in": 600,
            "interval": 1,
        },
    )

    def request(url, *, data):
        calls.append((url, data))
        return next(replies)

    monkeypatch.setattr(desktop_manager, "request_json", request)
    monkeypatch.setattr(desktop_manager.time, "monotonic", iter([0, 1, 2, 3, 4, 5]).__next__)
    workbench.stop = NoWait()
    job = "job-ms"
    workbench.jobs[job] = {"id": job, "status": "pending"}
    workbench._connect(job, "Work Microsoft", "microsoft", {"client_id": "client-id"})
    account = workbench.accounts.all()[0]
    token_path = Path(account["settings"]["token_path"])
    assert workbench.jobs[job]["status"] == "complete"
    assert len(calls) == 3
    assert calls[0][0] == desktop_manager.MICROSOFT_TOKEN
    assert calls[0][1]["device_code"] == "device-secret"
    # _write_private guarantees POSIX mode bits only where supported; Windows uses ACLs.
    if os.name != "nt":
        assert token_path.stat().st_mode & 0o077 == 0
    token_text = token_path.read_text()
    assert "access-secret" in token_text and "refresh-secret" in token_text
    workbench.close()


def test_reconnect_wrong_identity_preserves_existing_settings_and_duplicate_rejected(tmp_path, monkeypatch):
    reader = IdentityReader("original")
    workbench = make_workbench(tmp_path, reader)
    original = workbench.accounts.add(
        "Original", "microsoft", {"client_id": "client", "token_path": str(tmp_path / "old.json")}
    )
    workbench.accounts.bind_identity(original["id"], "original")
    other = workbench.accounts.add(
        "Other", "microsoft", {"client_id": "client", "token_path": str(tmp_path / "other.json")}
    )
    workbench.accounts.bind_identity(other["id"], "other")

    monkeypatch.setattr(
        desktop_manager,
        "microsoft_device",
        lambda _client: {
            "device_code": "d",
            "user_code": "c",
            "verification_uri": "https://microsoft.com/devicelogin",
            "expires_in": 20,
            "interval": 5,
        },
    )
    monkeypatch.setattr(desktop_manager, "request_json", lambda *_args, **_kwargs: {"error": "access_denied"})
    workbench.stop = NoWait()
    job = "wrong"
    workbench.jobs[job] = {"id": job, "status": "pending"}
    workbench._connect(
        job,
        "Changed",
        "microsoft",
        {"reconnect_id": original["id"], "client_id": "client"},
    )
    assert workbench.jobs[job]["status"] == "error"
    assert workbench.accounts.get(original["id"])["settings"]["token_path"] == str(tmp_path / "old.json")
    reader.identity = "wrong-mailbox"
    original_before = workbench.accounts.get(original["id"])
    with pytest.raises(ProviderError, match="not the original mailbox"):
        workbench._enroll_verified(
            "Changed", "microsoft", {"token_path": "new"}, original["id"], original_before
        )
    assert workbench.accounts.get(original["id"]) == original_before
    reader.identity = "original"
    with pytest.raises(ProviderError, match="already connected"):
        workbench._enroll_verified("Duplicate", "microsoft", {"x": "y"}, "new", {"id": "new"})
    workbench.close()


def test_imap_password_is_stored_only_after_success_and_reconnect_keeps_mailbox(tmp_path, monkeypatch):
    workbench = make_workbench(tmp_path)
    attempts = []

    class Reader:
        def __init__(self, _account, *, password):
            self.password = password

        def list(self, _limit):
            attempts.append(self.password)
            if len(attempts) == 1:
                raise ProviderError("synthetic failure")
            self.password = None
            return [], False

    stored = []
    monkeypatch.setattr(desktop_manager, "ImapReader", Reader)
    monkeypatch.setattr(
        desktop_manager, "password_store", lambda aid, password: stored.append((aid, password))
    )
    first = "imap-fail"
    workbench.jobs[first] = {"id": first, "status": "pending"}
    workbench._connect(
        first, "IMAP", "imap", {"host": "imap.example", "port": 993, "username": "u", "password": "secret"}
    )
    assert stored == []
    assert workbench.accounts.all() == []
    second = "imap-ok"
    workbench.jobs[second] = {"id": second, "status": "pending"}
    workbench._connect(
        second, "IMAP", "imap", {"host": "imap.example", "port": 993, "username": "u", "password": "secret"}
    )
    assert len(stored) == 1 and stored[0][1] == "secret"
    account = workbench.accounts.all()[0]
    assert account["settings"] == {"host": "imap.example", "port": 993, "username": "u"}
    workbench.jobs["reconnect"] = {"status": "pending"}
    workbench._connect(
        "reconnect",
        "Changed",
        "imap",
        {
            "reconnect_id": account["id"],
            "host": "other.example",
            "port": 993,
            "username": "u",
            "password": "new-secret",
        },
    )
    assert workbench.jobs["reconnect"]["status"] == "error"
    assert len(stored) == 1
    assert workbench.accounts.get(account["id"])["settings"]["host"] == "imap.example"
    assert attempts == ["secret", "secret"]
    workbench.close()
