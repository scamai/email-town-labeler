import threading
import time
from pathlib import Path

import pytest

from email_town_labeler.config import Paths
from email_town_labeler.desktop.manager import BODY_CACHE, Workbench
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
    key = message_key(first, "mailbox")
    # The body is read ahead for display only; a mailbox label never goes to the model.
    row = next(item for item in workbench.snapshot()["messages"] if item["id"] == key)
    assert (row["category"], row["source"]) == ("travel", "mailbox")
    assert readers[first].detail_calls == 1 and key in workbench.bodies
    workbench.action({"ids": [key], "action": "category", "category": "orders"})
    row = next(item for item in workbench.snapshot()["messages"] if item["id"] == key)
    assert row["category"] == "orders" and row["source"] == "manual"
    with pytest.raises(ValueError, match="Unknown category"):
        workbench.action({"ids": [key], "action": "category", "category": "nope"})
    workbench.close()


def test_a_correction_keeps_the_automatic_category_for_accuracy_counts(tmp_path):
    """The sidebar counts a classification as wrong when the user moved it: the snapshot must say
    what the classifier (or the mailbox label) chose, alongside the user's category."""
    workbench, readers, first, second = _workbench(tmp_path)
    readers[first] = FakeReader([_record("labelled", category="travel"), _record("predicted", category=None)])
    readers[second] = FakeReader([_record("pending", category=None)])
    keys = {
        name: message_key(first if name != "pending" else second, name)
        for name in ("labelled", "predicted", "pending")
    }
    workbench.predictions[keys["predicted"]] = ("orders", 0.91)
    workbench._refresh()
    rows = {row["id"]: row for row in workbench.snapshot()["messages"]}
    assert [rows[keys[n]]["source"] for n in ("labelled", "predicted")] == ["mailbox", "model"]
    assert not any("auto_category" in row for row in rows.values())  # nothing corrected yet
    for key in keys.values():
        workbench.action({"ids": [key], "action": "category", "category": "updates"})
    rows = {row["id"]: row for row in workbench.snapshot()["messages"]}
    assert {n: (rows[keys[n]]["category"], rows[keys[n]].get("auto_category")) for n in keys} == {
        "labelled": ("updates", "travel"),
        "predicted": ("updates", "orders"),
        "pending": ("updates", None),  # nothing automatic to be wrong about
    }
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
    for index in range(BODY_CACHE + 6):
        workbench._cache_body(f"key-{index}", {"body_text": "x"})
    assert len(workbench.bodies) == BODY_CACHE
    workbench.close()


def test_detail_does_not_return_stale_row_removed_during_provider_read(tmp_path):
    workbench, readers, first, _ = _workbench(tmp_path)
    started = threading.Event()
    release = threading.Event()
    armed = threading.Event()

    class BlockingReader(FakeReader):
        def detail(self, provider_id):
            if armed.is_set():
                started.set()
                release.wait(2)
            return super().detail(provider_id)

    readers[first] = BlockingReader([_record("blocked")])
    workbench._refresh()
    key = message_key(first, "blocked")
    workbench.bodies.clear()  # drop the read-ahead copy so the preview has to ask the provider
    armed.set()
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


class BatchReader(FakeReader):
    """Records how bodies are requested; accepts a folder like the real readers."""

    def __init__(self, records, folders=None):
        super().__init__(records)
        self.folders = folders or {}
        self.batches = []

    def list(self, limit, folder="inbox"):
        records = self.folders.get(folder, self.records)
        return records[:limit], len(records) > limit

    def details(self, provider_ids):
        self.batches.append(list(provider_ids))
        return {pid: {"body_text": "body " + pid} for pid in provider_ids}


def test_read_ahead_batches_bodies_and_model_calls(tmp_path):
    from email_town_labeler.desktop.manager import BODY_CHUNK, PREFETCH

    workbench, readers, first, second = _workbench(tmp_path)
    model = CountingModel()
    workbench.model = model
    labelled = [_record(f"l{i}") for i in range(PREFETCH + 5)]
    unlabelled = [_record(f"u{i}", category=None) for i in range(BODY_CHUNK + 2)]
    readers[first] = BatchReader(labelled + unlabelled)
    readers[second] = BatchReader([])
    workbench._refresh()
    fetched = [pid for batch in readers[first].batches for pid in batch]
    # Newest PREFETCH bodies for display plus every unlabelled one for the model, nothing else.
    assert fetched == [r["provider_id"] for r in labelled[:PREFETCH] + unlabelled]
    assert all(len(batch) <= BODY_CHUNK for batch in readers[first].batches)
    assert model.calls == 2  # one model call per chunk that holds unlabelled mail
    rows = {row["id"]: row for row in workbench.snapshot()["messages"]}
    assert all(rows[message_key(first, r["provider_id"])]["source"] == "model" for r in unlabelled)
    readers[first].detail_calls = 0
    assert workbench.detail(message_key(first, "l0"))["body"] == "body l0"
    assert readers[first].detail_calls == 0  # opened from the read-ahead copy
    workbench.close()


def test_preview_waits_for_a_busy_account_instead_of_failing(tmp_path):
    workbench, readers, first, second = _workbench(tmp_path)
    readers[first] = FakeReader([_record("one")])
    readers[second] = FakeReader([])
    workbench._refresh()
    key = message_key(first, "one")
    workbench.bodies.clear()
    held = threading.Event()

    def busy():  # a list read longer than the old 1 s preview timeout
        with workbench.reader_locks[first]:
            held.set()
            time.sleep(1.5)

    threading.Thread(target=busy).start()
    assert held.wait(2)
    assert workbench.detail(key)["body"] == "private body"
    workbench.close()


def test_folder_switch_shows_the_last_list_while_it_revalidates(tmp_path):
    workbench, readers, first, second = _workbench(tmp_path)
    gate = threading.Event()

    class SlowInbox(BatchReader):
        def list(self, limit, folder="inbox"):
            if folder == "inbox" and self.batches:
                gate.wait(2)
            return super().list(limit, folder)

    readers[first] = SlowInbox([_record("in")], {"sent": [_record("out")]})
    readers[second] = BatchReader([])
    workbench._refresh()
    workbench.change_folder("sent")
    deadline = time.monotonic() + 2
    while workbench.snapshot()["refreshing"] and time.monotonic() < deadline:
        time.sleep(0.01)
    assert [r["id"] for r in workbench.snapshot()["messages"]] == [message_key(first, "out")]
    workbench.change_folder("inbox")
    # The revalidating read is still blocked, yet the Inbox rows are already there.
    assert [r["id"] for r in workbench.snapshot()["messages"]] == [message_key(first, "in")]
    gate.set()
    workbench.close()


def test_state_long_poll_answers_on_change_and_times_out_unchanged(tmp_path):
    workbench, readers, first, second = _workbench(tmp_path)
    readers[first] = FakeReader([_record("one")])
    readers[second] = FakeReader([])
    workbench._refresh()
    etag = workbench.snapshot()["etag"]
    assert workbench.wait_snapshot("stale")["etag"] == etag  # differs: immediate full answer
    started = time.monotonic()
    assert workbench.wait_snapshot(etag, timeout=0.3) == {
        "unchanged": True,
        "etag": etag,
        "seq": workbench.seq,
    }
    assert time.monotonic() - started >= 0.3
    key = message_key(first, "one")
    threading.Timer(0.2, lambda: workbench.action({"ids": [key], "action": "done"})).start()
    started = time.monotonic()
    changed = workbench.wait_snapshot(etag, timeout=5)
    assert changed["etag"] != etag and changed["messages"][0]["done"]
    assert time.monotonic() - started < 1.5
    workbench.close()


def test_progressive_reader_rows_show_before_the_list_finishes(tmp_path):
    workbench, readers, first, second = _workbench(tmp_path)
    gate, partial_shown = threading.Event(), threading.Event()

    class Progressive(BatchReader):
        progressive = True

        def list(self, limit, folder="inbox", progress=None):
            progress(self.records[:1])
            partial_shown.set()
            gate.wait(2)
            return self.records[:limit], False

    readers[first] = Progressive([_record("newest"), _record("older")])
    readers[second] = BatchReader([])
    workbench.refresh()
    assert partial_shown.wait(2)
    assert [r["id"] for r in workbench.snapshot()["messages"]] == [message_key(first, "newest")]
    gate.set()
    deadline = time.monotonic() + 2
    while len(workbench.snapshot()["messages"]) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(workbench.snapshot()["messages"]) == 2
    workbench.close()


def test_reader_without_read_ahead_fetches_only_bodies_the_model_needs(tmp_path):
    workbench, readers, first, second = _workbench(tmp_path)
    workbench.model = CountingModel()

    class QuotaReader(BatchReader):
        read_ahead = 0  # like Gmail: a body costs quota and opening one is fast

    readers[first] = QuotaReader([_record("labelled"), _record("unlabelled", category=None)])
    readers[second] = BatchReader([])
    workbench._refresh()
    assert readers[first].batches == [["unlabelled"]]
    workbench.close()


def test_sent_mail_is_not_sent_to_the_model(tmp_path):
    workbench, readers, first, second = _workbench(tmp_path)
    model = CountingModel()
    workbench.model = model
    mine = [_record(f"s{i}", category=None) for i in range(3)]
    readers[first] = BatchReader([], {"sent": mine})
    readers[second] = BatchReader([])
    workbench._refresh()
    readers[first].batches.clear()
    workbench.change_folder("sent")
    deadline = time.monotonic() + 2
    while (
        workbench.snapshot()["refreshing"] or not workbench.snapshot()["messages"]
    ) and time.monotonic() < deadline:
        time.sleep(0.01)
    rows = workbench.snapshot()["messages"]
    assert {(r["source"], r["category"]) for r in rows} == {("none", "unknown")} and len(rows) == 3
    # Only the read-ahead for display; no body is read for, and nothing goes to, the model.
    assert model.calls == 0


def test_generated_default_account_name_shows_mailbox_address(tmp_path):
    workbench, readers, first, second = _workbench(tmp_path)
    legacy = workbench.accounts.add(
        "当前 Gmail", "imap", {"host": "three.example", "username": "u", "port": 993}
    )
    assert {a["name"] for a in workbench.snapshot()["accounts"]} == {"One", "Two", "Gmail"}
    for aid in (first, second, legacy["id"]):
        readers[aid] = FakeReader([])
        readers[aid].address = "owner@example.test"
    workbench._refresh()
    names = {a["id"]: a["name"] for a in workbench.snapshot()["accounts"]}
    # Only the app-generated name is replaced; names the user chose stay exactly as typed.
    assert names == {first: "One", second: "Two", legacy["id"]: "owner@example.test"}
    assert workbench.accounts.get(legacy["id"])["name"] == "当前 Gmail"  # display only, address not persisted
    workbench.close()
