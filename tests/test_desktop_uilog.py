"""desktop.log records what the user did and why mail flags changed, without mail content."""

import json
import logging
import threading

import pytest

from email_town_labeler.desktop.server import LocalServer, ui_line
from tests.test_desktop_manager import _record, _workbench
from tests.test_desktop_read_state import Writable
from tests.test_desktop_server import request


def test_ui_events_become_one_line_of_short_tokens():
    line = ui_line({"type": "open", "id": "3c464dbe", "via": "shift+j", "unread": True, "selected": 2})
    assert line == "open id=3c464dbe via=shift+j unread=True selected=2"
    assert ui_line({"type": "key", "key": "#"}) == "key key=#"
    assert ui_line({"type": "state-flags", "changes": ["a0995d24:unread:open", "3c464dbe:read"]}) == (
        "state-flags changes=a0995d24:unread:open,3c464dbe:read"
    )


@pytest.mark.parametrize(
    "event",
    [
        {"type": "open", "subject": "Quarterly results for Alice"},
        {"type": "open", "sender": "alice@example.com"},
        {"type": "open", "id": "x" * 41},
        {"type": "open", "id": ["ok", "has space"]},
        {"type": "open", "body": {"nested": "secret"}},
        {"type": "open", "note": "line\nbreak"},
    ],
)
def test_anything_but_short_tokens_is_masked(event):
    line = ui_line(event)
    assert line.endswith("=?")
    for secret in ("Alice", "alice@example.com", "secret", "space", "break", "x" * 41):
        assert secret not in line


@pytest.mark.parametrize("event", [{"type": "has space"}, {"type": "a@b"}, {}, "open", None, {"type": ""}])
def test_events_without_a_valid_name_are_dropped(event):
    assert ui_line(event) == ""


def test_too_many_fields_are_cut():
    event = {"type": "x", **{f"f{i}": i for i in range(30)}}
    assert ui_line(event).count("=") == 11


@pytest.fixture
def served(tmp_path):
    workbench, readers, first, _ = _workbench(tmp_path)
    readers[first] = Writable([{**_record("a"), "label_ids": ["UNREAD"]}, _record("b")])
    workbench._refresh()
    server = LocalServer(workbench)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server, workbench
    finally:
        server.shutdown()
        server.server_close()
        workbench.close()


def test_ui_log_route_needs_the_session_and_writes_lines(served, caplog):
    server, _workbench = served
    body = {"events": [{"type": "open", "id": "3c464dbe"}, {"type": "open", "subject": "Hi Bob"}]}
    assert request(server, "/api/ui-log", method="POST", data=body)[0] == 401
    with caplog.at_level(logging.INFO, logger="email_town_labeler.desktop"):
        status, _headers, raw = request(server, "/api/ui-log", method="POST", data=body, auth=True)
    assert status == 200 and json.loads(raw) == {"ok": True}
    lines = [r.getMessage() for r in caplog.records if r.name == "email_town_labeler.desktop.ui"]
    assert lines == ["open id=3c464dbe", "open subject=?"]
    assert "ui-log" not in caplog.text  # the per-request timing line is not written for it


def test_mark_read_logs_its_decision_by_key_prefix(served, caplog):
    _server, workbench = served
    rows = workbench.snapshot()["messages"]
    unread = next(r for r in rows if r["unread"])
    with caplog.at_level(logging.INFO, logger="email_town_labeler.desktop"):
        workbench.mark_read(unread["id"], True)
        workbench.mark_read(unread["id"], True)
    assert f"mark-read {unread['id'][:8]}: queued" in caplog.text
    assert f"mark-read {unread['id'][:8]}: already read" in caplog.text
    assert f"mail action read queued: 1 message(s) {unread['id'][:8]}" in caplog.text
    assert unread["subject"] not in caplog.text


def test_list_refresh_logs_flags_the_mailbox_changed(served, caplog):
    _server, workbench = served
    key = workbench.snapshot()["messages"][0]["id"]
    before = workbench.messages[key]["unread"]
    workbench.messages[key]["unread"] = not before  # the list showed the other state
    with caplog.at_level(logging.INFO, logger="email_town_labeler.desktop"):
        workbench._refresh()
    state = "unread" if before else "read"
    assert f"mailbox changed 1 flag(s): {key[:8]} {state}" in caplog.text


def test_done_reopen_category_and_undo_are_logged_as_local_edits(served, caplog):
    _server, workbench = served
    row = workbench.snapshot()["messages"][0]
    key = row["id"]
    with caplog.at_level(logging.INFO, logger="email_town_labeler.desktop"):
        token = workbench.action({"ids": [key], "action": "done"})["undo"]
        workbench.undo(token)
        workbench.action({"ids": [key], "action": "category", "category": "promotional"})
    assert f"workflow done: 1 message(s) {key[:8]} (local, mailbox unchanged)" in caplog.text
    assert f"workflow undo: 1 message(s) {key[:8]} restored" in caplog.text
    assert f"workflow category promotional: 1 message(s) {key[:8]}" in caplog.text
    assert row["subject"] not in caplog.text
    assert ui_line({"type": "workflow-action", "action": "done", "via": "preview", "ids": [key[:8]]}) == (
        f"workflow-action action=done via=preview ids={key[:8]}"
    )
