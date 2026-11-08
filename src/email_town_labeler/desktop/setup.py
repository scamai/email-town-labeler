"""Account setup without manual server settings: provider presets and the sign-in clients to use.

Sign-in client order for Gmail: the user's own `client_secret.json` (an explicit choice) wins, then
the client shipped with this copy of Email Town. Installed-app OAuth client secrets are not treated
as confidential by Google, which is why a desktop app can ship one:
https://developers.google.com/identity/protocols/oauth2/native-app
"""

from __future__ import annotations

import json
import re

from ..config import Paths
from ..gmail.auth import _write_private

# Filled in once the project's own Google Cloud / Microsoft Entra registrations exist. Empty means
# "this copy has no shipped sign-in": the UI then offers the user's own client file or app passwords.
GOOGLE_CLIENT: dict[str, str] = {}  # {"client_id": "...apps.googleusercontent.com", "client_secret": "..."}
MICROSOFT_CLIENT_ID = ""

GOOGLE_AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
_GOOGLE_CLIENT_ID = re.compile(r"[0-9]{6,40}-[a-z0-9]{8,64}\.apps\.googleusercontent\.com")

# Suggested IMAP/SMTP settings by address domain. They pre-fill editable fields; nothing is hidden.
# `use: "microsoft"` routes the address to Microsoft sign-in (Outlook.com no longer accepts app passwords).
PRESETS = [
    {
        "id": "gmail",
        "name": "Gmail",
        "domains": ["gmail.com", "googlemail.com"],
        "host": "imap.gmail.com",
        "port": 993,
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 465,
        "smtp_security": "ssl",
        "help": "Create an app password (needs 2-Step Verification). Google sign-in, when set up, also keeps Gmail labels.",
        "help_url": "https://myaccount.google.com/apppasswords",
    },
    {
        "id": "icloud",
        "name": "iCloud Mail",
        "domains": ["icloud.com", "me.com", "mac.com"],
        "host": "imap.mail.me.com",
        "port": 993,
        "smtp_host": "smtp.mail.me.com",
        "smtp_port": 587,
        "smtp_security": "starttls",
        "help": "Create an app-specific password under Sign-In and Security in your Apple Account.",
        "help_url": "https://account.apple.com/",
    },
    {
        "id": "yahoo",
        "name": "Yahoo Mail",
        "domains": ["yahoo.com", "ymail.com", "rocketmail.com"],
        "host": "imap.mail.yahoo.com",
        "port": 993,
        "smtp_host": "smtp.mail.yahoo.com",
        "smtp_port": 465,
        "smtp_security": "ssl",
        "help": "Generate an app password under Account security.",
        "help_url": "https://login.yahoo.com/account/security",
    },
    {
        "id": "aol",
        "name": "AOL Mail",
        "domains": ["aol.com"],
        "host": "imap.aol.com",
        "port": 993,
        "smtp_host": "smtp.aol.com",
        "smtp_port": 465,
        "smtp_security": "ssl",
        "help": "Generate an app password under Account security.",
        "help_url": "https://login.aol.com/account/security",
    },
    {
        "id": "fastmail",
        "name": "Fastmail",
        "domains": ["fastmail.com", "fastmail.fm"],
        "host": "imap.fastmail.com",
        "port": 993,
        "smtp_host": "smtp.fastmail.com",
        "smtp_port": 465,
        "smtp_security": "ssl",
        "help": "Create an app password in Fastmail's privacy and security settings.",
        "help_url": "",
    },
    {
        "id": "zoho",
        "name": "Zoho Mail",
        "domains": ["zoho.com", "zohomail.com"],
        "host": "imap.zoho.com",
        "port": 993,
        "smtp_host": "smtp.zoho.com",
        "smtp_port": 465,
        "smtp_security": "ssl",
        "help": "Turn on IMAP access in Zoho Mail settings and create an app-specific password.",
        "help_url": "",
    },
    {
        "id": "qq",
        "name": "QQ Mail",
        "domains": ["qq.com", "foxmail.com"],
        "host": "imap.qq.com",
        "port": 993,
        "smtp_host": "smtp.qq.com",
        "smtp_port": 465,
        "smtp_security": "ssl",
        "help": "Turn on IMAP/SMTP in QQ Mail settings, then use the authorization code as the password.",
        "help_url": "",
    },
    {
        "id": "microsoft",
        "name": "Outlook.com",
        "domains": ["outlook.com", "hotmail.com", "live.com", "msn.com"],
        "use": "microsoft",
        "help": "Outlook.com no longer accepts app passwords. Use Microsoft sign-in for this address.",
        "help_url": "",
    },
]


def google_client_config(paths: Paths) -> tuple[dict, str] | None:
    """(client config for InstalledAppFlow, "own" | "bundled"), or None when Gmail sign-in is unavailable."""
    if paths.client_secret.is_file():
        try:
            return json.loads(paths.client_secret.read_text()), "own"
        except ValueError:
            return None
    if GOOGLE_CLIENT.get("client_id") and GOOGLE_CLIENT.get("client_secret"):
        return _installed(GOOGLE_CLIENT["client_id"], GOOGLE_CLIENT["client_secret"]), "bundled"
    return None


def _installed(client_id: str, client_secret: str) -> dict:
    return {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": GOOGLE_AUTH_URI,
            "token_uri": GOOGLE_TOKEN_URI,
            "redirect_uris": ["http://localhost"],
        }
    }


def save_google_client(paths: Paths, data: dict) -> dict:
    """Validate a downloaded Google OAuth client file and store it privately as this device's client."""
    if not isinstance(data, dict):
        raise ValueError("Choose the JSON file you downloaded for a Google OAuth client")  # noqa: TRY004 - shown to the user
    if "web" in data and "installed" not in data:
        raise ValueError("This is a Web client. Create a Desktop app client and download its JSON file")
    client = data.get("installed")
    if not isinstance(client, dict):
        raise ValueError("Choose the JSON file you downloaded for a Google OAuth client")  # noqa: TRY004 - shown to the user
    client_id, secret = client.get("client_id"), client.get("client_secret")
    if not isinstance(client_id, str) or not _GOOGLE_CLIENT_ID.fullmatch(client_id):
        raise ValueError("The file has no valid Google client ID")
    if not isinstance(secret, str) or not 8 <= len(secret) <= 200 or not secret.isprintable():
        raise ValueError("The file has no valid client secret")
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    _write_private(paths.client_secret, json.dumps(_installed(client_id, secret)))
    return {"ok": True}


def setup_info(paths: Paths) -> dict:
    google = google_client_config(paths)
    return {
        "google_signin": google is not None,
        "google_source": google[1] if google else None,
        "microsoft_default": bool(MICROSOFT_CLIENT_ID),
        "presets": PRESETS,
    }
