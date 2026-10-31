"""Account-scoped local workflow state; never persist headers, snippets or bodies."""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from ..taxonomy import LABEL_CLASSES


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
            raise ValueError("未知操作")
        if action == "category" and category not in LABEL_CLASSES:
            raise ValueError("未知分类")
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
                    raise ValueError("这些邮件已有更新的操作，无法撤销；请重新查看")
            for old in before:
                db.execute(
                    "UPDATE workflow SET done=?,category=?,revision=revision+1 WHERE id=?",
                    (old["done"], old["category"], old["id"]),
                )
