"""Opening a message marks it read, as in Gmail: shown at once, written in the background, never fought by a stale list."""

import threading
import time

from email_town_labeler.desktop.state import message_key
from tests.test_desktop_manager import FakeReader, _record, _workbench


class Writable(FakeReader):
    def __init__(self, records, *, fail=False):
        super().__init__(records)
        self.writes, self.fail_writes, self.gate = [], fail, threading.Event()
        self.gate.set()

    def capabilities(self):
        return {"write": True, "send": False, "attachments": False, "max_attachment_bytes": 0, "reason": ""}

    def mutate(self, provider_id, action):
        self.gate.wait(5)
        if self.fail_writes:
            raise RuntimeError("synthetic write failure")
        self.writes.append((provider_id, action))
        record = next(r for r in self.records if r["provider_id"] == provider_id)
        labels = [x for x in record["label_ids"] if x != "UNREAD"]
        record["label_ids"] = labels + (["UNREAD"] if action == "unread" else [])


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
    key = message_key(first, "one")
    release.clear()
    syncing = threading.Thread(target=workbench._refresh)
    syncing.start()
    assert listed.wait(5)  # a sync has read UNREAD and holds the account
    assert workbench.mark_read(key) == {"ok": True, "changed": True}
    assert _row(workbench, key)["unread"] is False  # shown read before the mailbox confirms
    release.set()  # the stale list now publishes UNREAD; the write waits for the account
    syncing.join(5)
    assert _row(workbench, key)["unread"] is False
    assert _until(lambda: reader.writes == [("one", "read")])
    workbench._refresh()  # the mailbox now agrees: the override is gone
    assert key not in workbench.read_overrides and _row(workbench, key)["unread"] is False
    assert workbench.mark_read(key) == {"ok": True, "changed": False}  # already read: no second write
    workbench.close()


def test_a_failed_write_puts_the_row_back(tmp_path):
    workbench, readers, first, second = _workbench(tmp_path)
    readers[first], readers[second] = Writable([_unread("one")], fail=True), FakeReader([])
    workbench._refresh()
    key = message_key(first, "one")
    workbench.mark_read(key)
    assert _until(lambda: _row(workbench, key)["unread"] is True)
    assert key not in workbench.read_overrides
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
    key = message_key(first, "one")
    reader.gate.clear()
    workbench.mark_read(key)  # still in flight when the user marks it unread again
    reader.gate.set()
    assert _until(lambda: reader.writes == [("one", "read")])
    workbench._mutate_messages([dict(workbench.messages[key])], "unread", "req")
    workbench._refresh()
    assert _row(workbench, key)["unread"] is True and key not in workbench.read_overrides
    workbench.close()
