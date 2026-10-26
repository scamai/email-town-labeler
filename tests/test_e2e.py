"""End to end: real GmailClient + LabelMap + Store + Engine + the `rules` model over the googleapiclient-shaped
FakeGmail, through a simulated day: bootstrap, mixed deliveries with injected API failures, user corrections,
expired history, a restart, backfill, trash/spam, undo. Made-up mail only.

Invariants checked on every API call that changes labels: nothing outside our prefix is ever touched, no
message ever gets our label twice, every eligible message ends up with exactly one of our labels, corrections
become feedback, undo leaves nothing behind (Trash and Spam included), and no mail content reaches the logs.
"""

from __future__ import annotations

import logging
from collections import Counter
from pathlib import Path

import pytest

from email_town_labeler.config import Config
from email_town_labeler.engine import Engine
from email_town_labeler.gmail.client import GmailClient
from email_town_labeler.gmail.labels import LabelMap
from email_town_labeler.models.registry import load_model
from email_town_labeler.state import Store
from email_town_labeler.taxonomy import CONTENT_LABELS, UNKNOWN
from tests.fakes.fake_service import FakeGmail, http_error

DAY_MS = 86_400_000
KINDS = ["order", "travel", "promo", "news", "update", "action", "personal"]


class Mailbox:
    """FakeGmail plus a record of every label change and label creation made through the API."""

    def __init__(self, tmp_path: Path, cfg: Config):
        self.g, self.cfg, self.tmp = FakeGmail(), cfg, tmp_path
        self.mods: list[tuple[str, list[str], list[str]]] = []
        self.created: list[str] = []
        self.secrets: list[str] = []  # made-up content that must never reach a log line
        self.model = load_model("rules", models_dir=tmp_path / "models")
        g = self.g
        orig_mod, orig_bmod, orig_create = g._modify_api, g._batch_modify, g._create_label

        def mod(id, body, fields=None):
            self.mods.append(
                (id, list(body.get("addLabelIds") or []), list(body.get("removeLabelIds") or []))
            )
            return orig_mod(id, body, fields)

        def bmod(body):
            for mid in body.get("ids") or []:
                self.mods.append(
                    (mid, list(body.get("addLabelIds") or []), list(body.get("removeLabelIds") or []))
                )
            return orig_bmod(body)

        def create(body, fields=None, _type="user"):
            self.created.append(body.get("name"))
            return orig_create(body, fields, _type)

        g._modify_api, g._batch_modify, g._create_label = mod, bmod, create

    def engine(self, store_path: Path | str) -> tuple[Engine, Store]:
        client = GmailClient(self.g, sleep=lambda s: None, max_gets_per_minute=None)
        labels = LabelMap(client, self.cfg.label_prefix, self.cfg.display_names())
        store = Store(store_path)
        clock = lambda: self.g.now_ms / 1000  # the mailbox's clock, so after: queries line up
        return Engine(client, labels, self.model, store, self.cfg, sleep=lambda s: None, clock=clock), store

    def mk(self, kind: str, i: int, **kw) -> str:
        subj = {
            "order": f"Your order Zorbex{i} has shipped",
            "travel": f"Flight itinerary Quintaro{i} booking",
            "promo": f"50% off Plimsoll{i} sale, limited time",
            "news": f"Weekly digest Marmaduke{i} edition",
            "update": f"Security alert Vexillum{i} account",
            "action": f"Action required: please review Ostrakon{i}",
            "personal": f"Lunch Thursday Brindlewick{i}?",
        }[kind]
        name, addr = {
            "order": (f"Shop Kestrelton{i}", f"noreply@shop{i}.example"),
            "travel": (f"Air Fennimore{i}", f"trips@air{i}.example"),
            "promo": (f"Deals Wobbegong{i}", f"deals@promo{i}.example"),
            "news": (f"Gazette Halcyonix{i}", f"news@gazette{i}.example"),
            "update": (f"Svc Tamarack{i}", f"no-reply@svc{i}.example"),
            "action": (f"Pat Gorsefield{i}", f"pat{i}@work.example"),
            "personal": (f"Sam Ambleside{i}", f"sam{i}@friends.example"),
        }[kind]
        body = f"Body text sesquipedalian{kind}{i} with private details flibbertigibbet{i}."
        self.secrets += [subj, name, addr, f"sesquipedalian{kind}{i}", f"flibbertigibbet{i}"]
        headers = dict(kw.pop("headers", {}) or {})
        labels = kw.pop("label_ids", None) or ["INBOX", "UNREAD"]
        if kind in ("news", "promo"):
            headers["List-Unsubscribe"] = f"<mailto:unsub{i}@list.example>"
        labels = labels + {
            "promo": ["CATEGORY_PROMOTIONS"],
            "update": ["CATEGORY_UPDATES"],
            "personal": ["CATEGORY_PERSONAL"],
        }.get(kind, [])
        return self.g.deliver(
            subject=subj, from_=f"{name} <{addr}>", body=body, headers=headers, label_ids=labels, **kw
        )

    def ours(self) -> set[str]:
        p = self.cfg.label_prefix.lower()
        return {
            lb["id"]
            for lb in self.g._labels.values()
            if lb["name"].lower() == p or lb["name"].lower().startswith(p + "/")
        }

    def our_labels_on(self, mid: str) -> list[str]:
        ours = self.ours()
        return [self.g._labels[x]["name"] for x in self.g.labels_of(mid) if x in ours]

    def assert_single_label(self, tag: str) -> None:
        multi = {m: self.our_labels_on(m) for m in self.g.message_ids() if len(self.our_labels_on(m)) > 1}
        assert not multi, f"[{tag}] messages with more than one email-town-labeler label: {multi}"

    def assert_invariants(self) -> None:
        ours = self.ours()
        outside = [(m, a, r) for m, a, r in self.mods if (set(a) | set(r)) - ours]
        assert not outside, f"a modify touched labels outside the prefix: {outside[:3]}"
        adds = Counter(m for m, a, _ in self.mods if set(a) & ours)
        assert not adds or max(adds.values()) == 1, (
            f"a message got our label added twice: {adds.most_common(3)}"
        )
        assert all(len(a) <= 1 for _, a, _ in self.mods), "a modify added more than one label"
        assert all(n.lower().startswith(self.cfg.label_prefix.lower()) for n in self.created), self.created


@pytest.fixture
def box(tmp_path):
    cfg = Config(unknown_action="label").validate()  # exercise the unknown label too
    return Mailbox(tmp_path, cfg)


def test_simulated_day(box, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)  # every logger, every level
    g = box.g

    # -- morning: pre-existing mail, then setup -------------------------------------------------------
    old = [
        box.mk(k, 100 + j, internal_date=g.now_ms - 10 * DAY_MS)
        for j, k in enumerate(["order", "news", "personal", "travel", "update"])
    ]
    eng, store = box.engine(tmp_path / "state.sqlite")
    assert eng.sync_once().get("bootstrapped")
    for cls in CONTENT_LABELS + [UNKNOWN]:
        eng.labels.ensure(cls)
    assert not any(box.our_labels_on(m) for m in old), "bootstrap leaves pre-existing mail alone"

    # -- 30 mixed deliveries between polls, with rate limits and server errors -------------------------
    delivered, special = [], {}
    for batch in range(6):
        for j in range(5):
            i = batch * 5 + j
            k = KINDS[i % len(KINDS)]
            if i == 3:
                mid = box.mk(k, i, label_ids=["SENT"])
                special[mid] = "sent"
            elif i == 7:
                mid = box.mk(k, i, label_ids=["SPAM"])
                special[mid] = "spam"
            elif i == 11:
                mid = box.mk(k, i, label_ids=["UNREAD"])  # filtered to skip the inbox
                special[mid] = "archived"
            elif i == 16:
                mid = box.mk(k, i)
                g.remove_message(mid)  # deleted before we fetch it
                special[mid] = "deleted"
            else:
                mid = box.mk(k, i)
            delivered.append(mid)
        if batch == 1:
            g.fail_next(3, 429, method="messages.get")
        if batch == 2:
            g.fail_next(1, 403, method="messages.get", reason="rateLimitExceeded")
            g.fail_next(1, 503, method="batch")
        if batch == 3:
            g.fail_next(2, 429, method="messages.modify", retry_after=1)
            g.fail_next(1, 500, method="history.list")
        if batch == 4:
            g.fail_next(50, 429, method="messages.modify")  # retries exhausted: this poll fails
            with pytest.raises(Exception):  # noqa: B017 - whatever the client raises after its retries
                eng.sync_once()
            g._failures.clear()
        eng.sync_once()
        box.assert_single_label(f"poll {batch}")
    eligible = [m for m in delivered if m not in special]
    assert all(len(box.our_labels_on(m)) == 1 for m in eligible), "every eligible inbox message got one label"
    assert not any(box.our_labels_on(m) for m, why in special.items() if why != "deleted")

    # -- the user moves 3 labels and removes 1: corrections become feedback ----------------------------
    lab = {m: box.our_labels_on(m)[0] for m in eligible}
    movers = [m for m in eligible if lab[m] != "e/travel"][:3]
    remover = next(m for m in eligible if m not in movers)
    for m in movers:
        g.user_relabel(m, add=["e/travel"], remove=[lab[m]])
    g.user_relabel(remover, remove=[lab[remover]])
    assert eng.sync_once()["feedback"] == 4
    fb = {f["msg_id"]: f for f in store.feedback()}
    for m in movers:
        assert fb[m]["corrected"] == "travel"
        assert fb[m]["predicted"] == lab[m].split("/", 1)[1].replace(" ", "_")
    assert fb[remover]["corrected"] is None
    c = eng.sync_once()
    assert c["feedback"] == 0 and c["labeled"] == 0, "a quiet poll records nothing and relabels nothing"
    assert not box.our_labels_on(remover), "a label the user removed is not put back"

    # -- history expiry --------------------------------------------------------------------------------
    late = [box.mk("order", 200), box.mk("news", 201)]
    g.expire_history()
    assert eng.sync_once().get("recovered")
    assert all(box.our_labels_on(m) for m in late), "mail that arrived during the gap gets labeled"
    assert all(box.our_labels_on(m) == ["e/travel"] for m in movers), "corrections survive recovery"
    assert not box.our_labels_on(remover) and not any(box.our_labels_on(m) for m in old)
    box.assert_single_label("after recovery")

    # -- restart: a new Engine on the same Store -------------------------------------------------------
    store.close()
    eng, store = box.engine(tmp_path / "state.sqlite")
    after = [box.mk("travel", 300), box.mk("promo", 301)]
    c = eng.sync_once()
    assert not c.get("bootstrapped") and c["labeled"] == 2
    g.user_relabel(after[0], add=["e/orders"], remove=["e/travel"])
    assert eng.sync_once()["feedback"] == 1

    # -- backfill labels the pre-setup mail, once ------------------------------------------------------
    c = eng.backfill(days=30)
    assert c["labeled"] == len(old) and all(box.our_labels_on(m) for m in old)
    assert eng.backfill(days=30)["labeled"] == 0
    box.assert_single_label("after backfill")

    # -- the user trashes one labeled message and marks another as spam; undo reaches both -------------
    trashed, spammed = [m for m in eligible if box.our_labels_on(m)][:2]
    g.user_relabel(trashed, add=["TRASH"], remove=["INBOX"])
    g.user_relabel(spammed, add=["SPAM"], remove=["INBOX"])
    assert box.our_labels_on(trashed) and box.our_labels_on(spammed)
    eng.sync_once()
    foreign = {m: [x for x in g.labels_of(m) if x not in box.ours()] for m in g.message_ids()}
    fb_before = len(store.feedback())
    assert eng.undo() > 0
    left = {m: box.our_labels_on(m) for m in g.message_ids() if box.our_labels_on(m)}
    assert not left, f"undo left email-town-labeler labels behind: {left}"
    assert {m: g.labels_of(m) for m in g.message_ids()} == foreign, "undo touched other labels"
    c = eng.sync_once()
    assert len(store.feedback()) == fb_before and c["labeled"] == 0, "undo's own removals are not feedback"

    # -- global invariants over every label change made through the API --------------------------------
    box.assert_invariants()

    # -- privacy: no made-up subject, sender, address or body text in any log line ---------------------
    logs = caplog.text.lower()
    assert len(caplog.records) > 20
    leaks = sorted({s for s in box.secrets if s.lower() in logs})
    assert not leaks, f"mail content in logs: {leaks[:5]}"


def test_poll_failing_mid_batch_never_double_labels(box):
    """A poll that dies after labeling some of a batch is retried without labeling anything twice."""
    g = box.g
    eng, store = box.engine(":memory:")
    eng.sync_once()
    ids = [g.deliver(subject=f"Your order {i} shipped") for i in range(6)]
    state = {"n": 0, "armed": True}
    orig = g._modify_api

    def flaky(id, body, fields=None):
        if state["armed"]:
            state["n"] += 1
            if state["n"] > 2:
                raise http_error(429)
        return orig(id, body, fields)

    g._modify_api = flaky
    with pytest.raises(Exception):  # noqa: B017
        eng.sync_once()
    state["armed"] = False
    r = eng.sync_once()
    assert r["labeled"] == 4 and r["already_decided"] == 2  # the 2 labeled before the failure
    assert all(box.our_labels_on(m) == ["e/orders"] for m in ids)

    # crash between Gmail's modify and recording the decision: the label on the message is the record
    m = g.deliver(subject="Your order X shipped")
    real = store.record_decision

    def boom(*a, **k):
        raise RuntimeError("killed")

    store.record_decision = boom
    with pytest.raises(RuntimeError):
        eng.sync_once()
    store.record_decision = real
    r = eng.sync_once()
    assert r["labeled"] == 0 and r["skip_reasons"] == {"ours": 1}
    assert box.our_labels_on(m) == ["e/orders"]
    box.assert_invariants()


def test_contacts_policy_and_new_sender(box, tmp_path, caplog):
    """Sent mail seeds contacts at setup and from history; the known-contact guard and the new-sender rule
    run on the real Gmail layer; decisions keep the model's class; nothing about addresses reaches logs."""
    caplog.set_level(logging.DEBUG)
    g = box.g
    pal, newpal = "sam1@friends.example", "sam3@friends.example"
    box.secrets += [pal, newpal, "bcc7@friends.example"]
    g.deliver(  # sent a month before setup: found by the bootstrap scan of SENT (headers only)
        label_ids=["SENT"],
        from_=g.email,
        to=f"Sam Ambleside1 <{pal}>",
        bcc="bcc7@friends.example",
        internal_date=g.now_ms - 30 * DAY_MS,
    )
    eng, store = box.engine(tmp_path / "state.sqlite")
    assert eng.sync_once().get("bootstrapped")
    assert store.contacts_count() == 2 and "messages.get" in g.calls

    friend = box.mk("personal", 1)  # from pal: Primary, but you have written to them
    stranger = box.mk("personal", 2)  # Primary, never written to
    promo_from_pal = g.deliver(
        subject="50% off sale, limited time",
        from_=f"Sam Ambleside1 <{pal}>",
        headers={"List-Unsubscribe": "<mailto:u@list.example>"},
        label_ids=["INBOX", "UNREAD", "CATEGORY_PROMOTIONS"],
    )
    promo = box.mk("promo", 4)
    g.deliver(label_ids=["SENT"], from_=g.email, to=f"Sam <{newpal}>")  # you write to someone new...
    reply = box.mk("personal", 3)  # ...and their mail in the same poll is not "new sender"
    c = eng.sync_once()
    assert c["contacts_added"] == 1 and store.contacts_count() == 3
    assert box.our_labels_on(stranger) == ["e/new sender"]
    assert box.our_labels_on(friend) == box.our_labels_on(reply) == ["e/unknown"]
    assert box.our_labels_on(promo) == ["e/promotional"]
    assert box.our_labels_on(promo_from_pal) not in (["e/promotional"], [])
    d = store.decision(promo_from_pal)
    assert d["model_cls"] == "promotional" and d["cls"] != "promotional"
    assert store.decision(stranger)["model_cls"] == "unknown"

    # the user disagrees with "new sender": a correction; undo takes the label away like any other
    eng.labels.ensure("updates")
    g.user_relabel(stranger, add=["e/updates"], remove=["e/new sender"])
    assert eng.sync_once()["feedback"] == 1
    assert store.feedback()[-1]["predicted"] == "new_sender"
    assert eng.undo() == 5 and not any(box.our_labels_on(m) for m in g.message_ids())
    box.assert_invariants()

    logs = caplog.text.lower()
    leaks = sorted({s for s in box.secrets if s.lower() in logs})
    assert not leaks, f"mail content or addresses in logs: {leaks[:5]}"
