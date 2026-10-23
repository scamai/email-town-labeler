"""gmail.auth file handling and flow plumbing. Google endpoints are never contacted."""

import datetime as dt
import json
import os
import stat
import sys
import time
from urllib.parse import parse_qs, urlparse

import pytest
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow, InstalledAppFlow

from email_town_labeler.gmail import auth

POSIX = sys.platform != "win32"


@pytest.fixture
def secret(tmp_path):
    p = tmp_path / "client_secret.json"
    p.write_text(
        json.dumps(
            {
                "installed": {
                    "client_id": "test-client.apps.googleusercontent.com",
                    "client_secret": "test-secret",
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": ["http://localhost"],
                }
            }
        )
    )
    return p


def creds(expired=False, refresh_token="rt", scopes=auth.SCOPES):
    exp = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) + dt.timedelta(hours=-1 if expired else 1)
    return Credentials(
        token="at",
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id="cid",
        client_secret="cs",
        scopes=scopes,
        expiry=exp,
    )


def mode(p):
    return stat.S_IMODE(os.stat(p).st_mode)


@pytest.mark.skipif(not POSIX, reason="POSIX permissions")
def test_token_written_0600_even_over_existing_file(tmp_path):
    tok = tmp_path / "sub" / "token.json"
    tok.parent.mkdir()
    tok.write_text("{}")
    os.chmod(tok, 0o644)
    auth.save_credentials(creds(), tok)
    assert mode(tok) == 0o600 and json.loads(tok.read_text())["refresh_token"] == "rt"
    assert [p.name for p in tok.parent.iterdir()] == ["token.json"]  # no temp files left


def test_load_missing_returns_none(tmp_path):
    assert auth.load_credentials(tmp_path / "nope.json") is None


def test_load_valid_does_not_refresh(tmp_path, monkeypatch):
    tok = tmp_path / "token.json"
    auth.save_credentials(creds(), tok)
    monkeypatch.setattr(Credentials, "refresh", lambda self, req: pytest.fail("refreshed"))
    assert auth.load_credentials(tok).token == "at"


def test_load_expired_refreshes_and_rewrites(tmp_path, monkeypatch):
    tok = tmp_path / "token.json"
    auth.save_credentials(creds(expired=True), tok)

    def refresh(self, req):
        self.token = "fresh"
        self.expiry = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) + dt.timedelta(hours=1)

    monkeypatch.setattr(Credentials, "refresh", refresh)
    c = auth.load_credentials(tok)
    assert c.token == "fresh" and json.loads(tok.read_text())["token"] == "fresh"
    if POSIX:
        assert mode(tok) == 0o600


def test_revoked_refresh_token_gives_clear_error(tmp_path, monkeypatch):
    tok = tmp_path / "token.json"
    auth.save_credentials(creds(expired=True), tok)

    def refresh(self, req):
        raise RefreshError("invalid_grant: Token has been expired or revoked.")

    monkeypatch.setattr(Credentials, "refresh", refresh)
    with pytest.raises(auth.AuthError, match="email-town-labeler login"):
        auth.load_credentials(tok)


def test_expired_without_refresh_token(tmp_path):
    tok = tmp_path / "token.json"
    auth.save_credentials(creds(expired=True, refresh_token=None), tok)
    with pytest.raises(auth.AuthError, match="email-town-labeler login"):
        auth.load_credentials(tok)


def test_corrupt_token_and_wrong_scope(tmp_path):
    tok = tmp_path / "token.json"
    tok.write_text("not json")
    with pytest.raises(auth.AuthError, match="unreadable"):
        auth.load_credentials(tok)
    auth.save_credentials(creds(scopes=["https://www.googleapis.com/auth/gmail.readonly"]), tok)
    with pytest.raises(auth.AuthError, match="gmail.modify"):
        auth.load_credentials(tok)


def test_login_local_uses_random_port_and_saves(secret, tmp_path, monkeypatch):
    seen = {}

    def run_local_server(self, **kw):
        seen.update(kw)
        return creds()

    monkeypatch.setattr(InstalledAppFlow, "run_local_server", run_local_server)
    tok = tmp_path / "token.json"
    auth.login_local(secret, tok, open_browser=False)
    assert seen["port"] == 0 and seen["open_browser"] is False
    assert json.loads(tok.read_text())["token"] == "at"


def test_missing_client_secret(tmp_path):
    with pytest.raises(auth.AuthError, match="client secret"):
        auth.login_local(tmp_path / "missing.json", tmp_path / "t.json")
    with pytest.raises(auth.AuthError, match="client secret"):
        auth.headless_auth_url(tmp_path / "missing.json", tmp_path / "p.json")


def start_headless(secret, tmp_path):
    pending = tmp_path / "pending.json"
    url = auth.headless_auth_url(secret, pending)
    return url, pending, json.loads(pending.read_text())


def test_headless_url_uses_pkce_and_localhost(secret, tmp_path):
    url, pending, p = start_headless(secret, tmp_path)
    q = parse_qs(urlparse(url).query)
    assert q["redirect_uri"] == ["http://localhost:8765/"]
    assert q["code_challenge_method"] == ["S256"] and q["code_challenge"][0]
    assert q["access_type"] == ["offline"] and q["scope"] == auth.SCOPES
    assert q["state"] == [p["state"]] and len(p["code_verifier"]) >= 43
    assert p["code_verifier"] not in url  # only the challenge leaves the machine
    if POSIX:
        assert mode(pending) == 0o600


def test_headless_state_mismatch_rejected_and_pending_kept(secret, tmp_path):
    _, pending, _ = start_headless(secret, tmp_path)
    with pytest.raises(auth.AuthError, match="state mismatch"):
        auth.headless_finish(
            secret, pending, tmp_path / "t.json", "http://localhost:8765/?state=forged&code=abc"
        )
    assert pending.exists() and not (tmp_path / "t.json").exists()


def test_headless_error_and_missing_code(secret, tmp_path):
    _, pending, p = start_headless(secret, tmp_path)
    with pytest.raises(auth.AuthError, match="access_denied"):
        auth.headless_finish(
            secret,
            pending,
            tmp_path / "t.json",
            f"http://localhost:8765/?error=access_denied&state={p['state']}",
        )
    with pytest.raises(auth.AuthError, match="code"):
        auth.headless_finish(
            secret, pending, tmp_path / "t.json", f"http://localhost:8765/?state={p['state']}"
        )


def test_headless_finish_without_pending(secret, tmp_path):
    with pytest.raises(auth.AuthError, match="login --headless"):
        auth.headless_finish(
            secret, tmp_path / "none.json", tmp_path / "t.json", "http://localhost:8765/?code=x"
        )


def test_headless_finish_exchanges_code_with_verifier(secret, tmp_path, monkeypatch):
    _, pending, p = start_headless(secret, tmp_path)
    seen = {}

    def fetch_token(self, **kw):
        seen.update(kw, verifier=self.code_verifier)
        self.oauth2session.token = {
            "access_token": "at2",
            "refresh_token": "rt2",
            "token_type": "Bearer",
            "expires_in": 3600,
            "expires_at": time.time() + 3600,
        }

    monkeypatch.setattr(Flow, "fetch_token", fetch_token)
    tok = tmp_path / "token.json"
    c = auth.headless_finish(
        secret, pending, tok, f" http://localhost:8765/?state={p['state']}&code=4%2Fabc&scope=x "
    )
    assert seen["code"] == "4/abc" and seen["verifier"] == p["code_verifier"]
    assert c.token == "at2" and json.loads(tok.read_text())["refresh_token"] == "rt2"
    assert not pending.exists()
    if POSIX:
        assert mode(tok) == 0o600


def test_headless_exchange_failure_is_auth_error(secret, tmp_path, monkeypatch):
    _, pending, p = start_headless(secret, tmp_path)

    def boom(self, **kw):
        raise ValueError("invalid_grant")

    monkeypatch.setattr(Flow, "fetch_token", boom)
    with pytest.raises(auth.AuthError, match="token exchange failed"):
        auth.headless_finish(
            secret, pending, tmp_path / "t.json", f"http://localhost:8765/?state={p['state']}&code=c"
        )
