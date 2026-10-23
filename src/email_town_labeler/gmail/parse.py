"""Turn a Gmail API `users.messages.get(format=full)` resource into an `EmailRecord`.

Pure functions, no Google imports, so it can be tested without credentials.
"""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Iterable
from html.parser import HTMLParser
from typing import ClassVar

from ..types import EmailRecord

# Headers worth keeping for classification and for reply/thread signals.
KEEP_HEADERS = {
    "from",
    "to",
    "cc",
    "bcc",
    "reply-to",
    "sender",
    "delivered-to",
    "return-path",
    "subject",
    "date",
    "message-id",
    "in-reply-to",
    "references",
    "list-id",
    "list-unsubscribe",
    "list-unsubscribe-post",
    "precedence",
    "auto-submitted",
    "x-auto-response-suppress",
    "feedback-id",
    "x-mailer",
    "content-type",
}
MAX_BODY_CHARS = 100_000
_SKIP_TAGS = ("script", "style", "head")


def _b64url_decode(data: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    except (binascii.Error, ValueError):  # malformed body data: treat as empty
        return b""


def _charset(part: dict) -> str:
    for h in part.get("headers", []) or []:
        if h.get("name", "").lower() == "content-type":
            m = re.search(r'charset\s*=\s*"?([\w.:-]+)', h.get("value", ""), re.IGNORECASE)
            if m:
                return m.group(1)
    return "utf-8"


def _decode_body(part: dict) -> str:
    data = (part.get("body") or {}).get("data")
    if not data:
        return ""
    raw = _b64url_decode(data)
    try:
        return raw.decode(_charset(part), errors="replace")
    except LookupError:  # unknown charset name
        return raw.decode("utf-8", errors="replace")


class _HTMLText(HTMLParser):
    BLOCK: ClassVar[set[str]] = {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "table", "blockquote"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.out.append(data)


def html_to_text(html: str) -> str:
    p = _HTMLText()
    p.feed(html)
    p.close()
    text = re.sub(r"[ \t\xa0]+", " ", "".join(p.out))
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def _walk(part: dict, acc: dict) -> None:
    mime = (part.get("mimeType") or "").lower()
    filename = part.get("filename") or ""
    if mime == "text/calendar" or filename.lower().endswith(".ics"):
        acc["has_calendar"] = True
    if filename:
        size = (part.get("body") or {}).get("size", 0)
        acc["attachments"].append({"filename": filename, "mime": mime, "size": size})
    elif mime == "text/plain":
        acc["plain"].append(_decode_body(part))
    elif mime == "text/html":
        acc["html"].append(_decode_body(part))
    for child in part.get("parts") or []:
        _walk(child, acc)


def parse_message(msg: dict) -> EmailRecord:
    """Flatten a format=full message. Plain text wins over HTML; body capped at MAX_BODY_CHARS."""
    payload = msg.get("payload") or {}
    headers: dict[str, str] = {}
    for h in payload.get("headers") or []:
        name = (h.get("name") or "").lower()
        if name in KEEP_HEADERS:
            value = h.get("value", "")
            headers[name] = headers[name] + "\n" + value if name in headers else value

    acc: dict = {"plain": [], "html": [], "attachments": [], "has_calendar": False}
    _walk(payload, acc)
    plain = "\n".join(t for t in acc["plain"] if t.strip())
    body = plain if plain.strip() else html_to_text("\n".join(acc["html"]))
    source = "plain" if plain.strip() else ("html" if any(t.strip() for t in acc["html"]) else "none")

    return EmailRecord(
        id=msg["id"],
        thread_id=msg.get("threadId") or "",
        label_ids=list(msg.get("labelIds") or []),
        internal_date=int(msg.get("internalDate") or 0),
        history_id=str(msg.get("historyId") or ""),
        size_estimate=int(msg.get("sizeEstimate") or 0),
        snippet=msg.get("snippet", ""),
        headers=headers,
        body_text=body[:MAX_BODY_CHARS],
        body_source=source,
        has_calendar=acc["has_calendar"],
        attachments=acc["attachments"],
    )


def parse_metadata(msg: dict, keep: Iterable[str] | None = None) -> EmailRecord:
    """Flatten a format=metadata message: ids, labels, date and the requested headers (lower-cased names;
    repeated headers joined with newlines). No snippet, no body."""
    want = {k.lower() for k in keep} if keep is not None else None
    headers: dict[str, str] = {}
    for h in (msg.get("payload") or {}).get("headers") or []:
        name = (h.get("name") or "").lower()
        if name and (want is None or name in want):
            value = h.get("value", "")
            headers[name] = headers[name] + "\n" + value if name in headers else value
    return EmailRecord(
        id=msg["id"],
        thread_id=msg.get("threadId") or "",
        label_ids=list(msg.get("labelIds") or []),
        internal_date=int(msg.get("internalDate") or 0),
        history_id=str(msg.get("historyId") or ""),
        headers=headers,
    )
