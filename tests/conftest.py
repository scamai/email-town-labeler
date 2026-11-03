from __future__ import annotations

import pytest

from email_town_labeler import update_check


@pytest.fixture(autouse=True)
def _no_update_check(monkeypatch):
    """No test reaches GitHub: the update check is off (as with EMAIL_TOWN_LABELER_NO_UPDATE_CHECK=1),
    and building its real opener fails the test. tests/test_update_check.py turns it back on with
    fakes, test by test."""
    monkeypatch.setenv(update_check.DISABLE_ENV, "1")
    calls: list[str] = []

    def no_network():
        calls.append("make_opener")
        raise AssertionError("the update check tried to reach the network")

    monkeypatch.setattr(update_check, "make_opener", no_network)
    yield
    assert not calls, "a test reached the update check's network code; give it a fake opener"
