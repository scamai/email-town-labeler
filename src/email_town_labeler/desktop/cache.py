"""On-disk list cache, so a restart shows the last loaded mail at once and then revalidates it.

Like a desktop mail client's local store, but only what the list shows: subject, sender, date,
read/star state, category and IDs for the loaded window of each folder, plus a reader's resumable
state (Gmail: header metadata and the history cursor). Never bodies, snippets or attachments.
The file is mode 0600 in the private workbench directory; deleting it only costs one cold load.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

VERSION = 1
# Exactly the list row fields the UI needs; anything else a caller passes is dropped.
ROW_FIELDS = (
    "id",
    "account_id",
    "provider_id",
    "subject",
    "sender",
    "date",
    "category",
    "unread",
    "starred",
    "source",
    "confidence",
)


class ListCache:
    def __init__(self, path: Path):
        self.path, self.lock = path, threading.RLock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version != VERSION:
                # An older layout is only a cache: start over instead of migrating it.
                db.execute("DROP TABLE IF EXISTS rows")
                db.execute("DROP TABLE IF EXISTS readers")
                db.execute(f"PRAGMA user_version = {VERSION}")
            db.execute(
                "CREATE TABLE IF NOT EXISTS rows (account_id TEXT NOT NULL, folder TEXT NOT NULL,"
                " saved_at INTEGER NOT NULL, data TEXT NOT NULL, PRIMARY KEY (account_id, folder))"
            )
            db.execute("CREATE TABLE IF NOT EXISTS readers (account_id TEXT PRIMARY KEY, data TEXT NOT NULL)")
        path.chmod(0o600)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def save_rows(self, account_id: str, folder: str, rows: list[dict]) -> None:
        clean = [{k: row[k] for k in ROW_FIELDS if k in row} for row in rows]
        with self.lock, self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO rows VALUES (?, ?, ?, ?)",
                (account_id, folder, int(time.time() * 1000), json.dumps(clean, ensure_ascii=False)),
            )

    def load_rows(self, account_ids: set[str]) -> dict[tuple[str, str], tuple[int, list[dict]]]:
        """(account, folder) -> (saved_at ms, rows). Rows of accounts no longer connected are deleted."""
        result = {}
        with self.lock, self._connect() as db:
            for (account_id,) in db.execute("SELECT account_id FROM readers").fetchall():
                if account_id not in account_ids:
                    db.execute("DELETE FROM readers WHERE account_id=?", (account_id,))
            for account_id, folder, saved_at, data in db.execute("SELECT * FROM rows").fetchall():
                if account_id not in account_ids:
                    db.execute("DELETE FROM rows WHERE account_id=?", (account_id,))
                    continue
                try:
                    rows = json.loads(data)
                except ValueError:
                    continue
                result[(account_id, folder)] = (
                    saved_at,
                    [r for r in rows if isinstance(r, dict) and "id" in r],
                )
        return result

    def save_reader(self, account_id: str, state: dict) -> None:
        with self.lock, self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO readers VALUES (?, ?)",
                (account_id, json.dumps(state, ensure_ascii=False)),
            )

    def load_reader(self, account_id: str) -> dict | None:
        with self.lock, self._connect() as db:
            row = db.execute("SELECT data FROM readers WHERE account_id=?", (account_id,)).fetchone()
        try:
            return json.loads(row[0]) if row else None
        except ValueError:
            return None
