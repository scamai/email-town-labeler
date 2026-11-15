"""Account-scoped local workflow state; never persist headers, snippets or bodies."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from ..taxonomy import LABEL_CLASSES

# Categories that can collapse into one Inbox row ("bundle"). Mail that may need you (action needed, new
# senders, unclassified) always stays as its own rows. All five bundle by default, as in Inbox by Gmail.
BUNDLEABLE = ("newsletters", "promotional", "updates", "orders", "travel")
# Pane widths in CSS pixels (desktop/web/panes.js enforces the same minimums against the window).
PANE_LIMITS = {"sidebar": (180, 360), "list": (320, 4000)}


def message_key(account_id: str, provider_id: str) -> str:
    return hashlib.sha256((account_id + "\0" + provider_id).encode()).hexdigest()


class LocalState:
    def __init__(self, path: Path):
        self.path, self.lock = path, threading.RLock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS workflow (id TEXT PRIMARY KEY, done INTEGER NOT NULL DEFAULT 0, category TEXT, revision INTEGER NOT NULL DEFAULT 0)"
            )
            db.execute("CREATE TABLE IF NOT EXISTS prefs (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        path.chmod(0o600)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def read(self, keys: list[str]) -> dict[str, dict]:
        if not keys:
            return {}
        with self.lock, self._connect() as db:
            result = {}
            for start in range(0, len(keys), 400):
                chunk = keys[start : start + 400]
                for row in db.execute(
                    "SELECT * FROM workflow WHERE id IN (" + ",".join("?" for _ in chunk) + ")", chunk
                ):
                    result[row["id"]] = dict(row)
            return result

    def edit(self, keys: list[str], action: str, category: str | None = None) -> list[dict]:
        if action not in ("done", "reopen", "category"):
            raise ValueError("Unknown action")
        if action == "category" and category not in LABEL_CLASSES:
            raise ValueError("Unknown category")
        with self.lock, self._connect() as db:
            before = []
            for key in dict.fromkeys(keys):
                db.execute("INSERT OR IGNORE INTO workflow(id) VALUES (?)", (key,))
                old = dict(db.execute("SELECT * FROM workflow WHERE id=?", (key,)).fetchone())
                before.append(old)
                if action == "category":
                    db.execute(
                        "UPDATE workflow SET category=?,revision=revision+1 WHERE id=?", (category, key)
                    )
                else:
                    db.execute(
                        "UPDATE workflow SET done=?,revision=revision+1 WHERE id=?",
                        (int(action == "done"), key),
                    )
            return before

    def undo(self, before: list[dict]) -> None:
        with self.lock, self._connect() as db:
            # Transaction-wide optimistic check: no partial restore if another browser edited an item.
            db.execute("BEGIN IMMEDIATE")
            for old in before:
                row = db.execute("SELECT revision FROM workflow WHERE id=?", (old["id"],)).fetchone()
                if row is None or row["revision"] != old["revision"] + 1:
                    raise ValueError(
                        "These messages have newer changes and cannot be undone. Review them again."
                    )
            for old in before:
                db.execute(
                    "UPDATE workflow SET done=?,category=?,revision=revision+1 WHERE id=?",
                    (old["done"], old["category"], old["id"]),
                )

    # ─── Preferences: per-device choices that are not about any one message ───

    def _saved(self, key: str):
        with self.lock, self._connect() as db:
            row = db.execute("SELECT value FROM prefs WHERE key=?", (key,)).fetchone()
        try:
            return json.loads(row["value"]) if row else None
        except ValueError:  # a damaged value falls back to the default, never stops the app
            return None

    def _save(self, key: str, value) -> None:
        with self.lock, self._connect() as db:
            db.execute(
                "INSERT INTO prefs(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)),
            )

    def prefs(self) -> dict:
        bundled, panes = self._saved("bundled"), self._saved("panes")
        return {
            "bundled": [c for c in BUNDLEABLE if c in bundled]
            if isinstance(bundled, list)
            else list(BUNDLEABLE),
            # Only widths inside the limits; anything else means "the default" to the page.
            "panes": {
                name: panes[name]
                for name, (low, high) in PANE_LIMITS.items()
                if isinstance(panes, dict) and type(panes.get(name)) is int and low <= panes[name] <= high
            },
        }

    def set_panes(self, widths) -> dict:
        """Remember the folder pane and message list widths (the page's origin changes every launch)."""
        if not isinstance(widths, dict):
            raise ValueError("Pane widths must be an object")  # noqa: TRY004 - the page gets it as a 400
        clean = {}
        for name, (low, high) in PANE_LIMITS.items():
            value = widths.get(name)
            if value is None:
                continue
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"The {name} width must be {low}–{high} pixels")
            clean[name] = value
        with self.lock:
            merged = self.prefs()["panes"] | clean
            self._save("panes", merged)
        return {"panes": merged}

    def set_bundled(self, category: str, bundled: bool) -> dict:
        """Bundle a category in the Inbox or show its messages one by one again."""
        if category not in BUNDLEABLE:
            raise ValueError("This category always stays in the Inbox")
        with self.lock:
            current = self.prefs()["bundled"]
            chosen = [
                c for c in BUNDLEABLE if (c == category and bundled) or (c != category and c in current)
            ]
            self._save("bundled", chosen)
        return {"bundled": chosen}
