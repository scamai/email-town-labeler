from pathlib import Path

import pytest

from email_town_labeler.config import Paths
from email_town_labeler.desktop.accounts import Accounts
from email_town_labeler.desktop.state import LocalState, message_key


def test_message_keys_are_account_scoped():
    assert message_key("a", "same") != message_key("b", "same")
    assert message_key("a", "same") == message_key("a", "same")


def test_state_persists_only_workflow_fields_and_undo_is_atomic(tmp_path: Path):
    state = LocalState(tmp_path / "workflow.sqlite")
    first = message_key("a", "1")
    second = message_key("b", "1")
    before = state.edit([first, second], "done")
    assert all(set(row) == {"id", "done", "category", "revision"} for row in before)
    assert state.read([first, second])[first]["done"] == 1

    state.edit([first], "category", "orders")
    with pytest.raises(ValueError, match="newer changes"):
        state.undo(before)
    rows = state.read([first, second])
    assert rows[first]["category"] == "orders"
    assert rows[second]["done"] == 1
    assert "body" not in (tmp_path / "workflow.sqlite").read_bytes().decode("utf-8", "ignore")


def test_duplicate_edit_is_one_revision_and_state_survives_reopen(tmp_path: Path):
    path = tmp_path / "workflow.sqlite"
    key = message_key("a", "duplicate")
    state = LocalState(path)
    state.edit([key, key], "done")
    assert state.read([key])[key]["revision"] == 1
    reopened = LocalState(path)
    assert reopened.read([key])[key]["done"] == 1


def test_accounts_registry_does_not_store_password_and_rejects_duplicates(tmp_path: Path):
    paths = Paths.default(tmp_path / "default").ensure()
    accounts = Accounts(tmp_path / "workbench", paths)
    row = accounts.add("IMAP", "imap", {"host": "imap.example.com", "username": "u", "port": 993})
    assert row["enabled"] is True
    with pytest.raises(ValueError, match="already connected"):
        accounts.add("Another", "imap", {"host": "imap.example.com", "username": "u", "port": 993})
    raw = (tmp_path / "workbench" / "accounts.json").read_text()
    assert "password" not in raw
    assert "secret" not in raw


def test_accounts_bind_identity_rejects_duplicate_authorization(tmp_path: Path):
    paths = Paths.default(tmp_path / "default").ensure()
    accounts = Accounts(tmp_path / "workbench", paths)
    first = accounts.add("One", "gmail", {"config_dir": str(tmp_path / "one")})
    second = accounts.add("Two", "gmail", {"config_dir": str(tmp_path / "two")})
    accounts.bind_identity(first["id"], "same-mailbox")
    with pytest.raises(ValueError, match="another account"):
        accounts.bind_identity(second["id"], "same-mailbox")


# ─── Inbox bundles: a per-device preference, not a message field ───


def test_bundles_default_to_every_bundleable_category_and_persist(tmp_path: Path):
    from email_town_labeler.desktop.state import BUNDLEABLE

    state = LocalState(tmp_path / "workflow.sqlite")
    assert state.prefs()["bundled"] == list(BUNDLEABLE)
    assert state.set_bundled("travel", False)["bundled"] == [c for c in BUNDLEABLE if c != "travel"]
    reopened = LocalState(tmp_path / "workflow.sqlite")
    assert "travel" not in reopened.prefs()["bundled"]
    assert reopened.set_bundled("travel", True)["bundled"] == list(BUNDLEABLE)  # back in its usual place


def test_mail_that_may_need_you_never_bundles(tmp_path: Path):
    state = LocalState(tmp_path / "workflow.sqlite")
    for category in ("action_needed", "new_sender", "unknown", "made_up"):
        with pytest.raises(ValueError, match="always stays in the Inbox"):
            state.set_bundled(category, True)
    assert "action_needed" not in state.prefs()["bundled"]


def test_bundle_preference_ignores_unknown_saved_values(tmp_path: Path):
    state = LocalState(tmp_path / "workflow.sqlite")
    with state._connect() as db:
        db.execute("INSERT INTO prefs(key, value) VALUES ('bundled', '[\"action_needed\", \"orders\"]')")
    assert state.prefs()["bundled"] == ["orders"]  # a hand-edited or older file cannot bundle Action needed


def test_a_damaged_bundle_preference_falls_back_to_the_default(tmp_path: Path):
    from email_town_labeler.desktop.state import BUNDLEABLE

    state = LocalState(tmp_path / "workflow.sqlite")
    with state._connect() as db:
        db.execute("INSERT INTO prefs(key, value) VALUES ('bundled', 'not json')")
    assert state.prefs()["bundled"] == list(BUNDLEABLE)


def test_pane_widths_persist_merge_and_reject_out_of_range_values(tmp_path: Path):
    state = LocalState(tmp_path / "workflow.sqlite")
    assert state.prefs()["panes"] == {}  # nothing saved: the page uses its defaults
    assert state.set_panes({"list": 640})["panes"] == {"list": 640}
    assert state.set_panes({"sidebar": 300})["panes"] == {"list": 640, "sidebar": 300}  # one border at a time
    assert LocalState(tmp_path / "workflow.sqlite").prefs()["panes"] == {"list": 640, "sidebar": 300}
    for bad in ({"list": 100}, {"sidebar": 9000}, {"list": "640"}, {"list": 640.5}, {"list": True}, []):
        with pytest.raises(ValueError):
            state.set_panes(bad)
    assert state.prefs()["panes"] == {"list": 640, "sidebar": 300}


def test_a_hand_edited_pane_width_outside_the_limits_is_ignored(tmp_path: Path):
    state = LocalState(tmp_path / "workflow.sqlite")
    with state._connect() as db:
        db.execute("""INSERT INTO prefs(key, value) VALUES ('panes', '{"list": 5, "sidebar": 250}')""")
    assert state.prefs()["panes"] == {"sidebar": 250}


# ─── Sender rules: decided once per sender, stored without the address ───


def test_sender_keys_are_keyed_per_install_and_ignore_case(tmp_path: Path):
    one = LocalState(tmp_path / "one.sqlite")
    other = LocalState(tmp_path / "other.sqlite")
    key = one.sender_key("News@Example.test")
    assert (
        key
        == one.sender_key("news@example.test ")
        == LocalState(tmp_path / "one.sqlite").sender_key("news@example.test")
    )
    assert key != other.sender_key("news@example.test")  # a different install's key: no shared dictionary
    assert "news@example.test" not in key
    assert "sender_secret" not in one.prefs()  # the key never goes to the page


def test_sender_rules_set_clear_and_restore(tmp_path: Path):
    state = LocalState(tmp_path / "workflow.sqlite")
    a, b = state.sender_key("a@example.test"), state.sender_key("b@example.test")
    assert state.set_sender_rules([a], "newsletters") == [(a, None)]
    previous = state.set_sender_rules([a, b], "orders")
    assert previous == [(a, "newsletters"), (b, None)]
    assert state.sender_rules() == {a: "orders", b: "orders"}
    state.restore_sender_rules(previous)
    assert state.sender_rules() == {a: "newsletters"}
    state.set_sender_rules([a], None)
    assert state.sender_rules() == {}
    for not_a_decision in ("new_sender", "unknown", "made_up"):
        with pytest.raises(ValueError, match="Choose a category"):
            state.set_sender_rules([a], not_a_decision)
