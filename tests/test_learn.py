"""learn.py and its engine/CLI wiring: signals from corrections, teacher (Town) labels and kept labels, the
personal head on stored scores, re-tuned thresholds. Made-up mail and synthetic scores only."""

from __future__ import annotations

import json
import logging

import numpy as np
import pytest

from email_town_labeler import learn
from email_town_labeler.config import Config, ConfigError, config_from_dict, dump_config
from email_town_labeler.engine import DAY, Engine
from email_town_labeler.state import Store
from email_town_labeler.taxonomy import CLASSES, DISPLAY_NAMES
from email_town_labeler.types import Prediction
from tests.fakes.fake_client import FakeClassifier, FakeGmailClient, FakeLabelMap

K = len(CLASSES)
POL = {"action_threshold": 0.2, "promotional_threshold": 0.6}
NOW = 1_000_000_000.0


class Clock:
    def __init__(self, t: float):
        self.t = t

    def __call__(self) -> float:
        return self.t


def scores_for(y: int, rng, *, strength=3.0, confuse: tuple[int, float] | None = None) -> np.ndarray:
    z = rng.normal(0, 1, K)
    z[y] += strength
    if confuse:
        z[confuse[0]] += confuse[1]
    return z - np.log(np.exp(z).sum())


def fill(
    store,
    n,
    *,
    seed=0,
    kind="teacher",
    model="base@1",
    ts=NOW - 30 * DAY,
    newsletter_looks_promo=True,
    prefix="m",
):
    """n decisions with synthetic scores; the base model mistakes this user's newsletters for promotional."""
    rng = np.random.default_rng(seed)
    for i in range(n):
        y = int(rng.choice(K, p=[0.08, 0.12, 0.15, 0.05, 0.03, 0.35, 0.22]))
        bulk = float(y in (1, 2))
        z = scores_for(y, rng, confuse=(2, 4.0) if newsletter_looks_promo and y == 1 else None)
        p = np.exp(z)
        top = CLASSES[int(p.argmax())]
        mid = f"{prefix}{seed}-{i}"
        store.record_decision(
            mid,
            top,
            float(p.max()),
            dict(zip(CLASSES, p)),
            model,
            True,
            model_cls=top,
            scores=z.tolist(),
            feats=[0, bulk, 0, 0, 0, 0, 0, 0],
        )
        store._db.execute("UPDATE decisions SET ts = ? WHERE msg_id = ?", (ts, mid))
        if kind == "teacher":
            store.record_teacher(mid, CLASSES[y])
        elif kind == "correction" and top != CLASSES[y]:
            store.record_feedback(mid, top, CLASSES[y])


# -- scores and features ----------------------------------------------------------------------------
def test_base_scores_from_logits_probs_and_bad_input():
    z = learn.base_scores(Prediction("orders", 0.9, {}, logits=[1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]))
    assert np.allclose(np.exp(z).sum(), 1.0) and z.argmax() == 6
    # shift-invariant: the same softmax gives the same scores
    z2 = learn.base_scores(Prediction("orders", 0.9, {}, logits=[11.0, 12, 13, 14, 15, 16, 17]))
    assert np.allclose(z, z2)
    p = {c: 0.0 for c in CLASSES} | {"travel": 1.0}
    zp = learn.base_scores(Prediction("travel", 1.0, p))
    assert zp.min() == learn.FLOOR and zp[CLASSES.index("travel")] == 0.0
    weird = learn.base_scores(Prediction("x", 0.1, {"orders": float("nan"), "travel": -1}))
    assert np.isfinite(weird).all()  # nothing usable: unknown
    inf = learn.base_scores(Prediction("orders", 1.0, {}, logits=[float("-inf")] * 6 + [0.0]))
    assert inf.min() == learn.FLOOR and np.isfinite(inf).all()


def test_features_are_yes_no_facts_only():
    rec = {
        "label_ids": ["INBOX", "CATEGORY_PROMOTIONS"],
        "headers": {"list-unsubscribe": "<mailto:x@example.test>", "subject": "made-up"},
        "has_calendar": True,
    }
    f = dict(zip(learn.FEATURES, learn.features(rec, known_contact=True)))
    assert f == {
        "known_contact": 1.0,
        "bulk": 1.0,
        "primary": 0.0,
        "promotions": 1.0,
        "social": 0.0,
        "updates_tab": 0.0,
        "forums": 0.0,
        "calendar": 1.0,
    }


# -- fitting ----------------------------------------------------------------------------------------
def test_identity_head_reproduces_the_model():
    W, c = learn.identity()
    head = learn.PersonalHead(W, c, "id", "m@1")
    rng = np.random.default_rng(1)
    z = np.array([scores_for(3, rng) for _ in range(5)])
    assert np.allclose(head.proba(z, np.zeros((5, learn.F))), np.exp(z))
    empty = learn.Examples(
        np.zeros((0, K + learn.F)), np.zeros(0, int), np.zeros(0, int), np.zeros(0), [], []
    )
    W2, c2 = learn.fit(empty, lam=1.0)
    assert np.array_equal(W2, W) and np.array_equal(c2, c)


def test_prior_keeps_a_few_signals_from_overwriting_the_model():
    rng = np.random.default_rng(2)
    X = np.hstack([np.array([scores_for(5, rng) for _ in range(3)]), np.zeros((3, learn.F))])
    ex = learn.Examples(X, np.full(3, 0), np.full(3, -1), np.ones(3), ["correction"] * 3, ["a", "b", "c"])
    strong, weak = learn.fit(ex, lam=10.0), learn.fit(ex, lam=0.01)
    shift = lambda Wc: np.abs(Wc[0] - learn.identity()[0]).sum()
    assert shift(strong) < shift(weak)
    # with a strong prior the model's own top class survives three contrary corrections
    P = softmax_rows(X @ strong[0].T + strong[1])
    assert (P.argmax(axis=1) == 5).all()


def softmax_rows(Z):
    Z = Z - Z.max(axis=1, keepdims=True)
    E = np.exp(Z)
    return E / E.sum(axis=1, keepdims=True)


def test_removal_signal_pushes_away_from_the_class():
    rng = np.random.default_rng(3)
    X = np.hstack([np.array([scores_for(2, rng, strength=1.0) for _ in range(40)]), np.zeros((40, learn.F))])
    ex = learn.Examples(
        X, np.full(40, -1), np.full(40, 2), np.ones(40), ["removal"] * 40, list(map(str, range(40)))
    )
    W, c = learn.fit(ex, lam=1.0)
    before, after = softmax_rows(X[:, :K])[:, 2].mean(), softmax_rows(X @ W.T + c)[:, 2].mean()
    assert after < before


# -- one run ----------------------------------------------------------------------------------------
def test_no_signals_no_head_no_changes(tmp_path):
    s = Store(tmp_path / "s.sqlite")
    res = learn.run(s, "base@1", Config(), current_policy=POL, base_policy=POL, now=NOW)
    assert res.artifact.head is None and not res.artifact.active and res.artifact.policy == {}
    assert res.changed_policy == {} and sum(res.examples.values()) == 0
    assert "collecting signals: 0 of 30" in res.artifact.reason


def test_teacher_signals_fix_a_systematic_error_and_activate(tmp_path):
    s = Store(tmp_path / "s.sqlite")
    fill(s, 600)
    res = learn.run(s, "base@1", Config(), current_policy=POL, base_policy=POL, now=NOW)
    a = res.artifact
    assert res.examples["teacher"] == 600 and a.active and a.head is not None
    hh, hb = a.metrics["heldout_head"], a.metrics["heldout_base"]
    assert hh["acc_strong"] > hb["acc_strong"] and hh["log_loss"] < hb["log_loss"]
    # bulk newsletters that look promotional to the model are newsletters after the head
    rng = np.random.default_rng(99)
    z = np.array([scores_for(1, rng, confuse=(2, 4.0)) for _ in range(50)])
    f = np.zeros((50, learn.F))
    f[:, learn.FEATURES.index("bulk")] = 1
    base_right = (z.argmax(axis=1) == 1).mean()
    head_right = (a.head.proba(z, f).argmax(axis=1) == 1).mean()
    assert head_right > base_right + 0.3
    # round trip through personal.json
    p = learn.save(tmp_path / "personal.json", a)
    back = learn.load(p, "base@1")
    assert back is not None and back.active and back.head.id == a.head.id
    assert np.allclose(back.head.W, a.head.W, atol=1e-6)
    assert learn.load(p, "other@2") is None  # a head is only for the model it was trained on
    text = p.read_text()
    assert "m0-" not in text  # no message ids, let alone content


def test_head_refused_when_it_does_not_beat_the_model(tmp_path):
    s = Store(tmp_path / "s.sqlite")
    fill(s, 300, newsletter_looks_promo=False)  # the model is already right: nothing to learn
    res = learn.run(s, "base@1", Config(), current_policy=POL, base_policy=POL, now=NOW)
    a = res.artifact
    assert a.head is not None and a.metrics["heldout_head"]["n"] == 300
    if a.active:  # it may only activate if it truly beats the model held out
        assert a.metrics["heldout_head"]["log_loss"] < a.metrics["heldout_base"]["log_loss"]
        assert a.metrics["heldout_head"]["acc_strong"] >= a.metrics["heldout_base"]["acc_strong"]


def test_better_rules():
    base = {"log_loss": 0.5, "acc_strong": 0.8, "acc_confirm": 0.99, "action_recall": 0.95}
    ok = dict(base, log_loss=0.4, acc_strong=0.85)
    assert learn.better(ok, base, recall_target=0.9)[0]
    assert not learn.better(dict(ok, log_loss=0.6), base)[0]
    assert not learn.better(dict(ok, acc_strong=0.7), base)[0]
    assert not learn.better(dict(ok, acc_confirm=0.9), base)[0]
    assert "recall" in learn.better(dict(ok, action_recall=0.85), base, recall_target=0.9)[1]
    assert learn.better(dict(ok, action_recall=0.91), base, recall_target=0.9)[0]  # target reached


def test_min_examples_gate(tmp_path):
    s = Store(tmp_path / "s.sqlite")
    fill(s, 29)
    res = learn.run(s, "base@1", Config(), current_policy=POL, base_policy=POL, now=NOW)
    assert res.artifact.head is None and "29 of 30" in res.artifact.reason


def test_collect_signal_kinds_and_priority(tmp_path):
    s = Store(tmp_path / "s.sqlite", clock=lambda: NOW - 10 * DAY)
    z = learn.base_scores(Prediction("orders", 0.9, {c: 1 / K for c in CLASSES}))
    f = [0.0] * learn.F

    def dec(mid, cls="orders", applied=True, ts=NOW - 10 * DAY, dry=False):
        s.record_decision(mid, cls, 0.9, {}, "m@1", applied, dry_run=dry, scores=z, feats=f)
        s._db.execute("UPDATE decisions SET ts = ? WHERE msg_id = ?", (ts, mid))

    dec("corr"), s.record_feedback("corr", "orders", "travel")
    dec("ns"), s.record_feedback("ns", "orders", "new_sender")  # new sender is the model's unknown
    dec("rem"), s.record_feedback("rem", "orders", None)
    dec("rem_ns", cls="new_sender"), s.record_feedback("rem_ns", "new_sender", None)  # no signal
    dec("both"), s.record_feedback("both", "orders", "updates"), s.record_teacher("both", "travel")
    dec("teach"), s.record_teacher("teach", "newsletters")
    dec("await"), s.record_teacher("await", None)  # awaiting reply: no signal either way
    dec("kept")
    dec("kept_young", ts=NOW - 2 * DAY)
    dec("dry", dry=True)
    dec("unlabeled", cls="unknown", applied=False, ts=NOW - 20 * DAY)
    dec("late", ts=NOW - 5 * DAY)
    s.record_decision("old_model", "orders", 0.9, {}, "old@1", True, scores=z, feats=f)
    s.record_decision("no_scores", "orders", 0.9, {}, "m@1", True)  # from before v4
    ex = learn.collect(s, "m@1", Config(), now=NOW)
    got = {
        i: (k, CLASSES[y] if y >= 0 else None, CLASSES[n] if n >= 0 else None)
        for i, k, y, n in zip(ex.ids, ex.kind, ex.y, ex.neg)
    }
    assert got["corr"] == ("correction", "travel", None)
    assert got["ns"] == ("correction", "unknown", None)
    assert got["rem"] == ("removal", None, "orders")
    assert got["both"] == ("correction", "updates", None)  # you outrank the teacher
    assert got["teach"] == ("teacher", "newsletters", None)
    # the teacher labeled "late" (decided after these), so it was running: unlabeled old mail -> unknown
    s.record_teacher("late", "orders")
    ex = learn.collect(s, "m@1", Config(), now=NOW)
    got = {i: (k, CLASSES[y] if y >= 0 else None) for i, k, y in zip(ex.ids, ex.kind, ex.y)}
    assert got["kept"] == ("teacher_none", "unknown") and got["unlabeled"] == ("teacher_none", "unknown")
    assert got["dry"] == ("teacher_none", "unknown")
    assert "kept_young" not in got and "await" not in got and "rem_ns" not in got
    assert "old_model" not in got and "no_scores" not in got
    # with the teacher off, kept labels are weak confirmations instead
    off = learn.collect(s, "m@1", Config(learn_teacher="off"), now=NOW)
    got = {i: (k, w) for i, k, w in zip(off.ids, off.kind, off.w)}
    assert got["kept"] == ("confirm", 0.2) and "dry" not in got and "unlabeled" not in got
    assert got["teach"] == ("confirm", 0.2) and "kept_young" not in got  # teacher rows ignored
    assert (
        learn.collect(s, "m@1", Config(learn_teacher="off", learn_confirm_weight=0), now=NOW).counts()[
            "confirm"
        ]
        == 0
    )


def test_history_gap_blocks_teacher_none(tmp_path):
    s = Store(tmp_path / "s.sqlite")
    z, f = [0.0] + [learn.FLOOR] * (K - 1), [0.0] * learn.F
    for mid, ts in (("a", NOW - 20 * DAY), ("b", NOW - 9 * DAY), ("later", NOW - 4 * DAY)):
        s.record_decision(mid, "updates", 0.9, {}, "m@1", True, scores=z, feats=f)
        s._db.execute("UPDATE decisions SET ts = ? WHERE msg_id = ?", (ts, mid))
    s.record_teacher("later", "orders")
    learn.add_history_gap(s, NOW - 8.5 * DAY, NOW - 8 * DAY)  # Town events for "b" may have been lost
    kinds = dict(zip((ex := learn.collect(s, "m@1", Config(), now=NOW)).ids, ex.kind))
    assert kinds.get("a") == "teacher_none" and kinds.get("b") != "teacher_none"


# -- thresholds -------------------------------------------------------------------------------------
def _ex(P, y, kinds=None, neg=None, known=None):
    n = len(y)
    X = np.hstack([np.log(np.clip(P, 1e-9, 1)), np.zeros((n, learn.F))])
    if known is not None:
        X[:, K + learn.FEATURES.index("known_contact")] = known
    return learn.Examples(
        X,
        np.asarray(y),
        np.asarray(neg if neg is not None else [-1] * n),
        np.ones(n),
        kinds or ["teacher"] * n,
        [str(i) for i in range(n)],
    )


def _row(pa=0.0, pp=0.0, rest="updates"):
    p = np.full(K, 0.0)
    p[learn.A], p[learn.PROMO] = pa, pp
    p[CLASSES.index(rest)] += 1 - pa - pp
    return p


def test_action_threshold_lowers_to_keep_recall():
    # 20 action-needed signals, half of them at p=0.1: below the current 0.2 bar
    P = np.array([_row(pa=0.1)] * 10 + [_row(pa=0.5)] * 10)
    new, info = learn.tune_policy(P, _ex(P, [learn.A] * 20), current=POL, cfg=Config())
    assert new["action_threshold"] == 0.1 and info["n_action"] == 20
    # an explicit config.toml value is never touched
    cfg = config_from_dict({"policy": {"action_threshold": 0.2}})
    assert learn.tune_policy(P, _ex(P, [learn.A] * 20), current=POL, cfg=cfg)[0]["action_threshold"] == 0.2


def test_action_threshold_rises_slowly_and_never_on_kept_labels_alone():
    P = np.array([_row(pa=0.6)] * 40)
    confirmed = _ex(P, [learn.A] * 40, kinds=["confirm"] * 40)
    assert learn.tune_policy(P, confirmed, current=POL, cfg=Config())[0]["action_threshold"] == 0.2
    taught = _ex(P, [learn.A] * 40)
    assert learn.tune_policy(P, taught, current=POL, cfg=Config())[0]["action_threshold"] == 0.25
    few = _ex(P[:9], [learn.A] * 9)
    assert learn.tune_policy(P[:9], few, current=POL, cfg=Config())[0]["action_threshold"] == 0.2


def test_promotional_threshold_rises_when_important_mail_is_labeled_promotional():
    P = np.array([_row(pp=0.7)] * 30 + [_row(pp=0.9)] * 30)
    y = [CLASSES.index("unknown")] * 5 + [learn.PROMO] * 55  # 5 personal mails at p_promo 0.7
    new, info = learn.tune_policy(P, _ex(P, y), current=POL, cfg=Config())
    assert new["promotional_threshold"] == 0.71 and info["important_in_promotional_now"] == 5
    # a promotional label you removed counts as important too
    neg = [learn.PROMO] + [-1] * 59
    y2 = [-1] + [learn.PROMO] * 59
    assert (
        learn.tune_policy(P, _ex(P, y2, neg=neg), current=POL, cfg=Config())[0]["promotional_threshold"]
        == 0.71
    )
    # known contacts are never promotional anyway, so they do not count against the threshold
    known = [1] * 5 + [0] * 55
    new, info = learn.tune_policy(P, _ex(P, y, known=known), current=POL, cfg=Config())
    assert info["important_in_promotional_now"] == 0 and new["promotional_threshold"] == 0.5


def test_promotional_threshold_falls_only_with_evidence_and_slowly():
    P = np.array([_row(pp=0.55)] * 25)
    cur = {"action_threshold": 0.2, "promotional_threshold": 0.8}
    new, _ = learn.tune_policy(P, _ex(P, [learn.PROMO] * 25), current=cur, cfg=Config())
    assert new["promotional_threshold"] == 0.7  # at most 0.1 per run
    few = _ex(P[:10], [learn.PROMO] * 10)
    assert learn.tune_policy(P[:10], few, current=cur, cfg=Config())[0]["promotional_threshold"] == 0.8


# -- config -----------------------------------------------------------------------------------------
def test_learn_config_roundtrip_and_validation():
    cfg = config_from_dict(
        {"learn": {"auto": False, "confirm_weight": 0.0}, "teacher_labels": {"orders": "Receipts"}}
    )
    assert not cfg.learn_auto and cfg.learn_confirm_weight == 0.0
    assert cfg.teacher_names()["orders"] == "Receipts" and cfg.teacher_names()["travel"] == "travel"
    text = dump_config(cfg)
    assert "[learn]" in text and "auto = false" in text and "interval_hours" not in text
    assert '[teacher_labels]\n"orders" = "Receipts"' in text
    assert "[learn]" not in dump_config(Config())  # defaults stay out of config.toml
    for bad in (
        {"learn": {"teacher": "maybe"}},
        {"learn": {"prior_strength": 0}},
        {"learn": {"action_recall_target": 1.5}},
        {"learn": {"nope": 1}},
        {"teacher_labels": {"spam": "x"}},
    ):
        with pytest.raises(ConfigError):
            config_from_dict(bad)


def test_policy_sources_precedence():
    from email_town_labeler.models.base import ModelManifest

    man = ModelManifest(
        "m",
        "1",
        "fake",
        config={"recommended_policy": {"action_threshold": 0.07, "promotional_threshold": 0.8}},
    )
    learned = {"action_threshold": 0.05}
    src = Config().policy_sources(man, learned)
    assert src == {"action_threshold": (0.05, "learned"), "promotional_threshold": (0.8, "model")}
    explicit = config_from_dict({"policy": {"action_threshold": 0.3}})
    assert explicit.policy_sources(man, learned)["action_threshold"] == (0.3, "config")
    cfg = Config().for_model(man, learned)
    assert cfg.action_threshold == 0.05 and cfg.promotional_threshold == 0.8
    assert not cfg.is_explicit("action_threshold")


# -- engine -----------------------------------------------------------------------------------------
TOWN = ("action needed", "newsletters", "promotional", "orders", "updates")


@pytest.fixture
def town(tmp_path):
    def make(*, town_labels=TOWN, learn_path=True, **cfg_kw):
        client = FakeGmailClient()
        for name in town_labels:
            client.create_label(name)
        cfg = Config(**cfg_kw)
        labels = FakeLabelMap(client, cfg.label_prefix, cfg.display_names())
        store = Store(tmp_path / f"s{len(list(tmp_path.iterdir()))}.sqlite")
        clock = Clock(client.now_ms / 1000)
        eng = Engine(
            client,
            labels,
            FakeClassifier(),
            store,
            cfg,
            clock=clock,
            learn_path=tmp_path / "personal.json" if learn_path else None,
        )
        return eng, client, store, clock

    return make


def test_decisions_keep_scores_and_features(town):
    eng, client, store, _ = town()
    eng.sync_once()
    mid = client.add_message(
        cls="orders", prob=0.9, labels=("INBOX", "CATEGORY_UPDATES"), **{"list-id": "<x.example.test>"}
    )
    eng.sync_once()
    d = store.decision(mid)
    assert len(d["scores"]) == K and np.isclose(np.exp(d["scores"]).sum(), 1.0, atol=1e-3)
    f = dict(zip(learn.FEATURES, d["feats"]))
    assert f["bulk"] == 1 and f["updates_tab"] == 1 and f["primary"] == 0 and d["head_id"] is None


def test_teacher_labels_recorded_from_history_and_from_the_message(town):
    eng, client, store, _ = town()
    eng.sync_once()
    a = client.add_message(cls="promotional", prob=0.9)
    eng.sync_once()
    client.user_add_label(a, client.label_id("newsletters"))  # Town labels it after us
    c = eng.sync_once()
    assert c["teacher"] == 1 and store.teacher(a)["cls"] == "newsletters"
    assert c["feedback"] == 0  # not one of ours: not a correction
    b = client.add_message(cls="orders", prob=0.9)
    client.user_add_label(b, client.label_id("orders"))  # Town labels it before our poll
    eng.sync_once()
    assert store.teacher(b)["cls"] == "orders"
    client.user_remove_label(a, client.label_id("newsletters"))  # teacher label gone: no signal
    eng.sync_once()
    assert store.teacher(a) is None


def test_teacher_auto_needs_the_teacher_labels_and_off_ignores_them(town):
    eng, client, store, _ = town(town_labels=("orders", "travel"))  # e.g. your own two labels
    eng.sync_once()
    a = client.add_message(cls="orders", prob=0.9)
    client.user_add_label(a, client.label_id("orders"))
    eng.sync_once()
    assert store.teacher(a) is None
    eng, client, store, _ = town(learn_teacher="off")
    eng.sync_once()
    a = client.add_message(cls="orders", prob=0.9)
    client.user_add_label(a, client.label_id("orders"))
    eng.sync_once()
    assert store.teacher(a) is None
    eng, client, store, _ = town(town_labels=("orders", "travel"), learn_teacher="on")
    eng.sync_once()
    a = client.add_message(cls="orders", prob=0.9)
    client.user_add_label(a, client.label_id("orders"))
    eng.sync_once()
    assert store.teacher(a)["cls"] == "orders"


def _flip_head(model_id: str) -> learn.Artifact:
    """A head that turns the model's `orders` into `travel` (and nothing else), plus learned thresholds."""
    W, c = learn.identity()
    o, t = CLASSES.index("orders"), CLASSES.index("travel")
    W[[o, t], :] = W[[t, o], :]
    return learn.Artifact(
        base_model=model_id,
        created=NOW,
        active=True,
        head=learn.PersonalHead(W, c, "personal-test", model_id),
        policy={"promotional_threshold": 0.9},
    )


def test_engine_applies_and_reloads_the_head(town, tmp_path):
    eng, client, store, _ = town()
    eng.sync_once()
    learn.save(tmp_path / "personal.json", _flip_head("fake@1"))
    a = client.add_message(cls="orders", prob=0.9)
    eng.sync_once()
    d = store.decision(a)
    assert d["cls"] == "travel" and d["head_id"] == "personal-test"
    assert d["scores"][CLASSES.index("orders")] > d["scores"][CLASSES.index("travel")]  # base scores kept
    assert eng.cfg.promotional_threshold == 0.9 and eng.cfg.policy_sources(None, eng.artifact.policy)
    # reset (from another process): the next poll uses the model as shipped again
    learn.reset(store, tmp_path / "personal.json")
    b = client.add_message(cls="orders", prob=0.9)
    eng.sync_once()
    assert store.decision(b)["cls"] == "orders" and store.decision(b)["head_id"] is None
    assert eng.cfg.promotional_threshold == Config().promotional_threshold


def test_head_for_another_model_is_ignored(town, tmp_path):
    eng, client, store, _ = town()
    learn.save(tmp_path / "personal.json", _flip_head("other@9"))
    eng.sync_once()
    a = client.add_message(cls="orders", prob=0.9)
    eng.sync_once()
    assert store.decision(a)["cls"] == "orders" and eng.head is None


def test_without_learn_path_nothing_changes(town, tmp_path):
    eng, client, store, _ = town(learn_path=False)
    learn.save(tmp_path / "personal.json", _flip_head("fake@1"))
    eng.sync_once()
    a = client.add_message(cls="orders", prob=0.9)
    eng.sync_once()
    assert store.decision(a)["cls"] == "orders" and eng.maybe_learn() is None
    with pytest.raises(RuntimeError):
        eng.learn()


def test_scheduled_learning(town, tmp_path, caplog):
    eng, client, store, clock = town(learn_min_new_signals=5, learn_min_examples=3)
    eng.sync_once()
    assert eng.maybe_learn() is None  # nothing yet
    ids = [client.add_message(cls="orders", prob=0.9) for _ in range(6)]
    eng.sync_once()
    for m in ids:
        client.user_add_label(m, client.label_id("orders"))
    eng.sync_once()
    clock.t += 2 * 3600  # past the hourly re-check
    with caplog.at_level(logging.INFO, logger="email_town_labeler"):
        res = eng.maybe_learn()
    assert res is not None and res.examples["teacher"] == 6
    assert (tmp_path / "personal.json").exists() and store.get(learn.LAST_N) == "6"
    assert "made-up" not in caplog.text and "@example.test" not in caplog.text
    clock.t += 2 * 3600
    assert eng.maybe_learn() is None  # within interval_hours
    clock.t += 25 * 3600
    assert eng.maybe_learn() is None  # due, but no new signals
    eng2, *_ = town(learn_auto=False)
    assert eng2.maybe_learn() is None


def test_run_forever_survives_a_learning_failure(town, monkeypatch):
    eng, _client, _store, _ = town()
    monkeypatch.setattr(eng, "maybe_learn", lambda: (_ for _ in ()).throw(ValueError("boom")))
    n = {"polls": 0}
    import threading

    stop = threading.Event()
    real = eng.sync_once

    def once():
        n["polls"] += 1
        if n["polls"] >= 2:
            stop.set()
        return real()

    monkeypatch.setattr(eng, "sync_once", once)
    eng._sleep_fn = lambda s: None
    eng.run_forever(stop)
    assert n["polls"] == 2


def test_recovery_records_a_history_gap(town):
    eng, client, store, clock = town()
    eng.sync_once()
    client.expire_history()
    clock.t += 3600
    eng.sync_once()
    assert len(learn.history_gaps(store)) == 1


def test_end_to_end_learning_changes_labels(town, tmp_path):
    """Town keeps calling bulk 'promotional' mail newsletters; after learning, we label it newsletters."""
    eng, client, store, _clock = town(learn_min_examples=20)
    eng.sync_once()
    for i in range(80):
        bulk = i % 2 == 0
        h = {"list-id": "<list.example.test>"} if bulk else {}
        m = client.add_message(cls="promotional", prob=0.7, **h)
        eng.sync_once()
        client.user_add_label(m, client.label_id("newsletters" if bulk else "promotional"))
    eng.sync_once()
    res = eng.learn()
    assert res.artifact.active, res.artifact.reason
    m1 = client.add_message(cls="promotional", prob=0.7, **{"list-id": "<list.example.test>"})
    m2 = client.add_message(cls="promotional", prob=0.95)
    eng.sync_once()
    assert store.decision(m1)["cls"] == "newsletters"
    assert store.decision(m2)["cls"] == "promotional"
    nl = client.label_id(f"email-town-labeler/{DISPLAY_NAMES['newsletters']}")
    assert nl in client.messages[m1]["label_ids"]
    art = json.loads((tmp_path / "personal.json").read_text())
    assert art["active"] and art["base_model"] == "fake@1"
