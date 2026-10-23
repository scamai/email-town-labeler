"""The single text rendering every model sees. Changing it changes model inputs: bump model versions."""

from __future__ import annotations

from .types import EmailRecord


def email_text(rec: EmailRecord, max_body_chars: int = 2000) -> str:
    h = rec.get("headers", {})
    body = " ".join((rec.get("body_text") or "").split())[:max_body_chars]
    lines = [
        f"From: {h.get('from', '')}",
        f"Subject: {h.get('subject', '')}",
        f"Mailing list: {'yes' if h.get('list-unsubscribe') or h.get('list-id') else 'no'}",
        f"Calendar invite: {'yes' if rec.get('has_calendar') else 'no'}",
        f"Body: {body}",
    ]
    return "\n".join(lines)
