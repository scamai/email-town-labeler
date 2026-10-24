from __future__ import annotations

import sqlite3
import threading

import pytest

from email_town_labeler import state
from email_town_labeler.state import SCHEMA_VERSION, Store


def test_fresh_store_is_wal_and_versioned(tmp_path):
    s = Store(tmp_path / "x" / "state.sqlite")
    assert s.schema_version == SCHEMA_VERSION
    assert s._db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    s.close()
    assert Store(tmp_path / "x" / "state.sqlite").schema_version == SCHEMA_VERSION  # reopen is a no-op


def test_migrates_v1_database(tmp_path):
    p = tmp_path / "old.sqlite"
    db = sqlite3.connect(p)
    db.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    db.execute("INSERT INTO schema_version VALUES (1)")
    db.executescript(state.MIGRATIONS[0])
    db.execute("INSERT INTO decisions VALUES ('m1', 'orders', 0.9, '{}', 'rules@1', 1, 5.0)")
    db.commit()
    db.close()
    s = Store(p)
    assert s.schema_version == SCHEMA_VERSION
    d = s.decision("m1")
    assert d["current"] == "orders" and d["dry_run"] is False and d["applied"] is True


def test_refuses_newer_schema(tmp_path):
    p = tmp_path / "new.sqlite"
    Store(p).close()
    db = sqlite3.connect(p)
    db.execute("UPDATE schema_version SET version = 999")
    db.commit()
    db.close()
    with pytest.raises(RuntimeError, match="newer"):
        Store(p)


def test_kv_decisions_feedback_stats(tmp_path):
    t = [100.0]
    s = Store(tmp_path / "s.sqlite", clock=lambda: t[0])
    assert s.get("k") is None and s.get("k", "d") == "d"
    s.set("k", "1")
    s.set("k", "2")
    assert s.get("k") == "2"
    s.record_decision("a", "orders", 0.9, {"orders": 0.9, "unknown": 0.1}, "rules@1", True)
    t[0] = 101.0
    s.record_decision("b", "unknown", 0.3, {"unknown": 0.3}, "rules@1", False, dry_run=True)
    assert s.decision("a")["probs"] == {"orders": 0.9, "unknown": 0.1}
    assert s.decision("zzz") is None
    assert [d["msg_id"] for d in s.recent_decisions(5)] == ["b", "a"]
    assert s.decided(["a", "b", "c"]) == {"a", "b"}
    assert s.decided(["a", "b"], include_dry_run=False) == {"a"}
    s.record_feedback("a", "orders", "travel")
    s.record_feedback("a", "orders", None)  # latest per message wins
    s.record_feedback("z", None, "updates")
    assert [(f["msg_id"], f["corrected"]) for f in s.feedback()] == [("a", None), ("z", "updates")]
    st = s.stats()
    assert st["decisions"] == 2 and st["applied"] == 1 and st["feedback"] == 2
    assert st["by_class"] == {"orders": 1, "unknown": 1} and st["last_decision_ts"] == 101.0
    assert s.clear_applied() == 1 and s.decision("a")["current"] is None


def test_no_content_columns(tmp_path):
    s = Store(tmp_path / "s.sqlite")
    cols = {r[1] for t in ("decisions", "feedback", "kv") for r in s._db.execute(f"PRAGMA table_info({t})")}
    assert not cols & {"subject", "sender", "from", "snippet", "body"}


def test_thread_safe_writes(tmp_path):
    s = Store(tmp_path / "s.sqlite")

    def work(k):
        for i in range(50):
            s.record_decision(f"{k}-{i}", "orders", 0.9, {}, "m@1", True)
            s.set(f"k{k}", str(i))

    ts = [threading.Thread(target=work, args=(k,)) for k in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert s.stats()["decisions"] == 200


def test_migrates_v2_database_to_contacts_and_model_cls(tmp_path):
    p = tmp_path / "v2.sqlite"
    db = sqlite3.connect(p)
    db.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    db.execute("INSERT INTO schema_version VALUES (2)")
    for m in state.MIGRATIONS[:2]:
        db.executescript(m)
    db.execute("INSERT INTO decisions VALUES ('m1', 'orders', 0.9, '{}', 'rules@1', 1, 5.0, 'orders', 0)")
    db.commit()
    db.close()
    s = Store(p)
    assert s.schema_version == SCHEMA_VERSION == 5
    d = s.decision("m1")
    assert d["cls"] == "orders" and d["model_cls"] is None  # the raw class was never kept before v3
    s.record_decision("m2", "new_sender", 0.6, {"unknown": 0.6}, "rules@1", True, model_cls="unknown")
    assert s.decision("m2")["model_cls"] == "unknown" and s.contacts_count() == 0


def test_contacts_table(tmp_path):
    s = Store(tmp_path / "s.sqlite")
    assert s.add_contacts({"Pal@Example.test": 200, "b@x.test": None}) == 2
    assert s.add_contacts({"pal@example.test": 100, "b@x.test": 50, "c@x.test": 300}) == 1
    rows = dict(s._db.execute("SELECT address, first_sent_ms FROM contacts").fetchall())
    assert rows == {"pal@example.test": 100, "b@x.test": 50, "c@x.test": 300}  # lower-cased, earliest wins
    assert s.add_contacts({"pal@example.test": 999}) == 0
    assert (
        s._db.execute("SELECT first_sent_ms FROM contacts WHERE address = 'pal@example.test'").fetchone()[0]
        == 100
    )
    assert s.known_contacts(["PAL@example.test", "zz@x.test", ""]) == {"pal@example.test"}
    assert s.known_contacts([f"n{i}@x.test" for i in range(1200)] + ["c@x.test"]) == {"c@x.test"}
    assert s.contacts_count() == 3 and s.stats()["contacts"] == 3
    assert s.replace_contacts({"only@x.test": 1}) == 1 and s.contact_addresses() == ["only@x.test"]
    cols = {r[1] for r in s._db.execute("PRAGMA table_info(contacts)")}
    assert cols == {"address", "first_sent_ms"}  # no names, no content
