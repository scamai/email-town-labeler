"""LabelMap against FakeGmail: creation, caching, case-insensitive reuse, 409 races."""

import pytest
from googleapiclient.errors import HttpError

from email_town_labeler.gmail.client import GmailClient
from email_town_labeler.gmail.labels import LabelMap, foreign_labels, prefix_renames
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


# -- colors --------------------------------------------------------------------------------

ORANGE = {"textColor": "#000000", "backgroundColor": "#ffad47"}
PURPLE = {"textColor": "#000000", "backgroundColor": "#b99aff"}


def make_colored(g=None, colors=None, prefix="email-town-labeler"):
    g = g or FakeGmail()
    client = GmailClient(g, sleep=lambda s: None, max_gets_per_minute=None)
    return g, LabelMap(client, prefix, DISPLAY_NAMES, colors=colors)


def color_of(g, name):
    return g._labels[g.label_id(name)].get("color")


def test_new_class_labels_are_created_colored_parent_uncolored():
    g, lm = make_colored(colors={"action_needed": ORANGE})
    lm.ensure("action_needed")
    lm.ensure("orders")  # no color configured for this class
    assert color_of(g, "email-town-labeler/action needed") == ORANGE
    assert color_of(g, "email-town-labeler") is None
    assert color_of(g, "email-town-labeler/orders") is None


def test_sync_colors_patches_missing_and_different_only_under_prefix():
    g = FakeGmail()
    g.add_label("email-town-labeler")
    g.add_label("email-town-labeler/action needed")  # uncolored: patched
    wrong = g.add_label("email-town-labeler/orders")
    g._labels[wrong]["color"] = {"textColor": "#FFFFFF", "backgroundColor": "#000000"}  # differs: patched
    ok = g.add_label("email-town-labeler/travel")
    g._labels[ok]["color"] = {"textColor": "#000000", "backgroundColor": "#A4C2F4"}  # same (case): kept
    town = g.add_label("action needed")  # Town's own label, same class name: never touched
    other = g.add_label("Projects/orders")
    travel = {"textColor": "#000000", "backgroundColor": "#a4c2f4"}
    colors = {"action_needed": ORANGE, "orders": PURPLE, "travel": travel, "updates": ORANGE}
    g, lm = make_colored(g, colors)
    assert lm.sync_colors() == 2
    assert g.calls.count("labels.patch") == 2
    assert color_of(g, "email-town-labeler/action needed") == ORANGE
    assert color_of(g, "email-town-labeler/orders") == PURPLE
    assert color_of(g, "email-town-labeler") is None  # parent never colored
    assert "color" not in g._labels[town] and "color" not in g._labels[other]
    assert g.label_id("email-town-labeler/updates") is None  # sync never creates labels
    assert lm.sync_colors() == 0  # idempotent: the cache knows the new colors


def test_sync_colors_without_colors_makes_no_calls():
    g = FakeGmail()
    g.add_label("email-town-labeler/orders")
    g, lm = make_colored(g, colors={})
    assert lm.sync_colors() == 0 and "labels.patch" not in g.calls


def test_rejected_color_on_create_falls_back_to_uncolored(caplog):
    g, lm = make_colored(colors={"orders": PURPLE})
    g.invalid_colors.add("#b99aff")
    lid = lm.ensure("orders")
    assert g._labels[lid]["name"] == "email-town-labeler/orders" and "color" not in g._labels[lid]
    assert "rejected color" in caplog.text


def test_rejected_color_on_patch_is_logged_and_skipped(caplog):
    g = FakeGmail()
    g.add_label("email-town-labeler/orders")
    g.add_label("email-town-labeler/action needed")
    g.invalid_colors.add("#b99aff")
    g, lm = make_colored(g, {"orders": PURPLE, "action_needed": ORANGE})
    assert lm.sync_colors() == 1
    assert color_of(g, "email-town-labeler/orders") is None
    assert color_of(g, "email-town-labeler/action needed") == ORANGE
    assert "rejected color" in caplog.text


def test_sync_colors_other_errors_propagate():
    g = FakeGmail()
    g.add_label("email-town-labeler/orders")
    g, lm = make_colored(g, {"orders": PURPLE})
    g.fail_next(1, 404, method="labels.patch")
    with pytest.raises(HttpError):
        lm.sync_colors()


# -- the prefix as a whole ------------------------------------------------------------------------


def lab(i, name, type="user"):
    return {"id": i, "name": name, "type": type}


def test_prefix_renames_moves_everything_under_the_prefix_parent_last():
    labels = [
        lab("1", "email-town-labeler"),
        lab("2", "email-town-labeler/orders"),
        lab("3", "email-town-labeler/old-class"),  # ours, but not a configured class: moves too
        lab("4", "Receipts"),
        lab("INBOX", "INBOX", "system"),
    ]
    plan = prefix_renames(labels, "email-town-labeler", "e", DISPLAY_NAMES)
    assert plan[-1] == ("1", "e") and sorted(plan[:-1]) == [("2", "e/orders"), ("3", "e/old-class")]


@pytest.mark.parametrize(
    "existing,ok",
    [
        ("e/orders", True),  # a class label an interrupted rename already moved
        ("e", False),  # someone's own parent label
        ("e/Receipts", False),  # someone's own label under the new prefix
        ("E/Old-Class", False),  # the name a moving label needs (Gmail compares case-insensitively)
    ],
)
def test_prefix_renames_refuses_what_is_in_the_way(existing, ok):
    labels = [lab("1", "email-town-labeler"), lab("3", "email-town-labeler/old-class"), lab("9", existing)]
    if ok:
        assert len(prefix_renames(labels, "email-town-labeler", "e", DISPLAY_NAMES)) == 2
    else:
        with pytest.raises(ValueError, match="already exist"):
            prefix_renames(labels, "email-town-labeler", "e", DISPLAY_NAMES)


def test_prefix_renames_edge_cases():
    labels = [lab("1", "e"), lab("2", "e/orders")]
    assert prefix_renames(labels, "e", "e", DISPLAY_NAMES) == []
    assert prefix_renames(labels, "gone", "x", DISPLAY_NAMES) == []  # nothing to move
    assert prefix_renames(labels, "e", "E", DISPLAY_NAMES) == [("2", "E/orders"), ("1", "E")]
    assert prefix_renames(labels, "e", "ex/e", DISPLAY_NAMES)[-1] == ("1", "ex/e")  # merely similar
    with pytest.raises(ValueError, match="inside"):
        prefix_renames(labels, "e", "e/sub", DISPLAY_NAMES)


def test_foreign_labels():
    labels = [
        lab("1", "e"),
        lab("2", "e/orders"),
        lab("3", "e/Receipts"),
        lab("4", "eat"),
        lab("5", "E/Travel"),
    ]
    assert [lb["id"] for lb in foreign_labels(labels, "e", DISPLAY_NAMES)] == ["3"]


def test_rename_label_keeps_the_id_and_collides_case_insensitively():
    g, lm = make()
    lid = lm.ensure("orders")
    mid = g.deliver(subject="s", body="b")
    g.user_relabel(mid, add=[lid])
    assert lm.client.rename_label(lid, "e/orders")["name"] == "e/orders"
    assert lid in g.labels_of(mid) and g.label_id("e/orders") == lid
    other = g.add_label("e/travel")
    with pytest.raises(HttpError) as ei:
        lm.client.rename_label(other, "E/Orders")
    assert ei.value.resp.status == 409 and g.label_id("e/travel") == other
