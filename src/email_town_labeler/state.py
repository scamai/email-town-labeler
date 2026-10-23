"""SQLite state: decisions, feedback, contacts and a small key/value table.

Ids, classes, probs and timestamps only, plus the lower-cased addresses you have sent mail to (contacts).
In `decisions`, `cls` is the final class (what was labeled or recorded), `model_cls` the model's own top
class and `prob` / `probs` the model's probabilities, so an evaluation can tell the model from the policy.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

# Each entry migrates version i -> i+1. Append only; never edit a shipped migration.
MIGRATIONS: list[str] = [
    """
    CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE decisions (
        msg_id TEXT PRIMARY KEY, cls TEXT NOT NULL, prob REAL NOT NULL, probs TEXT NOT NULL,
        model_id TEXT NOT NULL, applied INTEGER NOT NULL, ts REAL NOT NULL
    );
    CREATE INDEX decisions_ts ON decisions (ts);
    CREATE TABLE feedback (
        msg_id TEXT PRIMARY KEY, predicted TEXT, corrected TEXT, ts REAL NOT NULL
    );
    """,
    # v2: which of our class labels are on the message now as far as we know (comma-joined, NULL = none),
    # and whether the decision was made in dry-run mode (so a later real run may still label it).
    """
    ALTER TABLE decisions ADD COLUMN current TEXT;
    ALTER TABLE decisions ADD COLUMN dry_run INTEGER NOT NULL DEFAULT 0;
    UPDATE decisions SET current = CASE WHEN applied = 1 THEN cls END;
    """,
    # v3: addresses you have sent mail to (lower-cased, nothing else) and the model's own class next to
    # the final one (NULL for decisions made before v3, whose raw model class was not kept).
    """
    CREATE TABLE contacts (address TEXT PRIMARY KEY, first_sent_ms INTEGER);
    ALTER TABLE decisions ADD COLUMN model_cls TEXT;
    """,
]
SCHEMA_VERSION = len(MIGRATIONS)


class Store:
    def __init__(self, path: str | Path, *, clock: Callable[[], float] = time.time):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=NORMAL")
            self._db.execute("PRAGMA busy_timeout=5000")
            self._migrate()

    # -- schema -------------------------------------------------------------------------------
    def _migrate(self) -> None:
        self._db.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
        row = self._db.execute("SELECT version FROM schema_version").fetchone()
        if row is None:
            self._db.execute("INSERT INTO schema_version VALUES (0)")
            v = 0
        else:
            v = int(row[0])
        if v > SCHEMA_VERSION:
            raise RuntimeError(
                f"state db {self.path} is schema v{v}, newer than this email-town-labeler (v{SCHEMA_VERSION})"
            )
        for i in range(v, SCHEMA_VERSION):
            self._db.execute("BEGIN")
            try:
                for stmt in filter(str.strip, MIGRATIONS[i].split(";")):
                    self._db.execute(stmt)
                self._db.execute("UPDATE schema_version SET version = ?", (i + 1,))
                self._db.execute("COMMIT")
            except Exception:
                self._db.execute("ROLLBACK")
                raise

    @property
    def schema_version(self) -> int:
        with self._lock:
            return int(self._db.execute("SELECT version FROM schema_version").fetchone()[0])

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # -- kv -----------------------------------------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            row = self._db.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def set(self, key: str, value: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO kv VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value)),
            )

    def delete(self, key: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM kv WHERE key = ?", (key,))

    # -- decisions ----------------------------------------------------------------------------
    def record_decision(
        self,
        msg_id: str,
        cls: str,
        prob: float,
        probs: dict[str, float],
        model_id: str,
        applied: bool,
        *,
        dry_run: bool = False,
        model_cls: str | None = None,
    ) -> None:
        """`cls` is the final class; `model_cls`, `prob` and `probs` are what the model said."""
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO decisions (msg_id, cls, prob, probs, model_id, applied, ts, current,"
                " dry_run, model_cls) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    msg_id,
                    cls,
                    float(prob),
                    json.dumps({k: round(float(v), 6) for k, v in probs.items()}),
                    model_id,
                    int(bool(applied)),
                    self.clock(),
                    cls if applied else None,
                    int(bool(dry_run)),
                    model_cls,
                ),
            )

    @staticmethod
    def _row(r: sqlite3.Row) -> dict:
        d = dict(r)
        if "probs" in d:
            d["probs"] = json.loads(d["probs"])
        for k in ("applied", "dry_run"):
            if k in d:
                d[k] = bool(d[k])
        return d

    def decision(self, msg_id: str) -> dict | None:
        with self._lock:
            r = self._db.execute("SELECT * FROM decisions WHERE msg_id = ?", (msg_id,)).fetchone()
        return self._row(r) if r else None

    def decided(self, msg_ids: list[str], *, include_dry_run: bool = True) -> set[str]:
        """The subset of ids that already have a decision (dry-run decisions optionally ignored)."""
        out: set[str] = set()
        extra = "" if include_dry_run else " AND dry_run = 0"
        with self._lock:
            for i in range(0, len(msg_ids), 500):
                chunk = msg_ids[i : i + 500]
                q = f"SELECT msg_id FROM decisions WHERE msg_id IN ({','.join('?' * len(chunk))}){extra}"
                out.update(r[0] for r in self._db.execute(q, chunk))
        return out

    def recent_decisions(self, n: int = 20) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM decisions ORDER BY ts DESC, rowid DESC LIMIT ?", (n,)
            ).fetchall()
        return [self._row(r) for r in rows]

    def set_current(self, msg_id: str, cls: str | None) -> None:
        """Remember which of our class labels are on the message now (comma-joined; None = none)."""
        with self._lock:
            self._db.execute("UPDATE decisions SET current = ? WHERE msg_id = ?", (cls, msg_id))

    def clear_applied(self) -> int:
        with self._lock:
            return self._db.execute(
                "UPDATE decisions SET applied = 0, current = NULL WHERE applied = 1 OR current IS NOT NULL"
            ).rowcount

    # -- contacts -----------------------------------------------------------------------------
    _UPSERT_CONTACT = (
        "INSERT INTO contacts VALUES (?, ?) ON CONFLICT(address) DO UPDATE SET first_sent_ms = CASE"
        " WHEN contacts.first_sent_ms IS NULL OR excluded.first_sent_ms < contacts.first_sent_ms"
        " THEN excluded.first_sent_ms ELSE contacts.first_sent_ms END"
    )

    def _write_contacts(self, sent: Mapping[str, int | None], *, replace: bool) -> int:
        rows = [(a.strip().lower(), ms) for a, ms in sent.items() if a and a.strip()]
        with self._lock:
            self._db.execute("BEGIN")
            try:
                if replace:
                    self._db.execute("DELETE FROM contacts")
                before = self._db.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
                self._db.executemany(self._UPSERT_CONTACT, rows)
                after = self._db.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
                self._db.execute("COMMIT")
            except Exception:
                self._db.execute("ROLLBACK")
                raise
        return int(after - before)

    def add_contacts(self, sent: Mapping[str, int | None]) -> int:
        """Add address -> first-sent time (ms; the earliest wins). Returns how many addresses are new."""
        return self._write_contacts(sent, replace=False)

    def replace_contacts(self, sent: Mapping[str, int | None]) -> int:
        """Atomically replace the whole table. Returns its new size."""
        return self._write_contacts(sent, replace=True)

    def known_contacts(self, addresses: list[str]) -> set[str]:
        """The subset of (lower-cased) addresses you have sent mail to."""
        want = list(dict.fromkeys(a.lower() for a in addresses if a))
        out: set[str] = set()
        with self._lock:
            for i in range(0, len(want), 500):
                chunk = want[i : i + 500]
                q = f"SELECT address FROM contacts WHERE address IN ({','.join('?' * len(chunk))})"
                out.update(r[0] for r in self._db.execute(q, chunk))
        return out

    def contacts_count(self) -> int:
        with self._lock:
            return int(self._db.execute("SELECT COUNT(*) FROM contacts").fetchone()[0])

    def contact_addresses(self) -> list[str]:
        """Every stored address. Only for `contacts list --yes-show-addresses`; never log these."""
        with self._lock:
            return [r[0] for r in self._db.execute("SELECT address FROM contacts ORDER BY address")]

    # -- feedback -----------------------------------------------------------------------------
    def record_feedback(self, msg_id: str, predicted: str | None, corrected: str | None) -> None:
        """Latest correction per message wins."""
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO feedback VALUES (?, ?, ?, ?)",
                (msg_id, predicted, corrected, self.clock()),
            )

    def feedback(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM feedback ORDER BY ts, rowid").fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> dict:
        with self._lock:
            by_cls = {
                r[0]: r[1] for r in self._db.execute("SELECT cls, COUNT(*) FROM decisions GROUP BY cls")
            }
            applied = self._db.execute("SELECT COUNT(*) FROM decisions WHERE applied = 1").fetchone()[0]
            total = self._db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
            fb = self._db.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
            last = self._db.execute("SELECT MAX(ts) FROM decisions").fetchone()[0]
            contacts = self._db.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
        return {
            "contacts": contacts,
            "decisions": total,
            "applied": applied,
            "by_class": by_cls,
            "feedback": fb,
            "last_decision_ts": last,
        }
