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

    # No undo window: these tests are about what reaches the provider, not when (see test_desktop_mail).
    workbench = Workbench(root, paths, reader_factory=factory, classifier=object(), undo_seconds=0)
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
        read_ahead = 0  # a provider where bodies cost quota: only what the model needs

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


def test_prefetch_reads_neighbours_in_one_batch_and_skips_cached_and_busy(tmp_path):
    from email_town_labeler.desktop.manager import PREFETCH_MAX

    workbench, readers, first, second = _workbench(tmp_path)

    class NoAhead(BatchReader):
        read_ahead = 0

    readers[first] = NoAhead([_record(f"m{i}") for i in range(12)])
    readers[second] = NoAhead([])
    workbench.model = None
    workbench._classify = lambda items: None
    workbench._refresh()
    keys = [m["id"] for m in workbench.snapshot()["messages"]]
    readers[first].batches.clear()
    workbench.detail(keys[0])  # opened: cached already
    readers[first].batches.clear()
    assert workbench.prefetch(keys[:10]) == {"fetched": PREFETCH_MAX - 1, "ids": keys[:PREFETCH_MAX]}
    assert len(readers[first].batches) == 1  # one request, not one per message
    # all cached now: nothing fetched, and the page may read every one of them
    assert workbench.prefetch(keys[:PREFETCH_MAX]) == {"fetched": 0, "ids": keys[:PREFETCH_MAX]}
    # A busy account is skipped, never waited on: the open it serves must not slow down.
    lock = workbench.reader_locks[first]
    with lock:
        result = {}
        t = threading.Thread(target=lambda: result.update(workbench.prefetch(keys[10:])))
        t.start()
        t.join(2)
    assert result == {"fetched": 0, "ids": []}  # nothing for the page to read: it opens them itself later
    with pytest.raises(ValueError):
        workbench.prefetch("not a list")
    workbench.close()


def test_bundle_choice_reaches_the_snapshot_and_survives_a_restart(tmp_path):
    workbench, _readers, _first, _second = _workbench(tmp_path)
    snapshot = workbench.snapshot()
    assert "newsletters" in snapshot["prefs"]["bundled"]
    assert "action_needed" not in snapshot["bundleable"]
    version = workbench.version
    assert workbench.set_bundled("newsletters", False)["prefs"]["bundled"].count("newsletters") == 0
    assert workbench.version > version  # a waiting page hears about it at once
    assert "newsletters" not in workbench.snapshot()["prefs"]["bundled"]
    with pytest.raises(ValueError, match="Choose whether"):
        workbench.set_bundled("orders", "yes")
    workbench.close()
    restarted = Workbench(tmp_path / "workbench", workbench.paths, classifier=object(), undo_seconds=0)
    assert "newsletters" not in restarted.snapshot()["prefs"]["bundled"]
    restarted.close()


def _from(provider_id, sender, *, category="updates"):
    return {**_record(provider_id, category=category), "headers": {"subject": "Fixture", "from": sender}}


def test_a_sender_rule_moves_that_senders_other_mail_and_undo_takes_it_back(tmp_path):
    workbench, readers, first, _second = _workbench(tmp_path)
    readers[first] = FakeReader(
        [
            _from("n1", "Digest <digest@example.test>"),
            _from("n2", "Digest <DIGEST@example.test>"),
            _from("p1", "Person <person@example.test>", category="action_needed"),
        ]
    )
    workbench._refresh()
    digest = [m for m in workbench.snapshot()["messages"] if "digest@" in m["sender"].lower()]
    person = next(m for m in workbench.snapshot()["messages"] if "person@" in m["sender"])
    result = workbench.action(
        {"ids": [digest[0]["id"]], "action": "category", "category": "newsletters", "sender_rule": "set"}
    )
    assert result["senders"] == 1
    after = {m["id"]: m for m in workbench.snapshot()["messages"]}
    other = after[digest[1]["id"]]  # never selected, same address in other case
    assert (other["category"], other["source"], other["auto_category"]) == (
        "newsletters",
        "sender",
        "updates",
    )
    assert after[digest[0]["id"]]["source"] == "manual"  # the one chosen is also a per-message choice
    assert after[digest[0]["id"]]["sender_rule"] == "newsletters"  # still shows the sender has a rule
    assert "sender_rule" not in after[person["id"]]
    assert after[person["id"]]["category"] == "action_needed"
    # A per-message choice beats the sender's rule.
    workbench.action({"ids": [other["id"]], "action": "category", "category": "orders"})
    assert {m["id"]: m for m in workbench.snapshot()["messages"]}[other["id"]]["category"] == "orders"
    # Undo of the rule change restores the rule set (none) and the chosen message.
    workbench.undo(result["undo"])
    assert workbench.sender_rules == {}
    assert {m["id"]: m for m in workbench.snapshot()["messages"]}[digest[0]["id"]]["category"] == "updates"
    stored = (tmp_path / "workbench" / "workflow.sqlite").read_bytes()
    assert b"digest@example.test" not in stored.lower() and b"digest" not in stored.lower()
    workbench.close()


def test_clearing_a_sender_rule_and_rules_survive_a_restart(tmp_path):
    workbench, readers, first, _second = _workbench(tmp_path)
    readers[first] = FakeReader([_from("n1", "a@example.test"), _from("n2", "a@example.test")])
    workbench._refresh()
    ids = [m["id"] for m in workbench.snapshot()["messages"]]
    workbench.action({"ids": ids[:1], "action": "category", "category": "travel", "sender_rule": "set"})
    restarted = Workbench(tmp_path / "workbench", workbench.paths, classifier=object(), undo_seconds=0)
    assert list(restarted.sender_rules.values()) == ["travel"]
    restarted.close()
    workbench.action({"ids": ids[1:], "action": "category", "category": "travel", "sender_rule": "clear"})
    assert workbench.sender_rules == {}
    with pytest.raises(ValueError, match="Unknown sender rule"):
        workbench.action(
            {"ids": ids[:1], "action": "category", "category": "travel", "sender_rule": "always"}
        )
    workbench.close()


class SplitModel:
    """Unsure marketing: promotional 0.55 or a newsletters/promotional split, never above 0.55."""

    manifest = ModelManifest("fixture", "1", "rules")

    def predict(self, records):
        out = []
        for record in records:
            if record.get("split"):
                probs = {"newsletters": 0.40, "promotional": 0.35, "unknown": 0.25}
            else:
                probs = {"promotional": 0.55, "newsletters": 0.15, "unknown": 0.30}
            best = max(probs, key=probs.get)
            out.append(Prediction(best, probs[best], probs))
        return out


def test_desktop_classifies_bulk_mail_by_its_own_headers(tmp_path):
    workbench, readers, first, _ = _workbench(tmp_path)
    workbench.model = SplitModel()
    bulk = {"list-unsubscribe": "<mailto:u@x.test>"}

    class HeaderReader(FakeReader):
        def detail(self, provider_id):
            self.detail_calls += 1
            return {
                "body_text": "body",
                "headers": bulk if provider_id.startswith("list") else {},
                "label_ids": ["CATEGORY_PROMOTIONS"] if provider_id == "tab" else [],
                "split": provider_id.endswith("split"),
            }

    ids = ["list", "list-split", "tab", "direct", "direct-split"]
    readers[first] = HeaderReader([_record(i, category=None) for i in ids])
    workbench._refresh()
    rows = {row["id"]: row["category"] for row in workbench.snapshot()["messages"]}
    shown = {i: rows[message_key(first, i)] for i in ids}
    assert shown == {
        "list": "promotional",  # 0.55 clears the 0.5 bulk bar (promotional_threshold is 0.6)
        "list-split": "newsletters",  # 0.40 + 0.35 of bulk classes clears min_confidence
        "tab": "promotional",  # Gmail's Promotions tab counts as bulk
        "direct": "unknown",
        "direct-split": "unknown",
    }
    workbench.close()


def test_the_none_of_these_class_is_shown_as_people(tmp_path):
    # "Unclassified" read as a failure; what the model puts there is mail written by people.
    workbench, _readers, _first, _ = _workbench(tmp_path)
    names = {c["id"]: c["name"] for c in workbench.snapshot()["categories"]}
    assert names["unknown"] == "People" and "Unclassified" not in names.values()
    workbench.close()


def test_a_refused_sender_rule_changes_nothing(tmp_path):
    # People and New sender are not decisions a rule can make: refusing the rule must not keep the category.
    workbench, readers, first, _second = _workbench(tmp_path)
    readers[first] = FakeReader([_from("n1", "a@example.test")])
    workbench._refresh()
    (row,) = workbench.snapshot()["messages"]
    for category in ("unknown", "new_sender"):
        with pytest.raises(ValueError, match="Choose a category for this sender"):
            workbench.action(
                {"ids": [row["id"]], "action": "category", "category": category, "sender_rule": "set"}
            )
        (after,) = workbench.snapshot()["messages"]
        assert (after["category"], after["source"]) == (row["category"], row["source"])
    assert workbench.sender_rules == {}
    workbench.close()


class RefreshError(Exception):  # stands in for google.auth.exceptions.RefreshError, matched by name
    def __init__(self, *args, retryable=False):
        super().__init__(*args)
        self.retryable = retryable


class ExpiredReader(FakeReader):
    def __init__(self, error):
        super().__init__([])
        self.error = error

    def list(self, limit):
        raise self.error


def test_an_expired_google_sign_in_says_reconnect_and_is_logged(tmp_path, caplog):
    workbench, readers, first, second = _workbench(tmp_path)
    try:
        raise RefreshError("invalid_grant: Token has been expired or revoked.")
    except RefreshError as cause:
        wrapped = RuntimeError("wrapped")
        wrapped.__cause__ = cause
    readers[first] = ExpiredReader(wrapped)  # a refresh deep inside a request arrives wrapped
    readers[second] = ExpiredReader(RefreshError("server busy", retryable=True))
    with caplog.at_level("WARNING", logger="email_town_labeler.desktop"):
        workbench._refresh()
    status = workbench.account_status
    assert status[first]["status"] == "error"
    assert status[first]["error"] == "Google sign-in expired. Reconnect the account from its ⋯ menu"
    assert "Token has been" not in status[first]["error"]
    assert status[second]["error"].startswith("Sync failed.")  # retryable: not a reason to reconnect
    assert "sync inbox failed (RuntimeError)" in caplog.text
    assert "sync inbox failed (RefreshError)" in caplog.text
    workbench.close()


def _settle(workbench, deadline=5.0):
    end = time.monotonic() + deadline
    while workbench.snapshot()["refreshing"] and time.monotonic() < end:
        time.sleep(0.01)


def test_an_expired_sign_in_is_not_retried_every_minute_until_refresh_or_reconnect(tmp_path, caplog):
    """Each 60 s check rebuilt the expired account's reader: one rejected token refresh and one warning a
    minute, the error banner cleared and set again each time (20 in a row in one owner's desktop.log)."""
    workbench, readers, first, second = _workbench(tmp_path)
    built = []
    factory = workbench.reader_factory
    workbench.reader_factory = lambda account: built.append(account["id"]) or factory(account)
    readers[first] = ExpiredReader(RefreshError("invalid_grant: Token has been expired or revoked."))
    readers[second] = FakeReader([_record("fine")])
    with caplog.at_level("WARNING", logger="email_town_labeler.desktop"):
        for _ in range(3):  # the scheduled check, three minutes running
            workbench._refresh()
    assert built.count(first) == 1, built
    assert caplog.text.count("sync inbox failed (RefreshError) account " + first[:8]) == 1
    assert "not checked again until Refresh or Reconnect" in caplog.text
    assert workbench.account_status[first]["error"].startswith("Google sign-in expired")
    assert workbench.account_status[second]["status"] == "ready"  # the other account keeps syncing

    workbench.refresh(retry_signed_out=True)  # the Refresh button
    _settle(workbench)
    assert built.count(first) == 2
    workbench.enable(first, True)  # enabling (or reconnecting) the account asks again too
    _settle(workbench)
    assert built.count(first) == 3
    workbench.close()


def test_list_lines_name_the_account_and_keep_connect_time_out_of_lock_wait(tmp_path, caplog):
    workbench, readers, first, second = _workbench(tmp_path)
    readers[first] = FakeReader([_record("a")])
    readers[second] = FakeReader([_record("b")])
    factory = workbench.reader_factory

    def slow_connect(account):
        time.sleep(0.2)  # a token refresh and getProfile before the first list
        return factory(account)

    workbench.reader_factory = slow_connect
    with caplog.at_level("INFO", logger="email_town_labeler.desktop"):
        workbench._refresh()
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("list inbox (")]
    assert {line.split()[3].rstrip("):") for line in lines} == {first[:8], second[:8]}, lines
    for line in lines:
        wait = float(line.split("lock wait ")[1].split(" ms")[0])
        connect = float(line.split("connect ")[1].split(" ms")[0])
        assert wait < 150 <= connect, line
    workbench.close()


def test_the_idle_model_is_released_only_after_a_refresh_has_classified(tmp_path):
    """The poll started a refresh and checked for 10 idle minutes at once, before that refresh had listed
    anything: new mail at the 10-minute mark ended the worker and respawned it 1-16 s later."""
    workbench, readers, first, second = _workbench(tmp_path)
    readers[first], readers[second] = FakeReader([_record("a")]), FakeReader([])
    events = []

    class Model:
        def release_if_idle(self):
            events.append("idle check")
            return False

    workbench.model = Model()
    read_ahead = workbench._read_ahead
    workbench._read_ahead = lambda *args: events.append("classified") or read_ahead(*args)
    workbench._refresh()
    assert events == ["classified", "classified", "idle check"], events
    workbench.close()
