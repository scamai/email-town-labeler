"""Provider readers for the local workbench. No adapter archives, sends, marks read or moves mail."""

from __future__ import annotations

import hashlib
import imaplib
import json
import re
import ssl
import time
from datetime import datetime
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ..config import Paths, load_config
from ..gmail.auth import _write_private, save_credentials
from ..gmail.client import GmailClient
from ..gmail.parse import html_to_text
from ..taxonomy import LABEL_CLASSES
from .accounts import password_store


class ProviderError(Exception):
    """Safe, deliberately content-free user-facing error."""


def safe_link(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (ValueError, TypeError):
        return ""
    if (
        parsed.scheme == "https"
        and parsed.hostname
        in (
            "mail.google.com",
            "outlook.office.com",
            "outlook.office365.com",
            "outlook.live.com",
        )
        and not parsed.username
        and not parsed.password
        and port in (None, 443)
    ):
        return value
    return ""


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:24]


class GmailReader:
    def __init__(self, settings: dict):
        import httplib2
        from google.auth.transport.requests import Request as GoogleRequest
        from google.oauth2.credentials import Credentials
        from google_auth_httplib2 import AuthorizedHttp
        from googleapiclient.discovery import build

        paths = Paths(Path(settings["config_dir"]), Path(settings["data_dir"]))
        if not paths.token.is_file():
            raise ProviderError("Gmail is not signed in. Connect the account")
        credentials = Credentials.from_authorized_user_file(str(paths.token))
        if credentials.scopes and not set(credentials.scopes) & {
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.modify",
            "https://mail.google.com/",
        }:
            raise ProviderError("This account has not granted mail-read access. Reconnect it")
        if not credentials.valid:
            credentials.refresh(GoogleRequest())
            save_credentials(credentials, paths.token)
        service = build(
            "gmail",
            "v1",
            http=AuthorizedHttp(credentials, http=httplib2.Http(timeout=20)),
            cache_discovery=False,
        )
        self.client = GmailClient(service, max_retries=2, transport_retries=1)
        self.cfg = load_config(paths.config_file)
        self.address = self.client.profile()["emailAddress"]
        self.identity = fingerprint(self.address.casefold())
        self.cache: dict[str, dict] = {}
        self.history_id = None

    def list(self, limit: int) -> tuple[list[dict], bool]:
        ids = self.client.list_message_ids(label_ids=["INBOX"], max_results=limit + 1)
        chosen = ids[:limit]
        dirty = set(chosen) - self.cache.keys()
        cursor = self.client.profile()["historyId"]
        if self.history_id:
            try:
                events, cursor = self.client.history(self.history_id)
                dirty.update(e.message_id for e in events if e.message_id in chosen)
            except Exception:  # noqa: BLE001 - stale/failed cursor requires a complete metadata refresh
                # No cursor advance on a failed fetch: re-read current metadata before retrying.
                dirty.update(chosen)
        labels = self.client.list_labels()
        expected = {f"{self.cfg.label_prefix}/{v}".casefold(): k for k, v in self.cfg.display_names().items()}
        classes = {
            lb["id"]: expected[lb["name"].casefold()] for lb in labels if lb["name"].casefold() in expected
        }
        if dirty:
            records = self.client.get_metadata(
                [mid for mid in chosen if mid in dirty], ["Subject", "From", "Date"]
            )
            for mid in dirty:
                self.cache.pop(mid, None)
            self.cache.update({r["id"]: r for r in records})
        self.history_id = cursor
        self.cache = {mid: r for mid, r in self.cache.items() if mid in chosen}
        out = []
        for mid in chosen:
            rec = self.cache.get(mid)
            if rec is None:
                continue
            matching = {classes[x] for x in rec.get("label_ids", []) if x in classes}
            category = next((c for c in LABEL_CLASSES if c in matching), None)
            out.append({**rec, "provider_id": f"{self.identity}:{mid}", "category": category})
        return out, len(ids) > limit

    def detail(self, provider_id: str) -> dict:
        identity, mid = provider_id.split(":", 1)
        if identity != self.identity or mid not in self.cache:
            raise ProviderError("Message is no longer in the current list. Refresh")
        rows = self.client.get_records([mid])
        if not rows:
            raise ProviderError("Message was moved or deleted. Refresh")
        return {
            **rows[0],
            "url": safe_link(
                f"https://mail.google.com/mail/?authuser={quote(self.address, safe='')}#all/{quote(mid, safe='')}"
            ),
        }


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ProviderError("The mail server returned an unexpected redirect")


def request_json(
    url: str, *, data: dict | None = None, token: str | None = None, headers: dict | None = None
) -> dict:
    """Only fixed Microsoft origins are permitted; never forward a token through redirects."""
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in ("graph.microsoft.com", "login.microsoftonline.com")
        or parsed.username
        or parsed.port not in (None, 443)
    ):
        raise ProviderError("Untrusted mail API address was rejected")
    h = {"Accept": "application/json", **(headers or {})}
    if token:
        h["Authorization"] = "Bearer " + token
    payload = urlencode(data).encode() if data is not None else None
    if payload is not None:
        h["Content-Type"] = "application/x-www-form-urlencoded"
    try:
        response = build_opener(NoRedirect()).open(Request(url, data=payload, headers=h), timeout=25)
    except HTTPError as e:
        if parsed.hostname == "login.microsoftonline.com":
            try:
                body = json.loads(e.read(1_000_000))
            except (ValueError, OSError):
                body = {}
            # OAuth protocol error codes are safe; descriptions may contain identity details.
            return {"error": str(body.get("error", "oauth_error"))[:80]}
        raise ProviderError(
            f"Microsoft mail read failed (HTTP {e.code}). Reconnect or try again shortly"
        ) from e
    with response:
        raw = response.read(16_000_001)
    if len(raw) > 16_000_000:
        raise ProviderError("Mail response is too large. Load fewer messages")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ProviderError("Invalid mail response format")
    return result


MICROSOFT_TOKEN = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
MICROSOFT_SCOPES = (
    "openid offline_access https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/User.Read"
)


def microsoft_device(client_id: str) -> dict:
    if not re.fullmatch(r"[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}", client_id):
        raise ProviderError("Enter the Microsoft app client ID (UUID) and enable public-client flow")
    data = request_json(
        "https://login.microsoftonline.com/common/oauth2/v2.0/devicecode",
        data={"client_id": client_id, "scope": MICROSOFT_SCOPES},
    )
    if "device_code" not in data:
        raise ProviderError(
            "Could not start Microsoft sign-in. Check the client ID and public-client settings"
        )
    return data


def save_microsoft_token(path: Path, result: dict, previous: dict | None = None):
    if not result.get("access_token"):
        raise ProviderError("Microsoft sign-in expired. Reconnect the account")
    _write_private(
        path,
        json.dumps(
            {
                "access_token": result["access_token"],
                "refresh_token": result.get("refresh_token") or (previous or {}).get("refresh_token"),
                "expires_at": time.time() + int(result.get("expires_in", 3600)),
            }
        ),
    )


class MicrosoftReader:
    def __init__(self, settings: dict):
        self.settings = settings
        self.path = Path(settings["token_path"])
        self.cache = {}
        self.identity = None

    def token(self) -> str:
        saved = json.loads(self.path.read_text())
        if saved.get("expires_at", 0) < time.time() + 60:
            if not saved.get("refresh_token"):
                raise ProviderError("Microsoft sign-in has expired. Reconnect the account")
            fresh = request_json(
                MICROSOFT_TOKEN,
                data={
                    "client_id": self.settings["client_id"],
                    "grant_type": "refresh_token",
                    "refresh_token": saved["refresh_token"],
                    "scope": MICROSOFT_SCOPES,
                },
            )
            save_microsoft_token(self.path, fresh, saved)
            saved = json.loads(self.path.read_text())
        return saved["access_token"]

    def get(self, path: str) -> dict:
        if urlsplit(path).netloc != "graph.microsoft.com" or not urlsplit(path).path.startswith("/v1.0/"):
            raise ProviderError("Microsoft returned an unexpected pagination URL")
        return request_json(
            path,
            token=self.token(),
            headers={"Prefer": 'IdType="ImmutableId", outlook.body-content-type="text"'},
        )

    @staticmethod
    def record(m: dict) -> dict:
        when = datetime.fromisoformat(m["receivedDateTime"].replace("Z", "+00:00")).timestamp()
        sender = (m.get("from") or {}).get("emailAddress") or {}
        return {
            "id": m["id"],
            "internal_date": int(when * 1000),
            "headers": {
                "subject": m.get("subject", ""),
                "from": sender.get("name") or sender.get("address", ""),
            },
            "label_ids": [] if m.get("isRead") else ["UNREAD"],
            "snippet": m.get("bodyPreview", ""),
            "category": None,
        }

    def list(self, limit: int) -> tuple[list[dict], bool]:
        if self.identity is None:
            self.identity = fingerprint(self.get("https://graph.microsoft.com/v1.0/me?$select=id")["id"])
        fields = "id,subject,from,receivedDateTime,isRead,bodyPreview"
        url = "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages?" + urlencode(
            {"$top": min(100, limit + 1), "$select": fields, "$orderby": "receivedDateTime desc"}
        )
        messages = []
        visited = set()
        while url and len(messages) <= limit:
            if url in visited or len(visited) >= 20:
                raise ProviderError("Microsoft pagination did not complete. Refresh shortly")
            visited.add(url)
            page = self.get(url)
            messages.extend(page.get("value", []))
            url = page.get("@odata.nextLink")
        self.cache = {m["id"]: m for m in messages[:limit]}
        return [
            {**self.record(m), "provider_id": f"{self.identity}:{m['id']}"} for m in self.cache.values()
        ], len(messages) > limit or bool(url)

    def detail(self, provider_id: str) -> dict:
        identity, mid = provider_id.split(":", 1)
        if identity != self.identity or mid not in self.cache:
            raise ProviderError("Message is no longer in the current list. Refresh")
        message = self.get(
            f"https://graph.microsoft.com/v1.0/me/messages/{quote(mid, safe='')}?$select=id,body,webLink"
        )
        body = message.get("body") or {}
        text = body.get("content") or ""
        if body.get("contentType", "").lower() == "html":
            text = html_to_text(text)
        return {
            **self.record(self.cache[mid]),
            "body_text": text,
            "url": safe_link(message.get("webLink", "")),
        }


class ImapReader:
    def __init__(self, account: dict, *, password: str | None = None):
        self.account, self.settings = account, account["settings"]
        self.password = password
        self.validity = None
        self.ids: set[str] = set()

    def connect(self):
        s = self.settings
        conn = imaplib.IMAP4_SSL(
            s["host"], int(s["port"]), ssl_context=ssl.create_default_context(), timeout=25
        )
        try:
            conn.login(
                s["username"],
                self.password if self.password is not None else password_store(self.account["id"]),
            )
            status, _ = conn.select("INBOX", readonly=True)
            if status != "OK":
                raise ProviderError("Could not read this account's inbox")
            value = conn.response("UIDVALIDITY")[1]
            if not value or not value[0] or not value[0].isdigit():
                raise ProviderError(
                    "IMAP did not return a valid mailbox identifier; safe organization is unavailable"
                )
            return conn, value[0].decode()
        except BaseException:
            try:
                conn.logout()
            except (OSError, imaplib.IMAP4.error):  # cleanup only; preserve the original connection failure
                pass
            raise

    @staticmethod
    def parse(raw: bytes, uid: str, flags: bytes) -> dict:
        message = BytesParser(policy=policy.default).parsebytes(raw)
        try:
            stamp = parsedate_to_datetime(str(message.get("Date", ""))).timestamp()
        except (ValueError, TypeError, OverflowError):
            stamp = 0
        # INTERNALDATE is receipt time; a sender-controlled Date can be old or in the future.
        received = re.search(rb'INTERNALDATE "([^"]+)"', flags)
        if received:
            try:
                stamp = datetime.strptime(received[1].decode("ascii"), "%d-%b-%Y %H:%M:%S %z").timestamp()
            except (ValueError, UnicodeError):
                pass
        text = ""
        part = message.get_body(preferencelist=("plain", "html")) if message.is_multipart() else message
        if part is not None and part.get_content_maintype() == "text":
            try:
                text = part.get_content()
                if part.get_content_type() == "text/html":
                    text = html_to_text(text)
            except (ValueError, LookupError, KeyError):
                text = ""
        return {
            "id": uid,
            "internal_date": int(stamp * 1000),
            "headers": {"subject": str(message.get("Subject", "")), "from": str(message.get("From", ""))},
            "body_text": str(text)[:262144],
            "label_ids": [] if b"\\Seen" in flags else ["UNREAD"],
            "category": None,
        }

    @staticmethod
    def fetch(conn, uid: bytes, headers_only: bool):
        field = "BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)]" if headers_only else "BODY.PEEK[]<0.262144>"
        status, data = conn.uid("fetch", uid, f"(UID FLAGS INTERNALDATE {field})")
        if status != "OK":
            raise ProviderError("IMAP message read failed. Refresh")
        result = next((entry for entry in data if isinstance(entry, tuple)), None)
        return result

    def list(self, limit: int) -> tuple[list[dict], bool]:
        conn, validity = self.connect()
        try:
            status, response = conn.uid("search", None, "UNDELETED")
            if status != "OK":
                raise ProviderError("IMAP message list read failed")
            ids = (response[0] or b"").split()
            chosen = list(reversed(ids[-limit:]))
            result = []
            for uid in chosen:
                item = self.fetch(conn, uid, True)
                if item:
                    result.append(
                        {
                            **self.parse(item[1], uid.decode(), item[0]),
                            "provider_id": f"{validity}:{uid.decode()}",
                        }
                    )
            self.validity, self.ids = validity, {u.decode() for u in chosen}
            return result, len(ids) > limit
        finally:
            try:
                conn.logout()
            except (OSError, imaplib.IMAP4.error):
                pass  # Retrieval has finished; a disconnected logout must not discard its result.

    def detail(self, provider_id: str) -> dict:
        validity, uid = provider_id.split(":", 1)
        if validity != self.validity or uid not in self.ids or not uid.isdigit():
            raise ProviderError("Message identity changed. Refresh")
        conn, current = self.connect()
        try:
            if current != validity:
                raise ProviderError("The mailbox was rebuilt. Refresh and try again")
            item = self.fetch(conn, uid.encode(), False)
            if not item:
                raise ProviderError("Message was moved or deleted. Refresh")
            return self.parse(item[1], uid, item[0])
        finally:
            try:
                conn.logout()
            except (OSError, imaplib.IMAP4.error):
                pass  # Retrieval has finished; a disconnected logout must not discard its result.


def create_reader(account: dict):
    if account["provider"] == "gmail":
        return GmailReader(account["settings"])
    if account["provider"] == "microsoft":
        return MicrosoftReader(account["settings"])
    if account["provider"] == "imap":
        return ImapReader(account)
    raise ProviderError("Unknown email provider")
