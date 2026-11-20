"""On-disk list cache: instant restart, list fields only, Gmail resumes from its history cursor."""

import json
import os
import stat
import threading

from email_town_labeler.config import Config, Paths
from email_town_labeler.desktop import providers as p
from email_town_labeler.desktop.cache import ListCache
from email_town_labeler.desktop.manager import Workbench
from email_town_labeler.desktop.state import message_key
from tests.fakes.fake_client import FakeGmailClient
from tests.test_desktop_manager import BatchReader, CountingModel, _record


def _root(tmp_path):
    return Paths.default(tmp_path / "config").ensure(), tmp_path / "workbench"


def _first_run(tmp_path):
    paths, root = _root(tmp_path)
    readers = {}
    wb = Workbench(root, paths, reader_factory=lambda a: readers[a["id"]], classifier=CountingModel())
    aid = wb.accounts.add("One", "imap", {"host": "one.example", "username": "u", "port": 993})["id"]
    readers[aid] = BatchReader([_record("labelled"), _record("guessed", category=None)])
    wb._refresh()
    wb.close()
    return paths, root, aid


def test_restart_shows_the_last_list_before_any_provider_read_and_keeps_model_categories(tmp_path):
    paths, root, aid = _first_run(tmp_path)
    gate, listing = threading.Event(), threading.Event()

    class Slow(BatchReader):
        def list(self, limit, folder="inbox"):
            listing.set()
            gate.wait(5)
            return super().list(limit, folder)

    reader = Slow([_record("labelled"), _record("guessed", category=None)])
    model = CountingModel()
    wb = Workbench(root, paths, reader_factory=lambda a: reader, classifier=model)
    wb.start()
    assert listing.wait(2)  # the provider read is in flight and blocked...
    rows = {r["id"]: r for r in wb.snapshot()["messages"]}  # ...yet the list is already on screen
    guessed = rows[message_key(aid, "guessed")]
    assert (guessed["category"], guessed["source"]) == ("orders", "model")
    assert rows[message_key(aid, "labelled")]["subject"] == "Fixture subject"
    assert "provider_id" not in guessed
    gate.set()
    for _ in range(200):
        if wb.snapshot()["accounts"][0]["status"] == "ready":
            break
        threading.Event().wait(0.01)
    assert model.calls == 0  # a restored model category is not computed again
    wb.close()


def test_cache_file_holds_list_fields_only_and_is_private(tmp_path):
    _paths, root, _aid = _first_run(tmp_path)
    path = root / "cache.sqlite"
    raw = path.read_bytes()
    assert b"Fixture subject" in raw and b"fixture@example.test" in raw
    assert b"body " not in raw and b"private body" not in raw  # read-ahead bodies stay in RAM
    if os.name == "posix":  # Windows has no POSIX modes; the data folder is in the user's profile
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    cache = ListCache(path)
    cache.save_rows("a", "inbox", [{"id": "k", "subject": "s", "body": "x", "snippet": "y", "headers": {}}])
    assert cache.load_rows({"a"})[("a", "inbox")][1] == [{"id": "k", "subject": "s"}]
    cache.save_reader("gone", {"x": 1})
    assert cache.load_rows(set()) == {} and cache.load_reader("gone") is None  # removed accounts are purged


def test_preview_of_a_cached_row_waits_for_the_first_list_instead_of_failing(tmp_path):
    paths, root, aid = _first_run(tmp_path)
    gate = threading.Event()

    class Quick(BatchReader):
        read_ahead = 0  # the preview itself must reach the provider

    def connect(account):  # like Gmail: refreshing the token and reading the profile takes a moment
        gate.wait(5)
        return Quick([_record("labelled")])

    wb = Workbench(root, paths, reader_factory=connect, classifier=CountingModel())
    wb.start()
    threading.Timer(0.3, gate.set).start()
    assert wb.detail(message_key(aid, "labelled"))["body"] == "private body"
    wb.close()


def test_reader_state_is_saved_after_a_sync_and_handed_to_the_next_process(tmp_path):
    paths, root = _root(tmp_path)
    restored = []

    class Resumable(BatchReader):
        def export_state(self):
            return {"cursor": 7}

        def restore_state(self, state):
            restored.append(state)

    wb = Workbench(
        root, paths, reader_factory=lambda a: Resumable([_record("one")]), classifier=CountingModel()
    )
    wb.accounts.add("One", "imap", {"host": "one.example", "username": "u", "port": 993})
    wb._refresh()
    wb.close()
    assert restored == [None]  # first process: nothing saved yet
    again = Workbench(
        root, paths, reader_factory=lambda a: Resumable([_record("one")]), classifier=CountingModel()
    )
    again._refresh()
    assert restored == [None, {"cursor": 7}]
    again.close()


def _gmail_reader(client):
    reader = p.GmailReader.__new__(p.GmailReader)
    reader.client, reader.cfg = client, Config()
    reader.address, reader.identity = "fixture@example.test", "identity"
    reader.cache, reader.history_id, reader.folder_ids = {}, None, {}
    return reader


def test_gmail_resumes_from_saved_metadata_and_history_cursor():
    client = FakeGmailClient()
    ids = [client.add_message() for _ in range(3)]
    first = _gmail_reader(client)
    first.list(10)
    state = json.loads(json.dumps(first.export_state()))  # as it round-trips through the cache file
    assert not {"snippet", "body_text", "body_html"} & {k for rec in state["cache"].values() for k in rec}
    client.calls.clear()
    client.labels["star"] = {"id": "star", "name": "Starred"}
    client.user_add_label(ids[0], "star")
    resumed = _gmail_reader(client)
    resumed.restore_state(state)
    rows, _ = resumed.list(10)
    reads = [c[1][0] for c in client.calls if c[0] == "get_metadata"]
    assert reads == [(ids[0],)]  # only the message history says changed; 2 of 3 rows cost nothing
    assert "star" in next(r for r in rows if r["id"] == ids[0])["label_ids"]
    other = _gmail_reader(client)
    other.identity = "another-mailbox"
    other.restore_state(state)
    assert other.history_id is None and other.cache == {}  # never resume another mailbox's state


def _wait_ready(wb):
    for _ in range(300):
        if wb.snapshot()["accounts"][0]["status"] == "ready":
            return
        threading.Event().wait(0.01)


def _restart(root, paths):
    model = CountingModel()
    reader = BatchReader([_record("labelled"), _record("guessed", category=None)])
    wb = Workbench(root, paths, reader_factory=lambda a: reader, classifier=model)
    wb.start()
    _wait_ready(wb)
    wb.close()
    return model


def test_a_new_policy_or_threshold_classifies_cached_model_categories_again(tmp_path):
    paths, root = _root(tmp_path)
    paths.config_file.write_text('[model]\nref = "rules"\n')
    readers = {}
    wb = Workbench(root, paths, reader_factory=lambda a: readers[a["id"]], classifier=CountingModel())
    aid = wb.accounts.add("One", "imap", {"host": "one.example", "username": "u", "port": 993})["id"]
    readers[aid] = BatchReader([_record("labelled"), _record("guessed", category=None)])
    wb._refresh()
    wb.close()
    assert _restart(root, paths).calls == 0  # same model and policy: the cached category stands
    # A changed threshold changes what the policy returns: the cached decision is redone, once.
    paths.config_file.write_text('[model]\nref = "rules"\n[policy]\nbulk_promotional_threshold = 0.4\n')
    assert _restart(root, paths).calls == 1
    assert _restart(root, paths).calls == 0


def test_categories_cached_before_classifier_stamps_are_classified_again(tmp_path):
    paths, root, aid = _first_run(tmp_path)
    cache = ListCache(root / "cache.sqlite")
    (_saved, rows) = cache.load_rows({aid})[(aid, "inbox")]
    assert any(r.get("classified_by") for r in rows)
    cache.save_rows(aid, "inbox", [{k: v for k, v in r.items() if k != "classified_by"} for r in rows])
    assert _restart(root, paths).calls == 1  # a decision from an earlier version is not carried forward


def test_cached_categories_without_a_learning_record_are_classified_once_more(tmp_path):
    # Mail listed before learning.sqlite existed would otherwise never be recorded, so correcting it taught nothing.
    paths, root, _aid = _first_run(tmp_path)
    (root / "learning.sqlite").unlink()
    assert _restart(root, paths).calls == 1
    assert _restart(root, paths).calls == 0  # recorded now: the cached category stands again


def test_a_category_being_redone_stays_on_screen_until_the_model_answers(tmp_path):
    # After a policy change the list must not turn into People (and lose its bundles) while the model works.
    paths, root, aid = _first_run(tmp_path)
    paths.config_file.write_text('[model]\nref = "rules"\n[policy]\nbulk_promotional_threshold = 0.4\n')
    release, asked = threading.Event(), threading.Event()

    class HeldModel(CountingModel):
        def predict(self, records):
            asked.set()
            release.wait(10)
            return super().predict(records)

    reader = BatchReader([_record("labelled"), _record("guessed", category=None)])
    wb = Workbench(root, paths, reader_factory=lambda a: reader, classifier=HeldModel())
    wb._restore_cache()  # what start() shows first: the cached list, its categories from the old policy
    worker = threading.Thread(target=wb._refresh)
    worker.start()
    assert asked.wait(10)  # the list is in and the model is working on the stale category
    rows = {r["id"]: r for r in wb.snapshot()["messages"]}
    guessed = rows[message_key(aid, "guessed")]
    assert (guessed["category"], guessed["source"]) == ("orders", "model")
    release.set()
    worker.join(10)
    assert wb.snapshot()["messages"] and wb.model.calls == 1  # and it was still classified again
    wb.close()
