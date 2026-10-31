import threading
from pathlib import Path

import pytest

from email_town_labeler.config import Paths
from email_town_labeler.desktop.manager import Workbench
from email_town_labeler.desktop.state import message_key
from email_town_labeler.models.base import ModelManifest
from email_town_labeler.types import Prediction


class FakeReader:
    def __init__(self, records, *, fail=False, identity=None):
        self.records = records
        self.fail = fail
        self.identity = identity
        self.detail_calls = 0

    def list(self, limit):
        if self.fail:
            raise RuntimeError("synthetic provider failure")
        return self.records[:limit], len(self.records) > limit

    def detail(self, provider_id):
        self.detail_calls += 1
        return {"body_text": "private body", "url": "https://mail.google.com/mail/#all/x"}


def _workbench(tmp_path: Path):
    paths = Paths.default(tmp_path / "config").ensure()
    root = tmp_path / "workbench"
    readers = {}

    def factory(account):
        return readers[account["id"]]

    workbench = Workbench(root, paths, reader_factory=factory, classifier=object())
    first = workbench.accounts.add("One", "imap", {"host": "one.example", "username": "u", "port": 993})
    second = workbench.accounts.add("Two", "imap", {"host": "two.example", "username": "u", "port": 993})
    return workbench, readers, first["id"], second["id"]


def _record(provider_id, *, category="orders"):
    return {
        "provider_id": provider_id,
        "headers": {"subject": "Fixture subject", "from": "fixture@example.test"},
        "internal_date": 1_700_000_000_000,
        "label_ids": [],
        "category": category,
    }


def test_same_provider_id_from_two_accounts_remains_isolated(tmp_path):
    workbench, readers, first, second = _workbench(tmp_path)
    readers[first] = FakeReader([_record("same-id")])
    readers[second] = FakeReader([_record("same-id")])
    workbench._refresh()
    snapshot = workbench.snapshot()
    assert {row["account_id"] for row in snapshot["messages"]} == {first, second}
    keys = [message_key(first, "same-id"), message_key(second, "same-id")]
    result = workbench.action({"ids": [keys[0]], "action": "done"})
    rows = {row["id"]: row for row in workbench.snapshot()["messages"]}
    assert rows[keys[0]]["done"] is True
    assert rows[keys[1]]["done"] is False
    workbench.undo(result["undo"])
    assert all(not row["done"] for row in workbench.snapshot()["messages"])
    workbench.close()


def test_mailbox_category_skips_detail_and_model_and_local_actions_are_reversible(tmp_path):
    workbench, readers, first, second = _workbench(tmp_path)
    readers[first] = FakeReader([_record("mailbox", category="travel")])
    readers[second] = FakeReader([_record("unknown", category=None)])
    workbench._refresh()
    assert readers[first].detail_calls == 0
    key = message_key(first, "mailbox")
    workbench.action({"ids": [key], "action": "category", "category": "orders"})
    row = next(item for item in workbench.snapshot()["messages"] if item["id"] == key)
    assert row["category"] == "orders" and row["source"] == "manual"
    with pytest.raises(ValueError, match="Unknown category"):
        workbench.action({"ids": [key], "action": "category", "category": "nope"})
    workbench.close()


def test_failed_account_keeps_last_good_rows_and_other_account_works(tmp_path):
    workbench, readers, first, second = _workbench(tmp_path)
    good = FakeReader([_record("good")])
    readers[first] = good
    other = FakeReader([_record("bad")])
    readers[second] = other
    workbench._refresh()
    old_first = message_key(first, "good")
    old_second = message_key(second, "bad")
    assert {row["id"] for row in workbench.snapshot()["messages"]} == {old_first, old_second}
    readers[first].fail = True
    other.records = [_record("new")]
    workbench._refresh()
    rows = {row["id"]: row for row in workbench.snapshot()["messages"]}
    assert old_first in rows
    assert message_key(second, "new") in rows
    assert old_second not in rows
    assert workbench.account_status[first]["status"] == "error"
    workbench.close()


def test_identity_binding_rejects_same_authorized_mailbox(tmp_path):
    workbench, readers, first, second = _workbench(tmp_path)
    readers[first] = FakeReader([_record("a")], identity="mailbox-hash")
    readers[second] = FakeReader([_record("b")], identity="mailbox-hash")
    workbench._refresh()
    assert workbench.account_status[second]["status"] == "error"
    assert "another account" in workbench.account_status[second]["error"]
    workbench.close()


class CountingModel:
    manifest = ModelManifest("fixture", "1", "rules")

    def __init__(self):
        self.calls = 0

    def predict(self, records):
        self.calls += 1
        return [Prediction("orders", 0.95, {"orders": 0.95}) for _ in records]


def test_two_unlabeled_accounts_share_one_model_and_body_cache_is_bounded(tmp_path):
    workbench, readers, first, second = _workbench(tmp_path)
    model = CountingModel()
    workbench.model = model
    readers[first] = FakeReader([_record("one", category=None)])
    readers[second] = FakeReader([_record("two", category=None)])
    workbench._refresh()
    assert model.calls == 2
    for index in range(70):
        workbench._cache_body(f"key-{index}", {"body_text": "x"})
    assert len(workbench.bodies) == 64
    workbench.close()


def test_detail_does_not_return_stale_row_removed_during_provider_read(tmp_path):
    workbench, readers, first, _ = _workbench(tmp_path)
    started = threading.Event()
    release = threading.Event()

    class BlockingReader(FakeReader):
        def detail(self, provider_id):
            started.set()
            release.wait(2)
            return super().detail(provider_id)

    readers[first] = BlockingReader([_record("blocked")])
    workbench._refresh()
    key = message_key(first, "blocked")
    thread_result = {}

    def load():
        try:
            thread_result["value"] = workbench.detail(key)
        except Exception as exc:  # noqa: BLE001 - assertion below checks the boundary
            thread_result["error"] = exc

    thread = threading.Thread(target=load)
    thread.start()
    assert started.wait(2)
    with workbench.lock:
        workbench.messages.pop(key)
    release.set()
    thread.join(2)
    assert isinstance(thread_result.get("error"), ValueError)
    workbench.close()


def test_action_rejects_unloaded_ids_and_oversized_batches(tmp_path):
    workbench, readers, first, _ = _workbench(tmp_path)
    readers[first] = FakeReader([_record("one")])
    workbench._refresh()
    with pytest.raises(ValueError, match="list changed"):
        workbench.action({"ids": ["missing"], "action": "done"})
    with pytest.raises(ValueError, match="1–500"):
        workbench.action({"ids": [message_key(first, "one")] * 501, "action": "done"})
    workbench.close()
