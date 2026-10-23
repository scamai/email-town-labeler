"""In-memory GmailClient (same interface as email_town_labeler.gmail.client.GmailClient), a LabelMap twin and a tiny
deterministic Classifier. Made-up mail only."""

from __future__ import annotations

import copy
import itertools
import re
from collections.abc import Iterable
from typing import Any

from email_town_labeler.models.base import ModelManifest
from email_town_labeler.taxonomy import CLASSES, UNKNOWN
from email_town_labeler.types import EmailRecord, HistoryEvent, Prediction

try:  # use the real exception class when the Gmail layer exists
    from email_town_labeler.gmail.client import HistoryExpired
except ImportError:  # pragma: no cover - only before gmail/client.py exists

    class HistoryExpired(Exception):  # type: ignore[no-redef]
        """Gmail 404 on startHistoryId."""


class TransientError(Exception):
    """Stands in for a 5xx/network error."""


SYSTEM_LABELS = [
    "INBOX",
    "SENT",
    "DRAFT",
    "SPAM",
    "TRASH",
    "UNREAD",
    "STARRED",
    "IMPORTANT",
    "CATEGORY_PERSONAL",
    "CATEGORY_PROMOTIONS",
    "CATEGORY_UPDATES",
    "CATEGORY_SOCIAL",
    "CATEGORY_FORUMS",
]
DAY_MS = 86_400_000


class FakeGmailClient:
    def __init__(self, *, email: str = "me@example.test", now_ms: int = 1_760_000_000_000):
        self.email = email
        self.now_ms = now_ms
        self.messages: dict[str, EmailRecord] = {}
        self.labels: dict[str, dict] = {i: {"id": i, "name": i, "type": "system"} for i in SYSTEM_LABELS}
        self.history_id = 1000
        self.log: list[tuple[int, HistoryEvent]] = []
        self.expired_before: int | None = None
        self.failures: dict[str, list[BaseException]] = {}
        self.calls: list[tuple[str, tuple]] = []
        self.modifies: list[tuple[str, list[str], list[str]]] = []
        self._ids = itertools.count(1)
        self._label_ids = itertools.count(1)

    # -- test controls ------------------------------------------------------------------------
    def fail(self, method: str, exc: BaseException | None = None, times: int = 1) -> None:
        self.failures.setdefault(method, []).extend([exc or TransientError("503")] * times)

    def _call(self, method: str, *args: Any) -> None:
        self.calls.append((method, args))
        q = self.failures.get(method)
        if q:
            raise q.pop(0)

    def _bump(self) -> int:
        self.history_id += 1
        return self.history_id

    def add_message(
        self,
        *,
        cls: str | None = None,
        prob: float = 0.9,
        labels: Iterable[str] = ("INBOX", "UNREAD"),
        age_days: float = 0.0,
        msg_id: str | None = None,
        record_history: bool = True,
        **headers: str,
    ) -> str:
        """Deliver a made-up message. `cls`/`prob` steer FakeClassifier through x-test-* headers."""
        mid = msg_id or f"m{next(self._ids):04d}"
        h = {"from": "Sender <sender@example.test>", "subject": f"made-up message {mid}", **headers}
        if cls:
            h["x-test-class"], h["x-test-prob"] = cls, str(prob)
        hid = self._bump() if record_history else self.history_id
        self.messages[mid] = EmailRecord(
            id=mid,
            thread_id=f"t{mid}",
            label_ids=list(labels),
            internal_date=int(self.now_ms - age_days * DAY_MS),
            history_id=str(hid),
            size_estimate=1000,
            snippet="made-up snippet",
            headers=h,
            body_text="made-up body",
            body_source="plain",
            has_calendar=False,
            attachments=[],
        )
        if record_history:
            self.log.append((hid, HistoryEvent("added", mid, [], list(labels))))
        return mid

    def label_id(self, name: str) -> str | None:
        return next((lb["id"] for lb in self.labels.values() if lb["name"] == name), None)

    def user_add_label(self, mid: str, label_id: str) -> None:
        self._change(mid, [label_id], [])

    def user_remove_label(self, mid: str, label_id: str) -> None:
        self._change(mid, [], [label_id])

    def expire_history(self) -> None:
        self.expired_before = self.history_id + 1

    def _change(self, mid: str, add: Iterable[str], remove: Iterable[str]) -> None:
        m = self.messages[mid]
        labs = m["label_ids"]
        added = [lid for lid in add if lid not in labs]
        removed = [lid for lid in remove if lid in labs]
        for lid in added:
            if lid not in self.labels:
                raise KeyError(f"no label {lid}")
            labs.append(lid)
        for lid in removed:
            labs.remove(lid)
        if added:
            self.log.append((self._bump(), HistoryEvent("label_added", mid, added, list(labs))))
        if removed:
            self.log.append((self._bump(), HistoryEvent("label_removed", mid, removed, list(labs))))

    # -- GmailClient interface ----------------------------------------------------------------
    def profile(self) -> dict:
        self._call("profile")
        return {
            "emailAddress": self.email,
            "historyId": str(self.history_id),
            "messagesTotal": len(self.messages),
        }

    def list_message_ids(
        self,
        *,
        query: str | None = None,
        label_ids: list[str] | None = None,
        max_results: int = 100,
        include_spam_trash: bool = False,
    ) -> list[str]:
        self._call("list_message_ids", query, tuple(label_ids or ()), max_results)
        want = set(label_ids or [])
        hidden = set() if include_spam_trash or want & {"SPAM", "TRASH"} else {"SPAM", "TRASH"}
        min_ms, labels_q = None, set()
        for tok in (query or "").split():
            if mt := re.fullmatch(r"newer_than:(\d+)d", tok):
                min_ms = self.now_ms - int(mt.group(1)) * DAY_MS
            elif mt := re.fullmatch(r"after:(\d+)", tok):  # epoch seconds, as Gmail accepts
                min_ms = int(mt.group(1)) * 1000
            elif mt := re.fullmatch(r"in:(\w+)", tok):
                labels_q.add(mt.group(1).upper())
            else:
                raise ValueError(f"fake query does not support {tok!r}")
        msgs = [
            m
            for m in self.messages.values()
            if want | labels_q <= set(m["label_ids"])
            and not hidden & set(m["label_ids"])
            and (min_ms is None or m["internal_date"] >= min_ms)
        ]
        msgs.sort(key=lambda m: (m["internal_date"], m["id"]), reverse=True)
        return [m["id"] for m in msgs[:max_results]]

    def get_records(self, ids: list[str]) -> list[EmailRecord]:
        self._call("get_records", tuple(ids))
        return [copy.deepcopy(self.messages[i]) for i in ids if i in self.messages]

    def get_metadata(self, ids: list[str], headers: Iterable[str]) -> list[EmailRecord]:
        self._call("get_metadata", tuple(ids))
        want = {h.lower() for h in headers}
        return [
            EmailRecord(
                id=m["id"],
                thread_id=m["thread_id"],
                label_ids=list(m["label_ids"]),
                internal_date=m["internal_date"],
                history_id=m["history_id"],
                headers={k: v for k, v in m["headers"].items() if k.lower() in want},
            )
            for i in dict.fromkeys(ids)
            if (m := self.messages.get(i)) is not None
        ]

    def get_label_ids(self, id: str) -> list[str]:
        self._call("get_label_ids", id)
        return list(self.messages[id]["label_ids"])

    def list_labels(self) -> list[dict]:
        self._call("list_labels")
        return [dict(lb) for lb in self.labels.values()]

    def create_label(self, name: str, color: dict[str, str] | None = None) -> dict:
        self._call("create_label", name)
        if self.label_id(name):
            raise ValueError(f"label exists: {name}")
        lid = f"Label_{next(self._label_ids)}"
        self.labels[lid] = {"id": lid, "name": name, "type": "user"}
        if color:
            self.labels[lid]["color"] = dict(color)
        return dict(self.labels[lid])

    def set_label_color(self, id: str, color: dict[str, str]) -> dict:
        self._call("set_label_color", id)
        self.labels[id]["color"] = dict(color)
        return dict(self.labels[id])

    def modify(self, id: str, *, add: Iterable[str] = (), remove: Iterable[str] = ()) -> None:
        self._call("modify", id, tuple(add), tuple(remove))
        self.modifies.append((id, list(add), list(remove)))
        self._change(id, add, remove)

    def history(self, start_history_id: str) -> tuple[list[HistoryEvent], str]:
        self._call("history", start_history_id)
        start = int(start_history_id)
        if self.expired_before is not None and start < self.expired_before:
            raise HistoryExpired(start_history_id)
        return [copy.deepcopy(e) for h, e in self.log if h > start], str(self.history_id)


class FakeLabelMap:
    """Same contract as email_town_labeler.gmail.labels.LabelMap, backed by any client."""

    def __init__(self, client: Any, prefix: str, names: dict[str, str], colors: dict | None = None):
        self.client, self.prefix, self.names = client, prefix, names
        self.colors = dict(colors or {})
        self.color_syncs = 0

    def _by_name(self) -> dict[str, str]:
        return {lb["name"]: lb["id"] for lb in self.client.list_labels()}

    def ensure(self, cls: str) -> str:
        by = self._by_name()
        if self.prefix not in by:
            self.client.create_label(self.prefix)
        full = f"{self.prefix}/{self.names[cls]}"
        return by.get(full) or self.client.create_label(full, color=self.colors.get(cls))["id"]

    def sync_colors(self) -> int:
        self.color_syncs += 1
        return 0

    def class_of(self, label_id: str) -> str | None:
        rev = {f"{self.prefix}/{n}": c for c, n in self.names.items()}
        name = next((lb["name"] for lb in self.client.list_labels() if lb["id"] == label_id), None)
        return rev.get(name or "")

    def our_label_ids(self) -> set[str]:
        full = {f"{self.prefix}/{n}" for n in self.names.values()}
        return {lid for name, lid in self._by_name().items() if name in full}


class FakeClassifier:
    """Reads x-test-class / x-test-prob headers; anything else is `unknown` at 0.5."""

    def __init__(self, name: str = "fake"):
        self.manifest = ModelManifest(name=name, version="1", backend="fake")
        self.seen: list[str] = []
        self.fail_next = 0

    def predict(self, records: list[EmailRecord]) -> list[Prediction]:
        if self.fail_next:
            self.fail_next -= 1
            raise RuntimeError("model hiccup")
        out = []
        for r in records:
            self.seen.append(r["id"])
            h = r.get("headers", {})
            cls, p = h.get("x-test-class", UNKNOWN), float(h.get("x-test-prob", 0.5))
            rest = (1.0 - p) / (len(CLASSES) - 1)
            out.append(Prediction(cls, p, {c: (p if c == cls else rest) for c in CLASSES}))
        return out
