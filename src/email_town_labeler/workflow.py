"""Daily client workflows: read-only inbox queries and explicit label visibility repair.

No mail content, model loading or local state is needed. Inbox views are search snapshots,
not task-completion state. Only show_labels changes Gmail, and only our label definitions.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import Config, ConfigError
from .gmail.client import GmailClient, _status
from .gmail.labels import LabelMap
from .taxonomy import CONTENT_LABELS, LABEL_CLASSES, NEW_SENDER, UNKNOWN

VIEW_TITLES = {
    "action": "Action needed in inbox (all dates)",
    "today": "Action needed received on selected day",
    "backlog": "Action needed received before selected day",
    "new-sender": "New senders in inbox (all dates)",
    "unclassified": "Inbox without any configured class label (all dates)",
}


def expected_classes(cfg: Config) -> list[str]:
    return (
        CONTENT_LABELS
        + ([UNKNOWN] if cfg.unknown_action == "label" else [])
        + ([NEW_SENDER] if cfg.new_sender else [])
    )


def audit_labels(client: GmailClient, cfg: Config) -> dict:
    """Fetch visibility for expected labels. Missing/unknown is never a visibility pass."""
    names = cfg.display_names()
    expected = [cfg.label_prefix] + [f"{cfg.label_prefix}/{names[c]}" for c in expected_classes(cfg)]
    by_name = {lb["name"].casefold(): lb for lb in client.list_labels()}
    rows = []
    for name in expected:
        label = by_name.get(name.casefold())
        row = {"name": name, "state": "missing"}
        if label is not None:
            try:
                full = client.get_label(label["id"])
            except Exception as e:
                if _status(e) != 404:
                    raise
            else:
                if full.get("name", "").casefold() != name.casefold():
                    rows.append({"name": name, "state": "changed-during-check"})
                    continue
                sidebar = full.get("labelListVisibility")
                message = full.get("messageListVisibility")
                state = "visible" if (sidebar, message) == ("labelShow", "show") else "not-visible"
                if sidebar not in ("labelShow", "labelHide", "labelShowIfUnread") or message not in (
                    "show",
                    "hide",
                ):
                    state = "unknown"
                if full.get("type", "user") != "user":
                    state = "not-user-label"
                row.update(id=full["id"], sidebar=sidebar, message_list=message, state=state)
        rows.append(row)
    return {
        "ok": all(row["state"] == "visible" for row in rows),
        "scope": "Gmail web visibility; IMAP subscriptions and client settings require client checks",
        "labels": rows,
    }


def show_labels(client: GmailClient, cfg: Config, *, dry_run: bool) -> dict:
    """Explicit repair, never part of startup: honor deliberate hiding until this is requested."""
    before = audit_labels(client, cfg)
    changes = [r for r in before["labels"] if r["state"] != "visible"]
    if any(r["state"] == "not-user-label" for r in changes):
        raise ConfigError("a configured label is a system label; choose another prefix")
    if dry_run or not changes:
        return {**before, "dry_run": dry_run, "changed": 0, "planned": len(changes)}
    labels = LabelMap(client, cfg.label_prefix, cfg.display_names(), colors=cfg.colors())
    # ensure() creates missing children and their parent, resolving concurrent create races.
    for cls in expected_classes(cfg):
        labels.ensure(cls)
    # A parent can be absent while its children exist: Gmail permits that arrangement.
    labels.ensure_parent()
    current = audit_labels(client, cfg)
    for row in current["labels"]:
        if row["state"] != "visible":
            if row.get("id") is None or row["state"] == "not-user-label":
                raise ConfigError("labels changed during repair; run `labels check` and retry")
            client.show_label(row["id"])
    return {**audit_labels(client, cfg), "dry_run": False, "changed": len(changes), "planned": len(changes)}


def _label_term(name: str) -> str:
    # Gmail does not specify escaping for arbitrary quote/control characters in label searches.
    # Refuse rather than broaden a daily shortlist with an injected search operator.
    if any(ch in name for ch in ('"', "\\")) or any(ord(ch) < 32 or ord(ch) == 127 for ch in name):
        raise ConfigError(
            "triage searches require label names without quotes, backslashes or control characters"
        )
    return f'label:"{name}"'


def daily_plan(cfg: Config, *, day: str | None = None, timezone: str | None = None) -> dict:
    """Calendar-day queries, with epoch seconds to avoid Gmail's PST date interpretation.

    Without --timezone, naive midnight.timestamp uses the OS timezone including DST. With an
    IANA zone, both midnights are constructed independently (days can be 23 or 25 hours).
    """
    try:
        zone = ZoneInfo(timezone) if timezone else None
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise ConfigError(
            "unknown timezone; use an IANA name (install tzdata if the OS has no zone database)"
        ) from e
    try:
        if day is not None and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            raise ValueError("non-ISO date")
        selected = date.fromisoformat(day) if day else datetime.now(zone).date()
        tomorrow = selected + timedelta(days=1)
        start = int(datetime.combine(selected, time.min, tzinfo=zone).timestamp())
        end = int(datetime.combine(tomorrow, time.min, tzinfo=zone).timestamp())
    except (ValueError, OverflowError, OSError) as e:
        raise ConfigError("date must be a supported calendar date in YYYY-MM-DD format") from e
    names = {c: f"{cfg.label_prefix}/{n}" for c, n in cfg.display_names().items()}
    terms = {c: _label_term(names[c]) for c in LABEL_CLASSES}
    action = f"in:inbox {terms['action_needed']}"
    queries = {
        "action": action,
        "today": f"{action} after:{start} before:{end}",
        "backlog": f"{action} before:{start}",
        "new-sender": f"in:inbox {terms[NEW_SENDER]}",
        "unclassified": "in:inbox " + " ".join(f"-{terms[c]}" for c in LABEL_CLASSES),
    }
    return {
        "date": selected.isoformat(),
        "timezone": timezone or "system-local",
        "start_epoch": start,
        "end_epoch": end,
        "views": [{"key": k, "title": VIEW_TITLES[k], "query": q} for k, q in queries.items()],
        "notes": [
            "Search snapshots, not unfinished-task counts; views overlap.",
            "Today is a fixed calendar date; regenerate daily. Action and backlog retain older mail.",
            "Unclassified includes intentionally skipped mail and mail not yet processed.",
            "Gmail web conversations can contain additional messages; API counts are message-level.",
            "Copy queries into the matching Gmail account; Outlook/Apple Mail use their own search syntax.",
        ],
    }


def inspect_inbox(client: GmailClient, plan: dict, *, view: str | None, limit: int) -> dict:
    """Bounded live ID-only queries; fetch one extra to distinguish an exact count from a cap."""
    if not 1 <= limit <= 1000:
        raise ConfigError("triage --limit must be between 1 and 1000")
    if view is not None and view not in VIEW_TITLES:
        raise ConfigError("unknown triage view")
    rows = []
    for row in plan["views"]:
        if view is not None and row["key"] != view:
            continue
        ids = client.list_message_ids(query=row["query"], max_results=limit + 1)
        rows.append({**row, "count": min(len(ids), limit), "truncated": len(ids) > limit})
    return {
        **plan,
        "views": rows,
        "limit": limit,
        "source": "live Gmail search (not a transactional snapshot)",
    }
