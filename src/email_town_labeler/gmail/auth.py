"""OAuth for a Desktop-app client: local-server flow, plus a headless URL/paste flow with PKCE.

Token and pending-state files are written atomically with mode 0600 where the OS supports it
(the chmod is a no-op on Windows).
"""

from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
HEADLESS_PORT = 8765
HEADLESS_REDIRECT = f"http://localhost:{HEADLESS_PORT}/"
LOGIN_HINT = "run `email-town-labeler login` (or `email-town-labeler login --headless` on a server)"


class AuthError(Exception):
    """Authentication problem the user must fix; the message says how."""


def _write_private(path: Path, text: str) -> None:
    """Atomic write, created 0600 (permissions are best-effort on Windows)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def save_credentials(creds, token_path: Path) -> None:
    _write_private(Path(token_path), creds.to_json())


def _check_secret(client_secret: Path) -> str:
    p = Path(client_secret)
    if not p.is_file():
        raise AuthError(
            f"OAuth client secret not found at {p}; download the Desktop-app JSON "
            "from Google Cloud (see docs) and run `email-town-labeler setup`"
        )
    return str(p)


def load_credentials(token_path: Path):
    """Saved credentials, refreshed (and rewritten) if expired. None if no token file exists.

    Raises AuthError when the token is unreadable, lacks the gmail.modify scope, or its refresh
    token was revoked/expired.
    """
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    token_path = Path(token_path)
    if not token_path.exists():
        return None
    try:
        info = json.loads(token_path.read_text(encoding="utf-8"))
        creds = Credentials.from_authorized_user_info(info)
    except (ValueError, OSError) as e:
        raise AuthError(f"token file {token_path} is unreadable ({type(e).__name__}); {LOGIN_HINT}") from e
    if creds.scopes and not set(SCOPES) <= set(creds.scopes):
        raise AuthError(f"token at {token_path} lacks the gmail.modify scope; {LOGIN_HINT}")
    if creds.valid:
        return creds
    if not creds.refresh_token:
        raise AuthError(f"token at {token_path} expired and has no refresh token; {LOGIN_HINT}")
    try:
        creds.refresh(Request())
    except RefreshError as e:
        raise AuthError(f"Google rejected the saved refresh token (revoked or expired); {LOGIN_HINT}") from e
    save_credentials(creds, token_path)
    return creds


def login_local(client_secret: Path, token_path: Path, *, open_browser: bool = True):
    """Browser flow on a random localhost port. Saves and returns the credentials."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(_check_secret(client_secret), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=open_browser, access_type="offline", prompt="consent")
    save_credentials(creds, token_path)
    return creds


def _flow(client_secret: Path, **kw):
    from google_auth_oauthlib.flow import Flow

    return Flow.from_client_secrets_file(
        _check_secret(client_secret), SCOPES, redirect_uri=HEADLESS_REDIRECT, **kw
    )


def headless_auth_url(client_secret: Path, pending_path: Path) -> str:
    """Start the headless flow: returns a URL to open on any machine. PKCE verifier + state are
    kept in pending_path (0600) until headless_finish."""
    flow = _flow(client_secret, autogenerate_code_verifier=True)
    url, state = flow.authorization_url(access_type="offline", prompt="consent")
    pending = {
        "state": state,
        "code_verifier": flow.code_verifier,
        "redirect_uri": HEADLESS_REDIRECT,
        "created": int(time.time()),
    }
    _write_private(Path(pending_path), json.dumps(pending))
    return url


def headless_finish(client_secret: Path, pending_path: Path, token_path: Path, redirect_url: str):
    """Finish the headless flow with the localhost URL the browser was redirected to.

    The URL's `state` must match the pending one. Saves the token and removes the pending file.
    """
    pending_path = Path(pending_path)
    try:
        pending = json.loads(pending_path.read_text(encoding="utf-8"))
        state, verifier = pending["state"], pending["code_verifier"]
    except (OSError, ValueError, KeyError) as e:
        raise AuthError("no pending headless login; run `email-town-labeler login --headless` first") from e

    q = parse_qs(urlparse(redirect_url.strip()).query)
    if "error" in q:
        raise AuthError(
            f"Google returned error '{q['error'][0]}'; run `email-town-labeler login --headless` again"
        )
    if q.get("state", [None])[0] != state:
        raise AuthError(
            "state mismatch: that URL is not from the latest `email-town-labeler login --headless`; "
            "paste the URL from the most recent attempt"
        )
    code = q.get("code", [None])[0]
    if not code:
        raise AuthError("the pasted URL has no `code` parameter; copy the full localhost URL")

    flow = _flow(client_secret, state=state, code_verifier=verifier, autogenerate_code_verifier=False)
    try:
        flow.fetch_token(code=code)
    except Warning as e:  # oauthlib: "Scope has changed" when the user unticked gmail.modify
        raise AuthError("Gmail modify access was not granted; tick the Gmail permission and retry") from e
    except Exception as e:
        raise AuthError(f"token exchange failed ({type(e).__name__}); {LOGIN_HINT}") from e
    creds = flow.credentials
    save_credentials(creds, token_path)
    pending_path.unlink(missing_ok=True)
    return creds
