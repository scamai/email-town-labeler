"""Account-bound mail adapters. Reads never mark mail read; writes follow app actions (opening a message is one)."""

from __future__ import annotations

import base64
import hashlib
import imaplib
import json
import re
import smtplib
import ssl
import time
from datetime import datetime
from email import policy
from email.parser import BytesParser
from email.utils import formataddr, parsedate_to_datetime
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
from .mail import FOLDERS, MAX_ATTACHMENTS, addresses, mime_details, mime_message


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


# Gmail meters 6,000 quota units per user per minute, shared with the CLI service; a list row
# (metadata) costs 20 and a body (full) 60. The CLI paces messages evenly (240 metadata a minute),
# which made a cold 100-row list wait about 12.5 s for its second batch. A person at the screen is
# better served by spending the minute's budget at once: 100 rows (2,000 units) arrive in a few
# round trips, and only a fourth cold folder in the same minute waits for refill. 1,200 units a
# minute stay free for the CLI service.
GMAIL_BATCH = 25
GMAIL_UNITS_PER_MINUTE = 4800
GMAIL_RETRIES = 4


class GmailReader:
    # list() reports rows batch by batch, so a cold list shows its newest rows within a second.
    progressive = True
    # Read ahead the newest 30 bodies, in batches, like a desktop client: an open must not wait on the
    # network. A body is fetched once per process (the body cache keeps it), so a refresh costs nothing.
    read_ahead = 30

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
        self.credentials, self.token_path = credentials, paths.token
        self.scopes = set(credentials.granted_scopes or credentials.scopes or [])
        self.client = GmailClient(
            service,
            max_retries=GMAIL_RETRIES,
            transport_retries=1,
            units_per_minute=GMAIL_UNITS_PER_MINUTE,
            batch_size=GMAIL_BATCH,
        )
        self.cfg = load_config(paths.config_file)
        self.address = self.client.profile()["emailAddress"]
        self.identity = fingerprint(self.address.casefold())
        self.cache: dict[str, dict] = {}
        self.folder_ids: dict[str, list[str]] = {}
        self.history_id = None

    # Header metadata fields kept across restarts (format=metadata has no snippet or body).
    STATE_FIELDS = ("id", "thread_id", "label_ids", "internal_date", "history_id", "headers")

    def export_state(self) -> dict:
        """Resumable list state for the on-disk cache: metadata rows and the history cursor."""
        return {
            "identity": self.identity,
            "history_id": self.history_id,
            "folder_ids": self.folder_ids,
            "cache": {
                mid: {k: rec[k] for k in self.STATE_FIELDS if k in rec} for mid, rec in self.cache.items()
            },
        }

    def restore_state(self, state: dict) -> None:
        """Resume from export_state(); the next list re-reads only what Gmail history says changed."""
        if not state or state.get("identity") != self.identity or not state.get("history_id"):
            return  # another mailbox, or nothing to resume from: start cold
        self.history_id = str(state["history_id"])
        self.folder_ids = {f: list(ids) for f, ids in state.get("folder_ids", {}).items() if f in FOLDERS}
        self.cache = {mid: dict(rec) for mid, rec in state.get("cache", {}).items()}

    def capabilities(self):
        write = bool(
            self.scopes & {"https://www.googleapis.com/auth/gmail.modify", "https://mail.google.com/"}
        )
        send = write or "https://www.googleapis.com/auth/gmail.send" in self.scopes
        return {
            "write": write,
            "send": send,
            "attachments": True,
            "max_attachment_bytes": MAX_ATTACHMENTS,
            "reason": "" if write and send else "Reconnect and grant permission to send and manage mail",
        }

    def list(self, limit: int, folder="inbox", progress=None) -> tuple[list[dict], bool]:
        if folder not in FOLDERS:
            raise ProviderError("Unsupported folder")
        args = (
            {"label_ids": [{"inbox": "INBOX", "sent": "SENT", "trash": "TRASH"}[folder]]}
            if folder != "archive"
            else {"query": "-in:inbox -in:sent -in:trash -in:spam -in:drafts"}
        )
        if folder == "trash":
            args["include_spam_trash"] = True
        ids = self.client.list_message_ids(**args, max_results=limit + 1)
        chosen = ids[:limit]
        cursor = self.client.profile()["historyId"]
        if self.history_id:
            try:
                events, cursor = self.client.history(self.history_id)
                # History is mailbox-wide, so this also keeps the other folders' rows current.
                for event in events:
                    self.cache.pop(event.message_id, None)
            except Exception:  # noqa: BLE001 - stale/failed cursor requires a complete metadata refresh
                # No cursor advance on a failed fetch: re-read current metadata before retrying.
                self.cache.clear()
        labels = self.client.list_labels()
        expected = {f"{self.cfg.label_prefix}/{v}".casefold(): k for k, v in self.cfg.display_names().items()}
        classes = {
            lb["id"]: expected[lb["name"].casefold()] for lb in labels if lb["name"].casefold() in expected
        }
        dirty = [mid for mid in chosen if mid not in self.cache]
        for start in range(0, len(dirty), GMAIL_BATCH):
            records = self.client.get_metadata(
                dirty[start : start + GMAIL_BATCH], ["Subject", "From", "Date"]
            )
            self.cache.update({r["id"]: r for r in records})
            if progress and start + GMAIL_BATCH < len(dirty):
                progress(self._rows(chosen, classes))
        self.history_id = cursor
        # Keep every folder's last window, so switching back re-reads only what history changed.
        self.folder_ids[folder] = chosen
        keep = {mid for window in self.folder_ids.values() for mid in window}
        self.cache = {mid: r for mid, r in self.cache.items() if mid in keep}
        return self._rows(chosen, classes), len(ids) > limit

    def _rows(self, chosen: list[str], classes: dict[str, str]) -> list[dict]:
        out = []
        for mid in chosen:
            rec = self.cache.get(mid)
            if rec is None:
                continue
            matching = {classes[x] for x in rec.get("label_ids", []) if x in classes}
            category = next((c for c in LABEL_CLASSES if c in matching), None)
            out.append({**rec, "provider_id": f"{self.identity}:{mid}", "category": category})
        return out

    def detail(self, provider_id: str) -> dict:
        identity, mid = provider_id.split(":", 1)
        if identity != self.identity or mid not in self.cache:
            raise ProviderError("Message is no longer in the current list. Refresh")
        found = self.details([provider_id])
        if provider_id not in found:
            raise ProviderError("Message was moved or deleted. Refresh")
        return found[provider_id]

    def details(self, provider_ids: list[str]) -> dict[str, dict]:
        """Full messages in batched requests; ids that left the list or were deleted are omitted."""
        wanted = {}
        for provider_id in provider_ids:
            identity, mid = provider_id.split(":", 1)
            if identity == self.identity and mid in self.cache:
                wanted[mid] = provider_id
        rows = self.client.get_records(list(wanted), include_html=True) if wanted else []
        return {wanted[row["id"]]: self._detail(row) for row in rows if row["id"] in wanted}

    def _detail(self, row: dict) -> dict:
        mid = row["id"]
        return {
            **row,
            "attachments": [
                {
                    "id": a.get("id", ""),
                    "name": a.get("filename") or "Inline image",
                    "type": a.get("mime", "application/octet-stream"),
                    "size": a.get("size", 0),
                    "content_id": a.get("content_id", ""),
                }
                for a in row.get("attachments", [])
                if a.get("id")
            ],
            "url": safe_link(
                f"https://mail.google.com/mail/?authuser={quote(self.address, safe='')}#all/{quote(mid, safe='')}"
            ),
        }

    def _mid(self, provider_id):
        identity, mid = provider_id.split(":", 1)
        if identity != self.identity or mid not in self.cache:
            raise ProviderError("Message is no longer in this account's current list. Refresh")
        return mid

    def write(self, path, payload):
        from google.auth.transport.requests import Request as GoogleRequest

        if not self.credentials.valid:
            self.credentials.refresh(GoogleRequest())
            save_credentials(self.credentials, self.token_path)
        # httplib2 retries a disconnected POST inside _conn_request even when
        # googleapiclient execute(num_retries=0). Use one-shot urllib for writes:
        # https://github.com/httplib2/httplib2/blob/master/python3/httplib2/__init__.py
        # A lost response is an UNKNOWN receipt, never an automatic resend.
        request = Request(
            "https://gmail.googleapis.com/gmail/v1/users/me/" + path,
            data=json.dumps(payload).encode(),
            method="POST",
            headers={"Authorization": "Bearer " + self.credentials.token, "Content-Type": "application/json"},
        )
        with build_opener(NoRedirect()).open(request, timeout=25) as response:
            raw = response.read(1_000_001)
        if len(raw) > 1_000_000:
            raise ProviderError("Mail operation returned an unexpected response. Check the mailbox")
        return json.loads(raw) if raw else {}

    def mutate(self, provider_id, action):
        if not self.capabilities()["write"]:
            raise ProviderError(self.capabilities()["reason"])
        mid = quote(self._mid(provider_id), safe="")
        if action in ("trash", "restore"):
            self.write("messages/" + mid + ("/trash" if action == "trash" else "/untrash"), {})
            if action == "restore":
                self.write("messages/" + mid + "/modify", {"addLabelIds": ["INBOX"]})
        else:
            labels = {
                "archive": ([], ["INBOX"]),
                "read": ([], ["UNREAD"]),
                "unread": (["UNREAD"], []),
                "star": (["STARRED"], []),
                "unstar": ([], ["STARRED"]),
            }
            if action not in labels:
                raise ProviderError("Unsupported mail action")
            add, remove = labels[action]
            self.write("messages/" + mid + "/modify", {"addLabelIds": add, "removeLabelIds": remove})
        # The row stays cached so a second write (read then unread, trash then undo) still resolves.
        # The next list() re-reads it anyway: history reports our own label changes too.

    def send(self, data, original=None):
        if not self.capabilities()["send"]:
            raise ProviderError(self.capabilities()["reason"])
        msg = mime_message(data, self.address, original)
        if data["bcc"]:
            msg["Bcc"] = data["bcc"]
        payload = {"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode()}
        if original and original.get("thread_id"):
            payload["threadId"] = original["thread_id"]
        self.write("messages/send", payload)
        return {"message": "Sent. Gmail saved a copy in Sent."}

    def attachment(self, provider_id, attachment_id):
        mid = self._mid(provider_id)
        details = self.detail(provider_id)
        meta = next((a for a in details["attachments"] if a["id"] == attachment_id), None)
        if not meta or meta["size"] > MAX_ATTACHMENTS:
            raise ProviderError("Attachment is unavailable or exceeds the 20 MB download limit")
        api = self.client.service.users().messages()
        if attachment_id.startswith("part:"):
            raw = api.get(userId="me", id=mid, format="full").execute(num_retries=0)

            def find(part):
                if "part:" + part.get("partId", "") == attachment_id:
                    return part.get("body", {}).get("data", "")
                return next((value for child in part.get("parts", []) if (value := find(child))), "")

            encoded = find(raw.get("payload", {}))
        else:
            encoded = (
                api.attachments()
                .get(userId="me", messageId=mid, id=attachment_id)
                .execute(num_retries=0)
                .get("data", "")
            )
        raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        if len(raw) > MAX_ATTACHMENTS:
            raise ProviderError("Attachment exceeds the download limit")
        return meta, raw


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ProviderError("The mail server returned an unexpected redirect")


def request_json(
    url: str,
    *,
    data: dict | None = None,
    token: str | None = None,
    headers: dict | None = None,
    method: str | None = None,
    json_body: dict | None = None,
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
    if json_body is not None:
        payload = json.dumps(json_body).encode()
        h["Content-Type"] = "application/json"
    try:
        response = build_opener(NoRedirect()).open(
            Request(url, data=payload, headers=h, method=method), timeout=25
        )
    except HTTPError as e:
        if parsed.hostname == "login.microsoftonline.com":
            try:
                body = json.loads(e.read(1_000_000))
            except (ValueError, OSError):
                body = {}
            # OAuth protocol error codes are safe; descriptions may contain identity details.
            return {"error": str(body.get("error", "oauth_error"))[:80]}
        raise ProviderError(
            f"Microsoft mail request failed (HTTP {e.code}). Reconnect or try again shortly"
        ) from e
    with response:
        raw = response.read(16_000_001)
    if len(raw) > 16_000_000:
        raise ProviderError("Mail response is too large. Load fewer messages")
    result = json.loads(raw) if raw else {}
    if not isinstance(result, dict):
        raise ProviderError("Invalid mail response format")
    return result


MICROSOFT_TOKEN = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
MICROSOFT_SCOPES = "openid offline_access https://graph.microsoft.com/Mail.ReadWrite https://graph.microsoft.com/Mail.Send https://graph.microsoft.com/User.Read"


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
                "scope": result.get("scope", (previous or {}).get("scope", "")),
            }
        ),
    )


class MicrosoftReader:
    def __init__(self, settings: dict):
        self.settings = settings
        self.path = Path(settings["token_path"])
        self.cache = {}
        self.identity = None
        self.address = ""

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
                    "scope": saved.get("scope")
                    or "openid offline_access https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/User.Read",
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

    def capabilities(self):
        saved = json.loads(self.path.read_text())
        scopes = {s.rsplit("/", 1)[-1].lower() for s in saved.get("scope", "").split()}
        return {
            "write": "mail.readwrite" in scopes,
            "send": "mail.send" in scopes,
            "attachments": True,
            "max_attachment_bytes": 2 * 1024 * 1024,
            "reason": "Reconnect to grant Mail.ReadWrite and Mail.Send"
            if not {"mail.readwrite", "mail.send"} <= scopes
            else "",
        }

    def write(self, path, payload, method="POST"):
        return request_json(
            "https://graph.microsoft.com/v1.0/me/" + path,
            token=self.token(),
            method=method,
            json_body=payload,
            headers={"Prefer": 'IdType="ImmutableId"'},
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
                "from": formataddr((sender.get("name", ""), sender.get("address", ""))),
            },
            "label_ids": ([] if m.get("isRead") else ["UNREAD"])
            + (["STARRED"] if (m.get("flag") or {}).get("flagStatus") == "flagged" else []),
            "snippet": m.get("bodyPreview", ""),
            "category": None,
        }

    def list(self, limit: int, folder="inbox") -> tuple[list[dict], bool]:
        if folder not in FOLDERS:
            raise ProviderError("Unsupported folder")
        if self.identity is None:
            profile = self.get("https://graph.microsoft.com/v1.0/me?$select=id,mail,userPrincipalName")
            self.identity = fingerprint(profile["id"])
            self.address = profile.get("mail") or profile.get("userPrincipalName", "")
        folder_name = {"inbox": "inbox", "sent": "sentitems", "archive": "archive", "trash": "deleteditems"}[
            folder
        ]
        fields = "id,subject,from,receivedDateTime,isRead,bodyPreview,flag"
        url = f"https://graph.microsoft.com/v1.0/me/mailFolders/{folder_name}/messages?" + urlencode(
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
            f"https://graph.microsoft.com/v1.0/me/messages/{quote(mid, safe='')}?$select=id,body,webLink,from,toRecipients,ccRecipients,replyTo,internetMessageId,internetMessageHeaders,hasAttachments"
        )
        body = message.get("body") or {}
        text = body.get("content") or ""
        html = ""
        if body.get("contentType", "").lower() == "html":
            html = text[:262144]
            text = html_to_text(text)
        # Graph hasAttachments excludes inline attachments; CID-only mail still needs them.
        # https://learn.microsoft.com/en-us/graph/api/resources/message
        attachments = (
            self._attachments(mid) if message.get("hasAttachments") or "cid:" in html.lower() else []
        )
        return {
            **self.record(self.cache[mid]),
            "body_text": text,
            "body_html": html,
            "headers": {
                **self.record(self.cache[mid])["headers"],
                **{
                    k: ", ".join(
                        formataddr(
                            (
                                r.get("emailAddress", {}).get("name", ""),
                                r.get("emailAddress", {}).get("address", ""),
                            )
                        )
                        for r in message.get(field, [])
                    )
                    for k, field in (("to", "toRecipients"), ("cc", "ccRecipients"), ("reply-to", "replyTo"))
                },
                "message-id": message.get("internetMessageId", ""),
                "references": next(
                    (
                        h.get("value", "")
                        for h in message.get("internetMessageHeaders", [])
                        if h.get("name", "").lower() == "references"
                    ),
                    "",
                ),
            },
            "attachments": attachments,
            "url": safe_link(message.get("webLink", "")),
        }

    def _mid(self, provider_id):
        identity, mid = provider_id.split(":", 1)
        if identity != self.identity or mid not in self.cache:
            raise ProviderError("Message is no longer in this account's current list. Refresh")
        return mid

    def _attachments(self, mid):
        result, seen = [], set()
        url = f"https://graph.microsoft.com/v1.0/me/messages/{quote(mid, safe='')}/attachments?$select=id,name,size,contentType,contentId"
        while url:
            if url in seen or len(seen) > 20:
                raise ProviderError("Attachment list is too large")
            seen.add(url)
            page = self.get(url)
            result.extend(
                {
                    "id": a["id"],
                    "name": a.get("name", "attachment"),
                    "size": a.get("size", 0),
                    "type": a.get("contentType", "application/octet-stream"),
                    "content_id": a.get("contentId", ""),
                }
                for a in page.get("value", [])
                if a.get("@odata.type", "#microsoft.graph.fileAttachment")
                == "#microsoft.graph.fileAttachment"
            )
            url = page.get("@odata.nextLink")
        return result

    def attachment(self, provider_id, attachment_id):
        mid = self._mid(provider_id)
        meta = next((a for a in self._attachments(mid) if a["id"] == attachment_id), None)
        if not meta or meta["size"] > 8 * 1024 * 1024:
            raise ProviderError("Attachment unavailable or larger than the 8 MB download limit")
        result = self.get(
            f"https://graph.microsoft.com/v1.0/me/messages/{quote(mid, safe='')}/attachments/{quote(attachment_id, safe='')}"
        )
        return meta, base64.b64decode(result.get("contentBytes", ""), validate=True)

    def mutate(self, provider_id, action):
        if not self.capabilities()["write"]:
            raise ProviderError(self.capabilities()["reason"])
        mid = self._mid(provider_id)
        path = "messages/" + quote(mid, safe="")
        if action in ("trash", "archive", "restore"):
            self.write(
                path + "/move",
                {
                    "destinationId": {"trash": "deleteditems", "archive": "archive", "restore": "inbox"}[
                        action
                    ]
                },
            )
        elif action in ("read", "unread"):
            self.write(path, {"isRead": action == "read"}, "PATCH")
        elif action in ("star", "unstar"):
            self.write(
                path, {"flag": {"flagStatus": "flagged" if action == "star" else "notFlagged"}}, "PATCH"
            )
        else:
            raise ProviderError("Unsupported mail action")

    def send(self, data, original=None):
        if not self.capabilities()["send"]:
            raise ProviderError(self.capabilities()["reason"])
        msg = {
            "subject": data["subject"],
            "body": {"contentType": "Text", "content": data["body"]},
            **{
                field: [{"emailAddress": {"address": a}} for a in addresses(data[key])]
                for key, field in (("to", "toRecipients"), ("cc", "ccRecipients"), ("bcc", "bccRecipients"))
            },
            "attachments": [
                {
                    "@odata.type": "#microsoft.graph.fileAttachment",
                    "name": a["name"],
                    "contentType": a["type"],
                    "contentBytes": a["data"],
                }
                for a in data["attachments"]
            ],
        }
        if original:
            if not self.capabilities()["write"]:
                raise ProviderError("Reply with attachments requires Mail.ReadWrite. Reconnect this account")
            # Native reply draft preserves the conversation. Never retry this sequence automatically.
            mid = self._mid(original["provider_id"])
            draft = self.write("messages/" + quote(mid, safe="") + "/createReply", {})
            path = "messages/" + quote(draft["id"], safe="")
            attachments = msg.pop("attachments")
            self.write(path, msg, "PATCH")
            for item in attachments:
                self.write(path + "/attachments", item)
            self.write(path + "/send", {})
        else:
            self.write("sendMail", {"message": msg, "saveToSentItems": True})
        return {"message": "Accepted by Microsoft for delivery. Check Sent for the saved copy."}


class ImapReader:
    def __init__(self, account: dict, *, password: str | None = None):
        self.account, self.settings = account, account["settings"]
        self.password = password
        self.validity = None
        self.ids: set[str] = set()
        self.folder = "inbox"
        self.address = self.settings.get("username", "")

    def capabilities(self):
        return {
            "write": True,
            "send": bool(self.settings.get("smtp_host") and "@" in self.address),
            "attachments": True,
            "max_attachment_bytes": MAX_ATTACHMENTS,
            "reason": "" if self.settings.get("smtp_host") else "Reconnect with SMTP settings to send mail",
        }

    @staticmethod
    def mailbox(conn, folder):
        if folder == "inbox":
            return b'"INBOX"'
        status, rows = conn.list()
        if status != "OK":
            raise ProviderError("Could not discover mailbox folders")
        wanted = {"sent": b"\\Sent", "trash": b"\\Trash", "archive": b"\\Archive"}[folder]
        for row in rows or []:
            if not isinstance(row, bytes):
                continue
            match = re.match(rb'\(([^)]*)\)\s+(?:"[^"\r\n]*"|NIL)\s+(.+)$', row)
            if match and wanted.lower() in {f.lower() for f in match[1].split()}:
                name = match[2]
                if b"\r" in name or b"\n" in name or name.startswith(b"{"):
                    continue
                # LIST returns a quoted string or atom; quote atom explicitly before commands.
                return (
                    name
                    if name.startswith(b'"') and name.endswith(b'"')
                    else b'"' + name.replace(b"\\", b"\\\\").replace(b'"', b'\\"') + b'"'
                )
        raise ProviderError(
            "This server does not advertise a " + folder + " folder. Configure its special-use folders first"
        )

    def connect(self, *, write=False):
        s = self.settings
        conn = imaplib.IMAP4_SSL(
            s["host"], int(s["port"]), ssl_context=ssl.create_default_context(), timeout=25
        )
        try:
            conn.login(
                s["username"],
                self.password if self.password is not None else password_store(self.account["id"]),
            )
            status, _ = conn.select(
                "INBOX" if self.folder == "inbox" else self.mailbox(conn, self.folder), readonly=not write
            )
            if status != "OK":
                raise ProviderError("Could not read this account's inbox")
            value = conn.response("UIDVALIDITY")[1]
            if not value or not value[0] or not value[0].isdigit():
                raise ProviderError(
                    "IMAP did not return a valid mailbox identifier; safe organization is unavailable"
                )
            return conn, ("" if self.folder == "inbox" else fingerprint(self.folder) + "/") + value[
                0
            ].decode()
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
            **mime_details(message),
            "body_text": str(text)[:262144],
            "label_ids": ([] if b"\\Seen" in flags else ["UNREAD"])
            + (["STARRED"] if b"\\Flagged" in flags else []),
            "category": None,
        }

    @staticmethod
    def fetch_headers(conn, uids: list[bytes]) -> dict[bytes, tuple[bytes, bytes]]:
        """Headers, flags and receipt time of many UIDs in one round trip per 200 UIDs."""
        found = {}
        for start in range(0, len(uids), 200):
            status, data = conn.uid(
                "fetch",
                b",".join(uids[start : start + 200]),
                "(UID FLAGS INTERNALDATE BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])",
            )
            if status != "OK":
                raise ProviderError("IMAP message list read failed")
            for index, entry in enumerate(data or []):
                if not isinstance(entry, tuple) or len(entry) < 2:
                    continue
                # Attributes after the header literal arrive as the following bytes element.
                trailing = data[index + 1] if index + 1 < len(data) else b""
                meta = entry[0] + (trailing if isinstance(trailing, bytes) else b"")
                match = re.search(rb"\bUID (\d+)", meta)
                if match:
                    found[match[1]] = (meta, entry[1])
        return found

    @staticmethod
    def fetch(conn, uid: bytes, headers_only: bool):
        field = "BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)]" if headers_only else "BODY.PEEK[]<0.28311553>"
        status, data = conn.uid("fetch", uid, f"(UID FLAGS INTERNALDATE {field})")
        if status != "OK":
            raise ProviderError("IMAP message read failed. Refresh")
        result = next((entry for entry in data if isinstance(entry, tuple)), None)
        if result and len(result[1]) > 28 * 1024 * 1024:
            raise ProviderError("Message exceeds the 28 MB preview/download limit")
        return result

    def list(self, limit: int, folder="inbox") -> tuple[list[dict], bool]:
        if folder not in FOLDERS:
            raise ProviderError("Unsupported folder")
        self.folder = folder
        conn, validity = self.connect()
        try:
            status, response = conn.uid("search", None, "UNDELETED")
            if status != "OK":
                raise ProviderError("IMAP message list read failed")
            ids = (response[0] or b"").split()
            chosen = list(reversed(ids[-limit:]))
            headers = self.fetch_headers(conn, chosen)
            result = [
                {
                    **self.parse(headers[uid][1], uid.decode(), headers[uid][0]),
                    "provider_id": f"{validity}:{uid.decode()}",
                }
                for uid in chosen
                if uid in headers
            ]
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

    def details(self, provider_ids: list[str]) -> dict[str, dict]:
        """Full messages over one connection instead of one login per message."""
        wanted = []
        for provider_id in provider_ids:
            validity, uid = provider_id.split(":", 1)
            if validity == self.validity and uid in self.ids and uid.isdigit():
                wanted.append((provider_id, uid))
        if not wanted:
            return {}
        conn, current = self.connect()
        try:
            if current != self.validity:
                raise ProviderError("The mailbox was rebuilt. Refresh and try again")
            found = {}
            for provider_id, uid in wanted:
                item = self.fetch(conn, uid.encode(), False)
                if item:
                    found[provider_id] = self.parse(item[1], uid, item[0])
            return found
        finally:
            try:
                conn.logout()
            except (OSError, imaplib.IMAP4.error):
                pass  # Retrieval has finished; a disconnected logout must not discard its result.

    def _uid(self, provider_id):
        validity, uid = provider_id.split(":", 1)
        if validity != self.validity or uid not in self.ids or not uid.isdigit():
            raise ProviderError("Message identity changed. Refresh")
        return validity, uid

    def mutate(self, provider_id, action):
        validity, uid = self._uid(provider_id)
        conn, current = self.connect(write=True)
        try:
            if current != validity:
                raise ProviderError("The mailbox was rebuilt. Refresh before changing messages")
            if action in ("trash", "archive", "restore"):
                destination = self.mailbox(
                    conn, {"trash": "trash", "archive": "archive", "restore": "inbox"}[action]
                )
                # RFC 6851: MOVE is scoped to the chosen UID. Never use global EXPUNGE,
                # which could permanently remove unrelated messages marked deleted by another client.
                capabilities = {
                    c.upper() if isinstance(c, bytes) else c.upper().encode() for c in conn.capabilities
                }
                if b"MOVE" not in capabilities:
                    raise ProviderError("This IMAP server lacks safe UID MOVE. No messages were deleted")
                status, _ = conn.uid("MOVE", uid, destination)
            else:
                flags = {
                    "read": ("+FLAGS.SILENT", r"(\Seen)"),
                    "unread": ("-FLAGS.SILENT", r"(\Seen)"),
                    "star": ("+FLAGS.SILENT", r"(\Flagged)"),
                    "unstar": ("-FLAGS.SILENT", r"(\Flagged)"),
                }
                if action not in flags:
                    raise ProviderError("Unsupported mail action")
                verb, flag = flags[action]
                status, _ = conn.uid("STORE", uid, verb, flag)
            if status != "OK":
                raise ProviderError("IMAP rejected this action. Refresh before retrying")
        finally:
            try:
                conn.logout()
            except (OSError, imaplib.IMAP4.error):
                pass

    def attachment(self, provider_id, attachment_id):
        validity, uid = self._uid(provider_id)
        conn, current = self.connect()
        try:
            if current != validity:
                raise ProviderError("The mailbox was rebuilt. Refresh")
            item = self.fetch(conn, uid.encode(), False)
            if not item:
                raise ProviderError("Message is no longer available")
            message = BytesParser(policy=policy.default).parsebytes(item[1])
            meta = next((a for a in mime_details(message)["attachments"] if a["id"] == attachment_id), None)
            if not meta or meta["size"] > MAX_ATTACHMENTS:
                raise ProviderError("Attachment is unavailable or exceeds 20 MB")
            part = list(message.walk())[int(attachment_id)]
            return meta, part.get_payload(decode=True) or b""
        finally:
            try:
                conn.logout()
            except (OSError, imaplib.IMAP4.error):
                pass

    def send(self, data, original=None):
        s = self.settings
        if not self.capabilities()["send"]:
            raise ProviderError(self.capabilities()["reason"])
        msg = mime_message(data, self.address, original)
        recipients = [a for key in ("to", "cc", "bcc") for a in addresses(data[key])]
        password = password_store(self.account["id"])
        context = ssl.create_default_context()
        if s["smtp_security"] == "ssl":
            smtp = smtplib.SMTP_SSL(s["smtp_host"], s["smtp_port"], timeout=25, context=context)
        else:
            smtp = smtplib.SMTP(s["smtp_host"], s["smtp_port"], timeout=25)
        try:
            if s["smtp_security"] == "starttls":
                smtp.ehlo()
                smtp.starttls(context=context)
                smtp.ehlo()
            smtp.login(s["username"], password)
            refused = smtp.sendmail(self.address, recipients, msg.as_bytes())
        finally:
            # SMTP acceptance is not undone by a failed QUIT.
            try:
                smtp.quit()
            except (OSError, smtplib.SMTPException):
                pass
        warning = (
            " Some recipients were rejected; check the original mailbox before resending." if refused else ""
        )
        try:
            conn, _ = self.connect()
            try:
                sent = self.mailbox(conn, "sent")
                status, _ = conn.append(
                    sent, r"(\Seen)", imaplib.Time2Internaldate(time.time()), msg.as_bytes()
                )
                if status != "OK":
                    raise ProviderError("Sent copy rejected")
            finally:
                conn.logout()
        except Exception:  # noqa: BLE001 - message already accepted by SMTP: never invite a resend
            warning += " Sent copy could not be saved. Do not resend just to create a copy."
        return {
            "message": "Accepted by SMTP for delivery." + warning,
            "status": "partial" if refused else "complete",
            "accepted": [a for a in recipients if a not in refused],
            "refused": list(refused),
        }


def create_reader(account: dict):
    if account["provider"] == "gmail":
        return GmailReader(account["settings"])
    if account["provider"] == "microsoft":
        return MicrosoftReader(account["settings"])
    if account["provider"] == "imap":
        return ImapReader(account)
    raise ProviderError("Unknown email provider")
