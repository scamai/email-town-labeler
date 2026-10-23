from __future__ import annotations

import logging
import threading

import pytest

from email_town_labeler.config import Config
from email_town_labeler.engine import DAY, Engine
from email_town_labeler.state import Store
from email_town_labeler.taxonomy import DISPLAY_NAMES
from tests.fakes.fake_client import FakeClassifier, FakeGmailClient, FakeLabelMap, TransientError


class Clock:
    def __init__(self, t: float = 1_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def _label_maps():
    maps = [FakeLabelMap]
    try:
        from email_town_labeler.gmail.labels import LabelMap

        maps.append(LabelMap)
    except ImportError:  # gmail layer not built yet
        pass
    return maps


@pytest.fixture(params=_label_maps(), ids=lambda c: c.__name__)
def env(tmp_path, request):
    def make(**cfg_kw):
        client = FakeGmailClient()
        cfg = Config(**cfg_kw)
        labels = request.param(client, cfg.label_prefix, cfg.display_names())
        store = Store(tmp_path / f"s{len(list(tmp_path.iterdir()))}.sqlite")
        eng = Engine(client, labels, FakeClassifier(), store, cfg, clock=Clock(client.now_ms / 1000))
        return eng, client, store

    return make


def lid(client, cls):
    return client.label_id(f"email-town-labeler/{DISPLAY_NAMES[cls]}")


def test_bootstrap_leaves_old_mail_alone(env):
    eng, client, store = env()
    old = client.add_message(cls="orders")
    c = eng.sync_once()
    assert c["bootstrapped"] and client.modifies == []
    assert store.get("history_id") == str(client.history_id)
    assert store.get("account_email") == "me@example.test"
    assert store.decision(old) is None
    eng.sync_once()  # history starts after bootstrap: old mail never shows up
    assert client.modifies == [] and store.decision(old) is None


def test_new_mail_labeled_and_idempotent(env):
    eng, client, store = env()
    eng.sync_once()
    a = client.add_message(cls="orders", prob=0.95)
    b = client.add_message(cls="travel", prob=0.8)
    c = eng.sync_once()
    assert c["labeled"] == 2 and c["by_class"] == {"orders": 1, "travel": 1}
    assert lid(client, "orders") in client.messages[a]["label_ids"]
    assert lid(client, "travel") in client.messages[b]["label_ids"]
    d = store.decision(a)
    assert d["cls"] == "orders" and d["applied"] and d["model_id"] == "fake@1"
    # re-sync over the same history (pointer reset) must not relabel or refetch decided mail
    store.set("history_id", "1000")
    n_mod = len(client.modifies)
    c2 = eng.sync_once()
    assert len(client.modifies) == n_mod and c2["already_decided"] == 2 and c2["feedback"] == 0


@pytest.mark.parametrize("action,labeled", [("skip", False), ("label", True)])
def test_low_confidence_unknown_action(env, action, labeled):
    eng, client, store = env(unknown_action=action, min_confidence=0.6)
    eng.sync_once()
    m = client.add_message(cls="orders", prob=0.4)
    eng.sync_once()
    d = store.decision(m)
    assert d["cls"] == "unknown" and d["applied"] is labeled and d["prob"] == pytest.approx(0.4)
    assert (lid(client, "unknown") in client.messages[m]["label_ids"]) is labeled


def test_dry_run_records_never_modifies(env):
    eng, client, store = env(dry_run=True)
    eng.sync_once()
    m = client.add_message(cls="orders")
    c = eng.sync_once()
    assert c["recorded"] == 1 and c["labeled"] == 0
    assert not [x for x in client.calls if x[0] in ("modify", "create_label")]
    assert store.decision(m)["dry_run"] is True
    assert eng.undo() == 0 and not client.modifies
    # turning dry-run off lets backfill label what dry-run only recorded
    eng.cfg.dry_run = False
    assert eng.backfill(days=2)["labeled"] == 1


def test_eligibility_filters(env):
    eng, client, store = env(skip_label_names=["Other Tool/Done"])
    other = client.create_label("Other Tool/Done")["id"]
    eng.sync_once()
    ok = client.add_message(cls="orders")
    sent = client.add_message(cls="orders", labels=["SENT", "INBOX"])
    archived = client.add_message(cls="orders", labels=["UNREAD"])
    spam = client.add_message(cls="orders", labels=["INBOX", "SPAM"])
    skip = client.add_message(cls="orders", labels=["INBOX", other])
    c = eng.sync_once()
    assert c["labeled"] == 1 and c["skipped"] == 4
    assert c["skip_reasons"] == {"excluded": 2, "not_inbox": 1, "skip_label": 1}
    assert store.decision(ok) and not any(store.decision(x) for x in (sent, archived, spam, skip))


def test_already_ours_is_skipped(env):
    eng, client, store = env()
    eng.sync_once()
    ours = eng.labels.ensure("travel")
    m = client.add_message(cls="orders", labels=["INBOX", ours])
    c = eng.sync_once()
    assert c["skip_reasons"] == {"ours": 1} and store.decision(m) is None
    assert store.feedback() == []  # an "added" event is not a user relabel


def test_feedback_cases(env):
    eng, client, store = env(dry_run=False)
    eng.sync_once()
    moved = client.add_message(cls="orders")
    removed = client.add_message(cls="orders")
    kept = client.add_message(cls="orders")
    skipped = client.add_message(cls="orders", prob=0.1)  # unknown + skip -> no label
    eng.sync_once()
    o, t, u = lid(client, "orders"), eng.labels.ensure("travel"), eng.labels.ensure("updates")
    client.user_remove_label(moved, o)
    client.user_add_label(moved, t)
    client.user_remove_label(removed, o)
    client.user_add_label(skipped, u)
    c = eng.sync_once()
    assert c["feedback"] == 3
    fb = {f["msg_id"]: (f["predicted"], f["corrected"]) for f in store.feedback()}
    assert fb == {moved: ("orders", "travel"), removed: ("orders", None), skipped: ("unknown", "updates")}
    assert kept not in fb
    # nothing new -> no duplicate feedback; our own labels in history are not feedback
    assert eng.sync_once()["feedback"] == 0


def test_feedback_on_mail_we_never_decided(env):
    eng, client, store = env()
    old = client.add_message(cls="orders")
    eng.sync_once()  # bootstrap: old is never decided
    client.user_add_label(old, eng.labels.ensure("travel"))
    assert eng.sync_once()["feedback"] == 1
    assert store.feedback()[0] | {"ts": 0} == {
        "msg_id": old,
        "predicted": None,
        "corrected": "travel",
        "ts": 0,
    }


def advance(eng, client, days: float) -> None:
    """Move the engine clock and the fake mailbox clock forward together."""
    eng.clock.t += days * DAY
    client.now_ms += int(days * DAY * 1000)


def test_history_expired_recovery(env):
    eng, client, store = env()
    eng.sync_once()
    advance(eng, client, 3)
    recent = client.add_message(cls="orders", age_days=1)  # arrived while we were away
    before_setup = client.add_message(cls="orders", age_days=5)  # internalDate predates setup
    client.expire_history()
    c = eng.sync_once()
    assert c["recovered"] and c["labeled"] == 1
    assert store.decision(recent) and store.decision(before_setup) is None
    # pointer restarts from the profile taken *before* the scan, so nothing that arrives during it is lost
    assert store.get("history_id") == "1002"
    nxt = client.add_message(cls="travel")
    client.expired_before = None
    c = eng.sync_once()
    assert c["labeled"] == 1 and c["feedback"] == 0 and store.decision(nxt)


def test_recovery_after_seven_days_offline(env):
    """The scan starts at the last successful poll, not a fixed 2-day window."""
    eng, client, store = env()
    old = client.add_message(cls="orders", age_days=3)  # before setup: backfill-only
    eng.sync_once()
    advance(eng, client, 1)
    assert eng.sync_once()["events"] == 0  # the last good poll, a day after setup
    advance(eng, client, 7)  # laptop closed for a week
    early = client.add_message(cls="orders", age_days=6.5)  # half a day after the last good poll
    mid = client.add_message(cls="travel", age_days=3)
    late = client.add_message(cls="updates", age_days=0)
    client.expire_history()
    q = []
    real_list = client.list_message_ids
    client.list_message_ids = lambda **kw: q.append(kw["query"]) or real_list(**kw)
    c = eng.sync_once()
    assert c["recovered"] and c["labeled"] == 3 and all(store.decision(m) for m in (early, mid, late))
    assert store.decision(old) is None
    since = int(q[0].removeprefix("after:"))
    assert eng.clock() - 7 * DAY - 2 * 3600 < since < eng.clock() - 7 * DAY  # last good poll, minus slack


def test_recovery_never_reaches_before_bootstrap(env):
    """Bootstrapped 1 day before the history expired: mail from before setup is never touched."""
    eng, client, store = env()
    pre_day = client.add_message(cls="orders", age_days=0.5)
    pre_minutes = client.add_message(
        cls="orders", age_days=30 / 1440
    )  # 30 min before setup (inside the slack)
    eng.sync_once()
    assert store.get("bootstrap_ts") == repr(eng.clock())
    advance(eng, client, 1)
    new = client.add_message(cls="travel", age_days=0.2)
    client.expire_history()
    c = eng.sync_once()
    assert c["recovered"] and c["labeled"] == 1 and store.decision(new)
    assert store.decision(pre_day) is None and store.decision(pre_minutes) is None
    assert client.modifies == [(new, [lid(client, "travel")], [])]


def test_recovery_without_any_successful_poll_starts_at_bootstrap(env):
    eng, client, store = env()
    before = client.add_message(cls="orders", age_days=0.01)
    eng.bootstrap()  # what `setup` does; the daemon never polled afterwards
    advance(eng, client, 2)
    after = client.add_message(cls="orders", age_days=1.5)
    client.expire_history()
    assert eng.recovery_since() == float(store.get("bootstrap_ts"))
    assert eng.sync_once()["labeled"] == 1 and store.decision(after) and store.decision(before) is None


def test_recovery_window_is_capped(env):
    eng, client, store = env(recovery_max_days=30)
    eng.sync_once()
    advance(eng, client, 45)
    too_old = client.add_message(cls="orders", age_days=40)
    ok = client.add_message(cls="orders", age_days=20)
    client.expire_history()
    assert eng.recovery_since() == pytest.approx(eng.clock() - 30 * DAY)
    assert eng.sync_once()["labeled"] == 1 and store.decision(ok) and store.decision(too_old) is None


def test_recovery_with_legacy_state_uses_two_days(env):
    eng, _, store = env()
    eng.sync_once()
    for k in ("bootstrap_ts", "last_ok_poll_start"):
        store.delete(k)
    assert eng.recovery_since() == pytest.approx(eng.clock() - 2 * DAY)


def test_run_forever_survives_transient_errors(env, caplog):
    eng, client, store = env(poll_seconds=60)
    eng.sync_once()
    client.add_message(cls="orders")
    client.fail("history", TransientError("503"), times=2)
    stop = threading.Event()
    sleeps: list[float] = []

    def sleep(s):
        sleeps.append(s)
        if len(sleeps) >= 4:
            stop.set()

    eng._sleep_fn = sleep
    with caplog.at_level(logging.INFO, logger="email_town_labeler.engine"):
        eng.run_forever(stop)
    assert sleeps[:3] == [15.0, 30.0, 60.0]
    assert store.stats()["applied"] == 1
    assert "TransientError" in store.get("last_error")
    text = caplog.text
    assert "poll failed" in text and "poll ok" in text
    assert "made-up" not in text and "example.test" not in text  # no content in logs


def test_run_forever_syncs_label_colors_and_survives_errors(env, caplog):
    eng, _, store = env()
    calls = []

    def sync():
        calls.append(1)
        raise TransientError("503")

    eng.labels.sync_colors = sync
    stop = threading.Event()
    eng._sleep_fn = lambda s: stop.set()
    with caplog.at_level(logging.WARNING, logger="email_town_labeler.engine"):
        eng.run_forever(stop)
    assert calls == [1] and "could not update label colors" in caplog.text
    assert store.get("history_id") is not None  # polling went on


def test_dry_run_does_not_sync_label_colors(env):
    eng, _, _ = env(dry_run=True)
    eng.labels.sync_colors = lambda: pytest.fail("synced colors in dry run")
    assert eng.sync_label_colors() == 0


def test_model_error_does_not_lose_mail(env):
    eng, client, store = env()
    eng.sync_once()
    m = client.add_message(cls="orders")
    eng.model.fail_next = 1
    with pytest.raises(RuntimeError):
        eng.sync_once()
    assert eng.sync_once()["labeled"] == 1 and store.decision(m)  # history pointer was not advanced


def test_stop_event_exits_promptly(env):
    eng, _, store = env()
    stop = threading.Event()
    stop.set()
    eng.run_forever(stop)
    assert store.get("history_id") is None


def test_backfill_and_undo(env):
    eng, client, store = env()
    ms = [client.add_message(cls="orders", age_days=d) for d in (0.5, 1, 3, 10)]
    eng.sync_once()
    c = eng.backfill(days=4)
    assert c["labeled"] == 3 and store.decision(ms[3]) is None
    assert eng.backfill(days=4)["already_decided"] == 3
    assert eng.backfill(limit=1)["already_decided"] == 1
    foreign = client.create_label("Mine")["id"]
    client.user_add_label(ms[0], foreign)
    eng.sync_once()
    assert eng.undo() == 3
    ours = eng.labels.our_label_ids()
    assert all(not set(m["label_ids"]) & ours for m in client.messages.values())
    assert foreign in client.messages[ms[0]]["label_ids"] and "INBOX" in client.messages[ms[0]]["label_ids"]
    assert eng.sync_once()["feedback"] == 0  # our own removals are not corrections
    assert store.stats()["applied"] == 0


def test_preview_touches_nothing(env):
    eng, client, store = env()
    for _ in range(7):
        client.add_message(cls="orders")
    rows = eng.preview(5)
    assert len(rows) == 5 and set(rows[0]) == {"id", "cls", "prob", "final"}
    assert not client.modifies and store.stats()["decisions"] == 0


def test_undo_reaches_trashed_and_spam_mail(env):
    eng, client, _ = env()
    eng.sync_once()
    a, b = client.add_message(cls="orders"), client.add_message(cls="travel")
    eng.sync_once()
    client.user_add_label(a, "TRASH")
    client.user_add_label(b, "SPAM")
    assert eng.undo() == 2
    ours = eng.labels.our_label_ids()
    assert not set(client.messages[a]["label_ids"]) & ours and not set(client.messages[b]["label_ids"]) & ours


# -- contacts, the policy and the new-sender rule --------------------------------------------------
PRIMARY = ["INBOX", "UNREAD", "CATEGORY_PERSONAL"]


def sent(client, to: str, **kw) -> str:
    """Mail the user sent (SENT only), with made-up recipients."""
    return client.add_message(labels=["SENT"], to=to, **kw)


def mail_from(client, addr: str, **kw) -> str:
    return client.add_message(**{"from": f"Someone <{addr}>"}, **kw)


def test_bootstrap_scans_sent_mail_for_contacts(env):
    eng, client, store = env(contacts_scan_days=365)
    sent(client, "Pal <Pal@Example.TEST>", cc="a@x.test, B <b@x.test>", bcc="c@x.test", age_days=10)
    sent(client, "pal@example.test", age_days=1)  # the earliest send time is kept
    sent(client, "undisclosed-recipients:;")  # no address at all
    sent(client, "too-old@x.test", age_days=400)  # outside contacts_scan_days
    mail_from(client, "inbound@x.test", to="me@example.test")  # received, not sent: never a contact
    eng.sync_once()
    assert eng.contacts_ready() and store.contacts_count() == 4
    assert store.known_contacts(
        ["PAL@example.test", "a@x.test", "b@x.test", "c@x.test", "too-old@x.test"]
    ) == {
        "pal@example.test",
        "a@x.test",
        "b@x.test",
        "c@x.test",
    }
    row = store._db.execute(
        "SELECT first_sent_ms FROM contacts WHERE address = 'pal@example.test'"
    ).fetchone()
    assert row[0] == client.now_ms - 10 * 86_400_000
    assert store.contact_addresses() == sorted(store.contact_addresses())


def test_contacts_scan_limit_and_rebuild(env):
    eng, client, store = env(contacts_scan_limit=1)
    sent(client, "older@x.test", age_days=2)
    sent(client, "newest@x.test", age_days=1)
    eng.sync_once()
    assert store.contact_addresses() == ["newest@x.test"]
    eng.cfg.contacts_scan_limit = 5000
    assert eng.rebuild_contacts() == {"contacts": 2, "sent_scanned": 2}
    store.add_contacts({"stale@x.test": 1})
    eng.cfg.contacts_scan_limit = 0  # no scan at all: rebuild empties the table
    assert eng.rebuild_contacts() == {"contacts": 0, "sent_scanned": 0} and store.contacts_count() == 0


def test_contacts_follow_history_and_recovery(env):
    eng, client, store = env()
    eng.sync_once()
    assert eng.contacts_ready() and store.contacts_count() == 0
    sent(client, "new@x.test")
    reply = mail_from(client, "NEW@x.test", cls="promotional", prob=0.9)  # same poll: learned first
    c = eng.sync_once()
    assert c["contacts_added"] == 1 and store.known_contacts(["new@x.test"]) == {"new@x.test"}
    assert store.decision(reply)["cls"] != "promotional"
    draft = client.add_message(labels=["DRAFT"], to="later@x.test")
    client.user_add_label(draft, "SENT")  # a draft that got sent: labelAdded SENT
    assert eng.sync_once()["contacts_added"] == 1
    sent(client, "gap@x.test")  # sent while the history pointer expired
    client.expire_history()
    c = eng.sync_once()
    assert c["recovered"] and c["contacts_added"] == 1 and store.contacts_count() == 3


def test_no_contacts_work_when_the_policy_does_not_need_them(env):
    eng, client, store = env(protect_known_contacts=False, new_sender=False)
    sent(client, "pal@x.test")
    eng.sync_once()
    sent(client, "pal2@x.test")
    eng.sync_once()
    assert not eng.contacts_ready() and store.contacts_count() == 0
    assert not [c for c in client.calls if c[0] == "get_metadata"]
    eng.cfg.new_sender = True  # turned on later: the next poll scans once
    eng.sync_once()
    assert eng.contacts_ready() and store.contacts_count() == 2


@pytest.mark.parametrize("protect,want", [(True, "unknown"), (False, "promotional")])
def test_known_contact_mail_stays_out_of_promotional(env, protect, want):
    eng, client, store = env(protect_known_contacts=protect)
    sent(client, "pal@x.test")
    eng.sync_once()
    friend = mail_from(client, "pal@x.test", cls="promotional", prob=0.9)
    stranger = mail_from(client, "deals@shop.test", cls="promotional", prob=0.9)
    eng.sync_once()
    assert store.decision(stranger)["cls"] == "promotional"
    d = store.decision(friend)
    assert d["cls"] == want and d["model_cls"] == "promotional" and d["prob"] == pytest.approx(0.9)


def test_policy_thresholds_change_the_final_class(env):
    eng, client, store = env()
    eng.sync_once()
    weak_promo = client.add_message(cls="promotional", prob=0.58)  # below promotional_threshold
    act = client.add_message(cls="action_needed", prob=0.3)  # below min_confidence, above action_threshold
    eng.sync_once()
    assert store.decision(weak_promo)["cls"] == "unknown"  # the best other class is far below the bar
    d = store.decision(act)
    assert d["cls"] == d["model_cls"] == "action_needed" and d["applied"]


def test_new_sender_label(env):
    eng, client, store = env()
    sent(client, "pal@x.test")
    eng.sync_once()
    new = mail_from(client, "Stranger@x.test", labels=PRIMARY)
    known = mail_from(client, "pal@x.test", labels=PRIMARY)
    social = mail_from(client, "s2@x.test", labels=["INBOX", "UNREAD", "CATEGORY_SOCIAL"])
    uncategorised = mail_from(client, "s3@x.test")
    content = mail_from(client, "s4@x.test", labels=PRIMARY, cls="orders", prob=0.9)
    mine = mail_from(client, "me@example.test", labels=PRIMARY)  # your own address is never new
    nobody = client.add_message(labels=PRIMARY, **{"from": "undisclosed"})  # no address to judge
    c = eng.sync_once()
    assert c["by_class"] == {"new_sender": 1, "unknown": 5, "orders": 1}
    assert lid(client, "new_sender") in client.messages[new]["label_ids"]
    d = store.decision(new)
    assert d["cls"] == "new_sender" and d["model_cls"] == "unknown" and d["applied"]
    for m in (known, social, uncategorised, mine, nobody):
        assert store.decision(m)["cls"] == "unknown" and not store.decision(m)["applied"]
    assert store.decision(content)["cls"] == "orders"
    # moving the label is a correction like any other; undo removes it like any other
    client.user_remove_label(new, lid(client, "new_sender"))
    client.user_add_label(new, eng.labels.ensure("updates"))
    assert eng.sync_once()["feedback"] == 1
    assert [(f["predicted"], f["corrected"]) for f in store.feedback()] == [("new_sender", "updates")]
    client.user_add_label(known, eng.labels.ensure("new_sender"))
    eng.sync_once()
    assert store.feedback()[-1]["corrected"] == "new_sender"
    assert eng.undo() == 3 and not set(client.messages[known]["label_ids"]) & eng.labels.our_label_ids()


@pytest.mark.parametrize("cfg", [{"new_sender": False}, {"dry_run": True}])
def test_new_sender_off_or_dry_run(env, cfg):
    eng, client, store = env(**cfg)
    eng.sync_once()
    m = mail_from(client, "stranger@x.test", labels=PRIMARY)
    eng.sync_once()
    d = store.decision(m)
    assert d["cls"] == ("unknown" if "new_sender" in cfg else "new_sender") and not d["applied"]
    assert not client.modifies


def test_new_sender_replaces_the_unknown_label(env):
    eng, client, _ = env(unknown_action="label")
    eng.sync_once()
    m = mail_from(client, "stranger@x.test", labels=PRIMARY)
    eng.sync_once()
    assert client.modifies == [(m, [lid(client, "new_sender")], [])]


def test_preview_applies_the_policy(env):
    eng, client, _ = env()
    sent(client, "pal@x.test")
    eng.sync_once()
    mail_from(client, "pal@x.test", cls="promotional", prob=0.9)
    mail_from(client, "stranger@x.test", labels=PRIMARY)
    rows = sorted(eng.preview(5), key=lambda r: r["id"])
    assert [(r["cls"], r["final"]) for r in rows] == [("promotional", "unknown"), ("unknown", "new_sender")]
    assert not client.modifies


def test_engine_uses_the_models_recommended_thresholds(tmp_path):
    model = FakeClassifier()
    model.manifest.config = {"recommended_policy": {"action_threshold": 0.07, "promotional_threshold": 0.8}}
    client = FakeGmailClient()
    labels = FakeLabelMap(client, "t", DISPLAY_NAMES)
    eng = Engine(client, labels, model, Store(tmp_path / "s.sqlite"), Config())
    assert (eng.cfg.action_threshold, eng.cfg.promotional_threshold) == (0.07, 0.8)
    cfg = Config()
    cfg._explicit.add("action_threshold")  # as if config.toml set it
    eng = Engine(client, labels, model, Store(tmp_path / "s2.sqlite"), cfg)
    assert (eng.cfg.action_threshold, eng.cfg.promotional_threshold) == (0.2, 0.8)
