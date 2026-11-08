"""Account setup: provider presets, which Google client signs in, and optional account names."""

import json
import os
import stat

import pytest

from email_town_labeler.config import Paths
from email_town_labeler.desktop import setup
from email_town_labeler.desktop.accounts import display_name
from tests.test_desktop_manager import _workbench

DESKTOP = {
    "installed": {
        "client_id": "123456789-abcdefgh12.apps.googleusercontent.com",
        "client_secret": "fixture-secret",
    }
}


def test_presets_are_complete_tls_settings_with_https_help_only():
    seen = set()
    for preset in setup.PRESETS:
        assert not seen & set(preset["domains"]), preset["id"]  # one preset per domain
        seen |= set(preset["domains"])
        assert preset["help_url"] == "" or preset["help_url"].startswith("https://")
        if preset.get("use") == "microsoft":
            continue
        assert preset["port"] == 993 and preset["smtp_security"] in ("ssl", "starttls")
        assert preset["smtp_port"] == (465 if preset["smtp_security"] == "ssl" else 587)
    assert {"gmail.com", "icloud.com", "outlook.com", "qq.com"} <= seen


def test_google_client_import_accepts_desktop_rejects_web_and_is_private(tmp_path):
    paths = Paths.default(tmp_path)
    assert setup.google_client_config(paths) is None
    with pytest.raises(ValueError, match="Web client"):
        setup.save_google_client(paths, {"web": DESKTOP["installed"]})
    for bad in (
        None,
        {"installed": {"client_id": "x", "client_secret": "fixture-secret"}},
        {"installed": {**DESKTOP["installed"], "client_secret": ""}},
    ):
        with pytest.raises(ValueError):
            setup.save_google_client(paths, bad)
    setup.save_google_client(paths, {"installed": {**DESKTOP["installed"], "extra": "dropped"}})
    if os.name == "posix":  # Windows has no POSIX modes; the data folder is in the user's profile
        assert stat.S_IMODE(paths.client_secret.stat().st_mode) == 0o600
    saved = json.loads(paths.client_secret.read_text())
    assert set(saved["installed"]) == {"client_id", "client_secret", "auth_uri", "token_uri", "redirect_uris"}
    assert setup.google_client_config(paths)[1] == "own"


def test_own_client_file_wins_over_the_shipped_client(tmp_path, monkeypatch):
    paths = Paths.default(tmp_path)
    monkeypatch.setattr(setup, "GOOGLE_CLIENT", {"client_id": "1-shipped", "client_secret": "shipped-secret"})
    config, source = setup.google_client_config(paths)
    assert source == "bundled" and config["installed"]["client_id"] == "1-shipped"
    setup.save_google_client(paths, DESKTOP)
    config, source = setup.google_client_config(paths)
    assert source == "own" and config["installed"]["client_id"] == DESKTOP["installed"]["client_id"]
    info = setup.setup_info(paths)
    assert info["google_signin"] and info["presets"] == setup.PRESETS


def test_account_name_is_optional_and_shown_as_the_address(tmp_path, monkeypatch):
    workbench, _readers, _first, _second = _workbench(tmp_path)
    started = []
    monkeypatch.setattr(
        workbench, "_connect", lambda job, name, provider, data: started.append((name, provider))
    )
    monkeypatch.setattr("threading.Thread.start", lambda self: self._target(*self._args))
    for data in (
        {"provider": "imap", "username": "fixture@example.test", "name": "  "},
        {"provider": "gmail"},
        {"provider": "microsoft", "name": "Work"},
    ):
        workbench.connect(data)
        workbench.jobs.clear()  # the recorded job stays "pending" because _connect is replaced
    assert started == [("fixture@example.test", "imap"), ("Gmail", "gmail"), ("Work", "microsoft")]
    assert (
        display_name("Microsoft", "") == "Microsoft" and display_name("Microsoft", "a@b.test") == "a@b.test"
    )
    workbench.close()


def test_gmail_without_any_client_explains_the_alternatives(tmp_path):
    workbench, _readers, _first, _second = _workbench(tmp_path)
    workbench._connect("job", "Gmail", "gmail", {"provider": "gmail"})
    message = workbench.jobs["job"]["message"]
    assert workbench.jobs["job"]["status"] == "error"
    assert "Import a Google OAuth client file" in message and "app password" in message
    workbench.close()


def test_microsoft_uses_the_shipped_client_id_when_none_is_typed(tmp_path, monkeypatch):
    from email_town_labeler.desktop import manager as m

    workbench, _readers, _first, _second = _workbench(tmp_path)
    monkeypatch.setattr(m, "MICROSOFT_CLIENT_ID", "11111111-2222-3333-4444-555555555555")
    asked = []

    def device(client_id):
        asked.append(client_id)
        raise m.ProviderError("fixture stop")

    monkeypatch.setattr(m, "microsoft_device", device)
    workbench._connect("job", "Microsoft", "microsoft", {"provider": "microsoft"})
    workbench._connect("job2", "Microsoft", "microsoft", {"provider": "microsoft", "client_id": "own-id"})
    assert asked == ["11111111-2222-3333-4444-555555555555", "own-id"]
    workbench.close()
