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
