"""LabelMap against FakeGmail: creation, caching, case-insensitive reuse, 409 races."""

import pytest
from googleapiclient.errors import HttpError

from email_town_labeler.gmail.client import GmailClient
from email_town_labeler.gmail.labels import LabelMap
from email_town_labeler.taxonomy import DISPLAY_NAMES
from tests.fakes.fake_service import FakeGmail


def make(g=None, prefix="email-town-labeler"):
    g = g or FakeGmail()
    return g, LabelMap(GmailClient(g, sleep=lambda s: None, max_gets_per_minute=None), prefix, DISPLAY_NAMES)


def user_labels(g):
    return sorted(lb["name"] for lb in g._labels.values() if lb["type"] == "user")


def test_creates_parent_then_child_and_caches():
    g, lm = make()
    lid = lm.ensure("action_needed")
    assert user_labels(g) == ["email-town-labeler", "email-town-labeler/action needed"]
    assert g.calls == ["labels.list", "labels.create", "labels.create"]
    assert lm.ensure("action_needed") == lid
    lm.ensure("orders")
    assert g.calls.count("labels.create") == 3 and g.calls.count("labels.list") == 1
    assert lm.class_of(lid) == "action_needed"


def test_reuses_existing_labels_case_insensitively():
    g = FakeGmail()
    parent = g.add_label("Email-Town-Labeler")
    child = g.add_label("EMAIL-TOWN-LABELER/Orders")
    g, lm = make(g)
    assert lm.ensure("orders") == child
    assert "labels.create" not in g.calls
    assert lm.class_of(child) == "orders"
    assert lm.our_label_ids() == {parent, child}


def test_409_race_relists_and_returns_existing():
    g, lm = make()
    lm.ensure("orders")  # cache loaded, parent exists
    other = g.add_label("email-town-labeler/Travel")  # another client creates it behind our back
    assert lm.ensure("travel") == other
    assert g.calls[-2:] == ["labels.create", "labels.list"]


def test_409_without_a_matching_label_is_raised():
    g, lm = make()
    lm.ensure("orders")
    g.fail_next(1, 409, method="labels.create")
    with pytest.raises(HttpError):
        lm.ensure("travel")


def test_other_errors_propagate():
    g, lm = make()
    g.fail_next(1, 400, method="labels.create")
    with pytest.raises(HttpError):
        lm.ensure("orders")


def test_class_of_and_our_label_ids():
    g = FakeGmail()
    mine = g.add_label("Projects/secret")
    similar = g.add_label("email-town-labelerx/orders")  # different prefix, not ours
    stray = g.add_label("email-town-labeler/old-class")  # ours, but not a configured class
    g, lm = make(g)
    lid = lm.ensure("unknown")
    assert lm.class_of(lid) == "unknown"
    assert lm.class_of(mine) is None and lm.class_of("INBOX") is None and lm.class_of(stray) is None
    ours = lm.our_label_ids()
    assert lid in ours and stray in ours and mine not in ours and similar not in ours
    assert g.label_id("email-town-labeler") in ours


def test_unknown_class_raises():
    _, lm = make()
    with pytest.raises(KeyError):
        lm.ensure("bogus")


def test_custom_prefix_and_names():
    g = FakeGmail()
    lm = LabelMap(GmailClient(g, sleep=lambda s: None), "AI/", {"orders": "Receipts"})
    lm.ensure("orders")
    assert user_labels(g) == ["AI", "AI/Receipts"]


def test_refresh_sees_labels_created_elsewhere():
    g, lm = make()
    assert lm.our_label_ids() == set()
    lid = g.add_label("email-town-labeler/orders")
    lm.refresh()
    assert lm.class_of(lid) == "orders"
