"""Opening a message marks it read, as in Gmail: shown at once, written in the background, never fought by a stale list."""

import threading
import time

import pytest

from email_town_labeler.desktop.state import message_key
from tests.test_desktop_manager import FakeReader, _record, _workbench


class Writable(FakeReader):
    def __init__(self, records, *, fail=False, fail_actions=()):
        super().__init__(records)
        self.attempts, self.writes, self.fail_writes, self.gate = [], [], fail, threading.Event()
        self.fail_actions = set(fail_actions)
        self.started, self.release = {}, {}
        self.gate.set()

    def capabilities(self):
        return {"write": True, "send": False, "attachments": False, "max_attachment_bytes": 0, "reason": ""}

    def sequence(self, *actions):
        self.started = {action: threading.Event() for action in actions}
        self.release = {action: threading.Event() for action in actions}

    def mutate(self, provider_id, action):
        self.attempts.append((provider_id, action))
        if action in self.release:
            self.started[action].set()
            assert self.release[action].wait(5), f"{action} gate never opened"
        else:
            assert self.gate.wait(5), "write gate never opened"
        if self.fail_writes or action in self.fail_actions:
            raise RuntimeError("synthetic write failure")
        self.writes.append((provider_id, action))
        record = next(r for r in self.records if r["provider_id"] == provider_id)
        labels = [x for x in record["label_ids"] if x != "UNREAD"]
        record["label_ids"] = labels + (["UNREAD"] if action == "unread" else [])


class RefreshObserver:
    def __init__(self, workbench, aid, *, gate_publication=True):
        self.workbench, self.aid = workbench, aid
        self.publication_waiting, self.release_publication, self.published = (
            threading.Event(),
            threading.Event(),
            threading.Event(),
        )
        if not gate_publication:
            self.release_publication.set()
        self.outbox_seen, self.outbox_empty = threading.Event(), threading.Event()
        self.original_rows, self.original_touch = workbench._rows, workbench._touch
        workbench._rows, workbench._touch = self.rows, self.touch

    def rows(self, aid, records, folder):
        if aid == self.aid and not self.publication_waiting.is_set():
            self.publication_waiting.set()
            assert self.release_publication.wait(5), "publication gate never opened"
        return self.original_rows(aid, records, folder)

    def touch(self):
        self.original_touch()
        if self.workbench.outbox_jobs:
            self.outbox_seen.set()
        elif self.outbox_seen.is_set():
            self.outbox_empty.set()
        status = self.workbench.account_status.get(self.aid, {}).get("status")
        if status == "classifying" and self.release_publication.is_set():
            self.published.set()

    def restore(self):
        self.workbench._rows, self.workbench._touch = self.original_rows, self.original_touch


def _unread(provider_id):
    return {**_record(provider_id), "label_ids": ["UNREAD"]}


def _row(workbench, key):
    return next(r for r in workbench.snapshot()["messages"] if r["id"] == key)


def _until(check, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not check() and time.monotonic() < deadline:
        time.sleep(0.01)
    return check()


def test_open_marks_read_now_writes_in_background_and_a_stale_list_does_not_undo_it(tmp_path):
    workbench, readers, first, second = _workbench(tmp_path)
    listed, release = threading.Event(), threading.Event()

    class SlowList(Writable):
        def list(self, limit):
            rows = [dict(r, label_ids=list(r["label_ids"])) for r in self.records]  # read before the write
            if release.is_set():
                return rows, False
            listed.set()
            release.wait(5)
            return rows, False

    reader = SlowList([_unread("one")])
    readers[first], readers[second] = reader, FakeReader([])
    release.set()
    workbench._refresh()
    workbench.refresh = lambda **kwargs: None
    key = message_key(first, "one")
    release.clear()
    syncing = threading.Thread(target=workbench._refresh)
    syncing.start()
    assert listed.wait(5)  # a sync has read UNREAD and holds the account
    assert workbench.mark_read(key).items() >= {"ok": True, "changed": True}.items()
    assert _row(workbench, key)["unread"] is False  # shown read before the mailbox confirms
    release.set()  # the stale list now publishes UNREAD; the write waits for the account
    syncing.join(5)
    assert _row(workbench, key)["unread"] is False
    assert _until(lambda: reader.writes == [("one", "read")])
    assert _until(lambda: not workbench.snapshot()["outbox"])
    workbench._refresh()
    assert "UNREAD" not in reader.records[0]["label_ids"] and _row(workbench, key)["unread"] is False
    assert workbench.mark_read(key) == {"ok": True, "changed": False}  # already read: no second write
    assert reader.attempts == [("one", "read")]
    workbench.close()


def test_a_failed_write_puts_the_row_back(tmp_path):
    workbench, readers, first, second = _workbench(tmp_path)
    reader = Writable([_unread("one")], fail=True)
    readers[first], readers[second] = reader, FakeReader([])
    workbench._refresh()
    workbench.refresh = lambda **kwargs: None
    key = message_key(first, "one")
    workbench.mark_read(key)
    assert _until(lambda: not workbench.snapshot()["outbox"])
    assert "UNREAD" in reader.records[0]["label_ids"] and _row(workbench, key)["unread"] is True
    workbench.close()


def test_read_only_or_paused_accounts_are_left_unread(tmp_path):
    workbench, readers, first, second = _workbench(tmp_path)
    readers[first], readers[second] = FakeReader([_unread("ro")]), Writable([_unread("rw")])
    workbench._refresh()
    assert workbench.mark_read(message_key(first, "ro")) == {"ok": False, "changed": False}
    assert _row(workbench, message_key(first, "ro"))["unread"] is True
    workbench.enable(second, False)
    assert workbench.mark_read(message_key(second, "rw"))["ok"] is False
    workbench.close()


def test_an_explicit_mark_unread_wins_over_open_to_read(tmp_path):
    workbench, readers, first, second = _workbench(tmp_path)
    reader = Writable([_unread("one")])
    readers[first], readers[second] = reader, FakeReader([])
    workbench._refresh()
    workbench.refresh = lambda **kwargs: None
    key = message_key(first, "one")
    reader.sequence("read")
    assert workbench.mark_read(key).items() >= {"ok": True, "changed": True}.items()
    assert reader.started["read"].wait(5)  # the open is in flight before the explicit unread
    workbench.mail_action({"request_id": "explicit_unread_456", "ids": [key], "action": "unread"})
    assert _row(workbench, key)["unread"] is True
    reader.release["read"].set()
    assert _until(lambda: workbench.mail_store.receipt("explicit_unread_456")["status"] == "complete")
    assert _until(lambda: not workbench.snapshot()["outbox"])
    workbench._refresh()
    actual = (reader.writes, "UNREAD" in reader.records[0]["label_ids"], _row(workbench, key)["unread"])
    assert actual == ([("one", "read"), ("one", "unread")], True, True)
    workbench.close()


def test_open_queued_before_explicit_unread_cannot_write_after_it(tmp_path):
    workbench, readers, first, second = _workbench(tmp_path)
    release_automatic = threading.Event()
    second_write = threading.Event()

    class OrderedWriter(Writable):
        def mutate(self, provider_id, action):
            release_automatic.set()
            super().mutate(provider_id, action)
            if len(self.writes) == 2:
                second_write.set()

    reader = OrderedWriter([_unread("one")])
    readers[first], readers[second] = reader, FakeReader([])
    workbench._refresh()
    workbench.refresh = lambda **kwargs: None
    key = message_key(first, "one")
    account_lock = workbench.reader_locks[first]

    class AutomaticReadGate:
        def __enter__(self):
            if threading.current_thread().name == "workbench-read":
                assert release_automatic.wait(5), "automatic read was never released"
            account_lock.acquire()
            return self

        def __exit__(self, *exc):
            account_lock.release()

    workbench.reader_locks[first] = AutomaticReadGate()
    try:
        assert workbench.mark_read(key).items() >= {"ok": True, "changed": True}.items()
        workbench.mail_action({"request_id": "explicit_unread_123", "ids": [key], "action": "unread"})
        assert second_write.wait(5), "both provider writes did not finish"
        assert _until(lambda: workbench.mail_store.receipt("explicit_unread_123")["status"] == "complete")
        workbench._refresh()
        actual = (reader.writes, "UNREAD" in reader.records[0]["label_ids"], _row(workbench, key)["unread"])
        assert actual == ([("one", "read"), ("one", "unread")], True, True)
    finally:
        release_automatic.set()
        workbench.close()


def test_repeated_opens_share_one_queued_write_and_one_optimistic_intent(tmp_path):
    workbench, readers, first, second = _workbench(tmp_path)
    reader = Writable([_unread("one")])
    readers[first], readers[second] = reader, FakeReader([])
    workbench._refresh()
    workbench.refresh = lambda **kwargs: None
    key = message_key(first, "one")
    reader.sequence("read")
    try:
        assert workbench.mark_read(key).items() >= {"ok": True, "changed": True}.items()
        assert reader.started["read"].wait(5)
        for _ in range(3):
            assert workbench.mark_read(key) == {"ok": True, "changed": False}
        assert reader.writes == [] and len(workbench.snapshot()["outbox"]) == 1
        assert _row(workbench, key)["unread"] is False
        assert workbench.detail(key)["unread"] is False

        reader.release["read"].set()
        assert _until(lambda: not workbench.snapshot()["outbox"])
        workbench._refresh()
        assert reader.attempts == reader.writes == [("one", "read")]
        assert "UNREAD" not in reader.records[0]["label_ids"] and _row(workbench, key)["unread"] is False
        assert workbench.mark_read(key) == {"ok": True, "changed": False}
        assert reader.attempts == [("one", "read")]
    finally:
        reader.release["read"].set()
        workbench.close()


def test_reopen_after_explicit_unread_is_the_latest_queued_intent(tmp_path):
    workbench, readers, first, second = _workbench(tmp_path)
    reader = Writable([_unread("one")])
    readers[first], readers[second] = reader, FakeReader([])
    workbench._refresh()
    workbench.refresh = lambda **kwargs: None
    key = message_key(first, "one")
    reader.sequence("read", "unread")
    try:
        assert workbench.mark_read(key).items() >= {"ok": True, "changed": True}.items()
        assert reader.started["read"].wait(5)
        workbench.mail_action({"request_id": "explicit_unread_012", "ids": [key], "action": "unread"})
        assert workbench.mark_read(key).items() >= {"ok": True, "changed": True}.items()
        assert workbench.mark_read(key) == {"ok": True, "changed": False}
        assert _row(workbench, key)["unread"] is False

        reader.release["read"].set()
        assert reader.started["unread"].wait(5)
        assert _row(workbench, key)["unread"] is False
        reader.release["unread"].set()
        assert _until(lambda: workbench.mail_store.receipt("explicit_unread_012")["status"] == "complete")
        assert _until(lambda: not workbench.snapshot()["outbox"])
        workbench._refresh()
        actual = (reader.attempts, "UNREAD" in reader.records[0]["label_ids"], _row(workbench, key)["unread"])
        assert actual == ([("one", "read"), ("one", "unread"), ("one", "read")], False, False)
    finally:
        for gate in reader.release.values():
            gate.set()
        workbench.close()


@pytest.mark.parametrize("open_fails", [False, True])
@pytest.mark.parametrize("unread_fails", [False, True])
def test_open_and_explicit_unread_keep_latest_intent_through_failures(tmp_path, open_fails, unread_fails):
    workbench, readers, first, second = _workbench(tmp_path)
    failures = ({"read"} if open_fails else set()) | ({"unread"} if unread_fails else set())
    reader = Writable([_unread("one")], fail_actions=failures)
    readers[first], readers[second] = reader, FakeReader([])
    workbench._refresh()
    workbench.refresh = lambda **kwargs: None
    key = message_key(first, "one")
    reader.sequence("read", "unread")
    try:
        assert workbench.mark_read(key).items() >= {"ok": True, "changed": True}.items()
        assert reader.started["read"].wait(5)
        workbench.mail_action({"request_id": "explicit_unread_789", "ids": [key], "action": "unread"})
        assert _row(workbench, key)["unread"] is True

        reader.release["read"].set()
        assert reader.started["unread"].wait(5)
        assert _row(workbench, key)["unread"] is True
        reader.release["unread"].set()
        expected_status = "failed" if unread_fails else "complete"
        assert _until(
            lambda: workbench.mail_store.receipt("explicit_unread_789")["status"] == expected_status
        )
        assert _until(lambda: not workbench.snapshot()["outbox"])
        workbench._refresh()

        expected_writes = ([] if open_fails else [("one", "read")]) + (
            [] if unread_fails else [("one", "unread")]
        )
        expected_unread = open_fails or not unread_fails
        actual = (
            reader.attempts,
            reader.writes,
            "UNREAD" in reader.records[0]["label_ids"],
            _row(workbench, key)["unread"],
        )
        assert actual == (
            [("one", "read"), ("one", "unread")],
            expected_writes,
            expected_unread,
            expected_unread,
        )
    finally:
        for gate in reader.release.values():
            gate.set()
        workbench.close()


def test_failed_open_restores_unread_immediately_after_stale_publication(tmp_path):
    workbench, readers, first, second = _workbench(tmp_path)
    reader = Writable([_unread("one")], fail_actions={"read"})
    readers[first], readers[second] = reader, FakeReader([])
    workbench._refresh()
    workbench.refresh = lambda **kwargs: None
    key = message_key(first, "one")
    reader.sequence("read")
    observer = RefreshObserver(workbench, first)
    syncing = threading.Thread(target=workbench._refresh, name="stale-refresh")
    try:
        syncing.start()
        assert observer.publication_waiting.wait(5)  # UNREAD is captured; publication is separate
        assert workbench.mark_read(key).items() >= {"ok": True, "changed": True}.items()
        assert reader.started["read"].wait(5)
        observer.release_publication.set()
        assert observer.published.wait(5)
        assert _row(workbench, key)["unread"] is False

        reader.release["read"].set()
        assert observer.outbox_empty.wait(5)
        syncing.join(5)
        assert not syncing.is_alive()
        actual = ("UNREAD" in reader.records[0]["label_ids"], _row(workbench, key)["unread"])
        assert actual == (True, True)
    finally:
        observer.release_publication.set()
        reader.release["read"].set()
        syncing.join(5)
        observer.restore()
        workbench.close()


def test_epoch_rejects_a_pre_write_list_after_successful_open(tmp_path):
    workbench, readers, first, second = _workbench(tmp_path)
    reader = Writable([_unread("one")])
    readers[first], readers[second] = reader, FakeReader([])
    workbench._refresh()
    workbench.refresh = lambda **kwargs: None
    key = message_key(first, "one")
    observer = RefreshObserver(workbench, first)
    syncing = threading.Thread(target=workbench._refresh, name="stale-refresh")
    try:
        syncing.start()
        assert observer.publication_waiting.wait(5)  # the provider write can finish independently now
        assert workbench.mark_read(key).items() >= {"ok": True, "changed": True}.items()
        assert observer.outbox_empty.wait(5)
        assert "UNREAD" not in reader.records[0]["label_ids"]

        observer.release_publication.set()
        syncing.join(5)
        assert not syncing.is_alive() and not observer.published.is_set()
        actual = ("UNREAD" in reader.records[0]["label_ids"], _row(workbench, key)["unread"])
        assert actual == (False, False)
    finally:
        observer.release_publication.set()
        syncing.join(5)
        observer.restore()
        workbench.close()


def test_external_read_state_changes_are_accepted_after_the_outbox_finishes(tmp_path):
    workbench, readers, first, second = _workbench(tmp_path)
    reader = Writable([_unread("one")])
    readers[first], readers[second] = reader, FakeReader([])
    workbench._refresh()
    workbench.refresh = lambda **kwargs: None
    key = message_key(first, "one")
    observer = RefreshObserver(workbench, first, gate_publication=False)
    try:
        assert workbench.mark_read(key).items() >= {"ok": True, "changed": True}.items()
        assert observer.outbox_empty.wait(5)
        assert _row(workbench, key)["unread"] is False

        reader.records[0]["label_ids"] = ["UNREAD"]
        workbench._refresh()
        assert _row(workbench, key)["unread"] is True
        reader.records[0]["label_ids"] = []
        workbench._refresh()
        assert _row(workbench, key)["unread"] is False
    finally:
        observer.restore()
        workbench.close()
