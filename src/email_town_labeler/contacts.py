"""Address helpers for the contacts table: who you have written to. Pure, no I/O.

Only lower-cased bare addresses come out of here: no display names, nothing else from the mail.
Never log what these return.
"""

from __future__ import annotations

from collections.abc import Iterable
from email.utils import getaddresses

from .types import EmailRecord

RECIPIENT_HEADERS = ("to", "cc", "bcc")


def addresses(values: Iterable[str | None]) -> list[str]:
    """Bare, lower-cased addresses from header values (several headers may be joined with newlines)."""
    fields = [line for v in values if v for line in str(v).split("\n") if line.strip()]
    out: list[str] = []
    for _, addr in getaddresses(fields):
        a = addr.strip().lower()
        if "@" in a and not any(ch.isspace() for ch in a) and a not in out:
            out.append(a)
    return out


def sender_address(rec: EmailRecord) -> str | None:
    """The From address, lower-cased, or None when there is none."""
    found = addresses([(rec.get("headers") or {}).get("from")])
    return found[0] if found else None


def recipient_addresses(rec: EmailRecord) -> list[str]:
    """To/Cc/Bcc addresses of a (sent) message, lower-cased."""
    h = rec.get("headers") or {}
    return addresses(h.get(k) for k in RECIPIENT_HEADERS)
