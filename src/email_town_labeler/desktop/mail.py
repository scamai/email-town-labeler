"""Validated MIME composition and private durable drafts / operation receipts.

Outgoing mail is never retried automatically. A receipt reserved before network I/O
survives a lost HTTP response or process restart, so a repeated request cannot send twice.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import sqlite3
import threading
import time
import uuid
from email import policy
from email.message import EmailMessage
from email.utils import formatdate, getaddresses, make_msgid
from pathlib import Path

MAX_ATTACHMENTS = 20 * 1024 * 1024
MAX_BODY_TEXT = 500_000
FOLDERS = ("inbox", "sent", "archive", "trash")
MAIL_ACTIONS = ("trash", "archive", "restore", "read", "unread", "star", "unstar")


def header(value, label, limit=2000):
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError(f"Invalid {label}")
    return value.strip()


def addresses(value, label="recipient") -> list[str]:
    value = header(value, label, 12000)
    if not value:
        return []
    result = []
    for _, addr in getaddresses([value]):
        if not re.fullmatch(r"[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+", addr) or not addr.isascii():
            raise ValueError(f"Enter valid {label} email addresses separated by commas")
        if addr.casefold() not in {a.casefold() for a in result}:
            result.append(addr)
    if len(result) > 100:
        raise ValueError("Use at most 100 recipients")
    return result


def recipient_headers(headers):
    """Structured RFC addresses: never treat an address in a display name as a recipient."""
    result = {}
    for key in ("from", "to", "cc", "reply-to"):
        parsed = getaddresses([str(headers.get(key, ""))])
        found = []
        for _, addr in parsed:
            try:
                valid = addresses(addr)
            except ValueError:
                continue
            if len(valid) == 1 and valid[0].casefold() not in {a.casefold() for a in found}:
                found.append(valid[0])
        result[key] = found
    return result


def filename(value):
    value = header(value, "attachment filename", 255)
    value = value.replace("\\", "/").rsplit("/", 1)[-1]
    if value in ("", ".", ".."):
        raise ValueError("Attachment needs a filename")
    return value


def compose_data(data: dict, *, sending=True, max_bytes=MAX_ATTACHMENTS) -> dict:
    result = {"account_id": header(data.get("account_id", ""), "account", 64)}
    for key in ("to", "cc", "bcc"):
        result[key] = ", ".join(addresses(data.get(key, ""), key))
    if sending and not any(result[k] for k in ("to", "cc", "bcc")):
        raise ValueError("Add at least one recipient")
    result["subject"] = header(data.get("subject", ""), "subject")
    body = data.get("body", "")
    if not isinstance(body, str) or len(body.encode("utf-8")) > MAX_BODY_TEXT or "\0" in body:
        raise ValueError("Message body is invalid or exceeds 500 KB")
    result["body"] = body
    reply = data.get("reply_to_id", "")
    if reply and (not isinstance(reply, str) or not re.fullmatch(r"[a-f0-9]{64}", reply)):
        raise ValueError("Invalid reply message")
    result["reply_to_id"] = reply
    attachments = data.get("attachments", [])
    if not isinstance(attachments, list) or len(attachments) > 20:
        raise ValueError("Attach at most 20 files")
    result["attachments"] = []
    total = 0
    for item in attachments:
        if not isinstance(item, dict):
            raise ValueError("Invalid attachment")  # noqa: TRY004 - consistent validated HTTP input error
        name = filename(item.get("name", ""))
        kind = header(item.get("type") or "application/octet-stream", "attachment type", 150)
        if not re.fullmatch(r"[\w.+-]+/[\w.+-]+", kind):
            kind = "application/octet-stream"
        encoded = item.get("data")
        if not isinstance(encoded, str) or len(encoded) > (max_bytes * 4 // 3 + 4):
            raise ValueError("Attachments exceed this account's size limit")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as e:
            raise ValueError("Invalid attachment data") from e
        total += len(raw)
        if total > max_bytes:
            raise ValueError("Attachments exceed this account's size limit")
        result["attachments"].append({"name": name, "type": kind, "data": encoded})
    return result


def mime_message(data, sender, original=None):
    msg = EmailMessage(policy=policy.SMTP)
    sender_addresses = addresses(sender, "sender")
    if len(sender_addresses) != 1:
        raise ValueError("This account has no verified sender address")
    msg["From"] = sender_addresses[0]
    for key in ("to", "cc"):
        if data[key]:
            msg[key.title()] = data[key]
    msg["Subject"] = data["subject"]
    msg["Date"] = formatdate(localtime=False, usegmt=True)
    msg["Message-ID"] = make_msgid(domain=sender_addresses[0].split("@", 1)[1])
    if original:
        headers = original.get("headers", {})
        mid = headers.get("message-id", "")
        if re.fullmatch(r"<[^<>\s]+>", mid):
            msg["In-Reply-To"] = mid
            refs = re.findall(r"<[^<>\s]+>", headers.get("references", ""))[-20:]
            msg["References"] = " ".join([*refs, mid])
    msg.set_content(data["body"])
    for item in data["attachments"]:
        maintype, subtype = item["type"].split("/", 1)
        msg.add_attachment(
            base64.b64decode(item["data"]), maintype=maintype, subtype=subtype, filename=item["name"]
        )
    return msg


def mime_details(message):
    attachments = []
    for index, part in enumerate(message.walk()):
        content_id = str(part.get("Content-ID", "")).strip().strip("<>")
        if part.is_multipart() or not (
            part.get_filename()
            or part.get_content_disposition() == "attachment"
            or (content_id and part.get_content_maintype() == "image")
        ):
            continue
        raw = part.get_payload(decode=True) or b""
        try:
            name = filename(part.get_filename() or "attachment")
        except ValueError:
            name = "attachment"
        attachments.append(
            {
                "id": str(index),
                "name": name,
                "size": len(raw),
                "type": part.get_content_type(),
                "content_id": content_id,
            }
        )
    html = ""
    part = message.get_body(preferencelist=("html",))
    if part is not None:
        try:
            html = str(part.get_content())[:262144]
        except (ValueError, LookupError, KeyError):
            pass
    return {
        "body_html": html,
        "headers": {
            k: str(message.get(k, ""))
            for k in ("from", "to", "cc", "reply-to", "message-id", "references", "subject")
        },
        "attachments": attachments,
    }


class MailStore:
    """New private store, separate from local category/workflow state."""

    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        root.chmod(0o700)
        self.path = root / "mail.sqlite"
        self.lock = threading.RLock()
        with self.db() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS drafts (id TEXT PRIMARY KEY, data TEXT NOT NULL, updated INTEGER NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS operations (id TEXT PRIMARY KEY, digest TEXT NOT NULL, result TEXT NOT NULL)"
            )
            # A process died after reservation: acceptance by the remote server is unknown.
            rows = db.execute("SELECT id,result FROM operations").fetchall()
            for key, raw in rows:
                result = json.loads(raw)
                if result["status"] == "pending":
                    result.update(
                        status="unknown",
                        message="Interrupted. Check Sent or the original mailbox before retrying.",
                    )
                    db.execute("UPDATE operations SET result=? WHERE id=?", (json.dumps(result), key))
        self.path.chmod(0o600)

    def db(self):
        from contextlib import contextmanager

        @contextmanager
        def connection():
            db = sqlite3.connect(self.path, timeout=10)
            try:
                with db:
                    yield db
            finally:
                db.close()

        return connection()

    @staticmethod
    def key(value):
        if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{16,80}", value):
            raise ValueError("Invalid operation or draft ID")
        return value

    def save_draft(self, data):
        key = self.key(data["id"]) if data.get("id") else uuid.uuid4().hex
        clean = compose_data(data, sending=False)
        with self.lock, self.db() as db:
            if (
                db.execute("SELECT COUNT(*) FROM drafts").fetchone()[0] >= 100
                and not db.execute("SELECT 1 FROM drafts WHERE id=?", (key,)).fetchone()
            ):
                raise ValueError("Keep at most 100 local drafts")
            db.execute(
                "INSERT OR REPLACE INTO drafts VALUES (?,?,?)",
                (key, json.dumps(clean), int(time.time() * 1000)),
            )
        return {"id": key}

    def drafts(self):
        with self.lock, self.db() as db:
            return {
                "drafts": [
                    {
                        "id": key,
                        "subject": d.get("subject", ""),
                        "account_id": d["account_id"],
                        "updated": updated,
                    }
                    for key, raw, updated in db.execute("SELECT * FROM drafts ORDER BY updated DESC")
                    for d in [json.loads(raw)]
                ]
            }

    def draft(self, key):
        with self.lock, self.db() as db:
            row = db.execute("SELECT data FROM drafts WHERE id=?", (self.key(key),)).fetchone()
        if not row:
            raise ValueError("Draft not found")
        return {"id": key, **json.loads(row[0])}

    def delete_draft(self, key):
        with self.lock, self.db() as db:
            db.execute("DELETE FROM drafts WHERE id=?", (self.key(key),))
        return {"ok": True}

    def delete_matching_draft(self, key, sent):
        """One transaction prevents an edit during send from being discarded."""
        with self.lock, self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM drafts WHERE id=?", (self.key(key),)).fetchone()
            if row and json.loads(row[0]) == sent:
                db.execute("DELETE FROM drafts WHERE id=?", (key,))

    def reserve(self, key, payload):
        self.key(key)
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        with self.lock, self.db() as db:
            row = db.execute("SELECT digest FROM operations WHERE id=?", (key,)).fetchone()
            if row:
                if row[0] != digest:
                    raise ValueError("This request ID was already used for different content")
                return False
            result = {"status": "pending", "succeeded": [], "failed": [], "message": "Working…"}
            db.execute("INSERT INTO operations VALUES (?,?,?)", (key, digest, json.dumps(result)))
        return True

    def receipt(self, key):
        with self.lock, self.db() as db:
            row = db.execute("SELECT result FROM operations WHERE id=?", (self.key(key),)).fetchone()
        if not row:
            raise ValueError("Operation not found")
        return json.loads(row[0])

    def finish(self, key, result):
        with self.lock, self.db() as db:
            db.execute("UPDATE operations SET result=? WHERE id=?", (json.dumps(result), key))
