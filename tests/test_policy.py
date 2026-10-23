"""policy.py: pure decision rules. No I/O."""

from __future__ import annotations

import math

import pytest

from email_town_labeler.config import Config
from email_town_labeler.policy import apply_policy, confident_class, decide, new_sender_class
from email_town_labeler.taxonomy import CLASSES
from email_town_labeler.types import Prediction

CFG = Config()  # action 0.2, promotional 0.6, protect contacts, new_sender on, min_confidence 0.55


def P(cls: str | None = None, **probs: float) -> Prediction:
    """A prediction from keyword probabilities; cls defaults to the argmax, as a model reports it."""
    best = cls or max(probs, key=probs.get) if probs else (cls or "unknown")
    return Prediction(best, probs.get(best, 0.0), dict(probs))


def pol(pred: Prediction, known: bool = False, cfg: Config = CFG) -> tuple[str, float]:
    out = apply_policy(pred, known_contact=known, cfg=cfg)
    return out.cls, out.prob


# -- rule 1: action threshold --------------------------------------------------------------------
def test_action_wins_at_its_threshold_even_when_not_top():
    assert pol(P(orders=0.7, action_needed=0.2, updates=0.1)) == ("action_needed", 0.2)
    assert pol(P(orders=0.7, action_needed=0.19, updates=0.11))[0] == "orders"


def test_action_below_its_threshold_is_never_picked_by_ranking():
    pred = P(action_needed=0.45, updates=0.3, orders=0.25)
    assert pol(pred)[0] == "action_needed"
    assert pol(pred, cfg=Config(action_threshold=0.5)) == ("updates", 0.3)


def test_action_beats_a_confident_promotional_and_a_known_contact():
    assert pol(P(promotional=0.75, action_needed=0.25), known=True)[0] == "action_needed"


# -- rule 2: promotional threshold --------------------------------------------------------------
@pytest.mark.parametrize(
    "promo,want", [(0.6, "promotional"), (0.9, "promotional"), (0.59, "updates"), (0.4, "updates")]
)
def test_promotional_needs_its_threshold(promo, want):
    rest = 1 - promo
    assert pol(P(promotional=promo, updates=rest * 0.6, newsletters=rest * 0.4))[0] == want


def test_uncertain_promotional_falls_back_to_best_other_class_with_its_prob():
    cls, prob = pol(P(promotional=0.5, newsletters=0.3, updates=0.15, action_needed=0.05))
    assert (cls, prob) == ("newsletters", 0.3)


def test_thresholds_are_configurable():
    strict = Config(action_threshold=0.5, promotional_threshold=0.95)
    assert pol(P(orders=0.6, action_needed=0.4), cfg=strict)[0] == "orders"
    assert pol(P(promotional=0.9, updates=0.1), cfg=strict)[0] == "updates"
    loose = Config(promotional_threshold=0.0)
    assert pol(P(promotional=0.3, updates=0.25, orders=0.25, travel=0.2), cfg=loose)[0] == "promotional"


# -- rule 3: known-contact guard ------------------------------------------------------------------
def test_known_contact_is_never_promotional():
    pred = P(promotional=0.9, action_needed=0.05, updates=0.03, unknown=0.02)
    assert pol(pred, known=False)[0] == "promotional"
    assert pol(pred, known=True) == ("updates", 0.03)  # not action_needed, though it is second


def test_guard_off_by_config_and_irrelevant_for_other_classes():
    pred = P(promotional=0.9, updates=0.1)
    assert pol(pred, known=True, cfg=Config(protect_known_contacts=False))[0] == "promotional"
    assert pol(P(orders=0.9, promotional=0.1), known=True)[0] == "orders"


def test_guard_with_only_promotional_mass_falls_to_unknown():
    assert pol(P(promotional=1.0), known=True) == ("unknown", 0.0)  # every other class ties at 0


# -- edge cases -----------------------------------------------------------------------------------
def test_ties_prefer_the_models_pick_then_class_order():
    assert pol(P("travel", orders=0.4, travel=0.4, updates=0.2))[0] == "travel"
    assert pol(P("updates", orders=0.4, travel=0.4, updates=0.2))[0] == "orders"  # CLASSES order
    flat = {c: 1 / len(CLASSES) for c in CLASSES}  # action 0.143 < 0.2, promo 0.143 < 0.6
    assert pol(Prediction("unknown", flat["unknown"], flat))[0] == "unknown"
    assert pol(Prediction("promotional", flat["promotional"], flat))[0] == "newsletters"


def test_all_zero_and_garbage_probs_are_unknown():
    zero = {c: 0.0 for c in CLASSES}
    assert pol(Prediction("orders", 0.0, zero)) == ("unknown", 0.0)
    assert pol(Prediction("orders", 0.0, {"orders": math.nan, "travel": -1.0})) == ("unknown", 0.0)
    assert pol(Prediction("spam", 0.9, {"spam": 0.9})) == ("unknown", 0.0)  # not one of ours


def test_missing_classes_count_as_zero_and_empty_probs_use_the_top_class():
    assert pol(P(orders=0.8)) == ("orders", 0.8)
    assert pol(Prediction("travel", 0.7, {})) == ("travel", 0.7)
    assert pol(Prediction("promotional", 0.5, {}))[0] == "unknown"  # below 0.6, nothing else to pick


def test_model_probs_are_kept_untouched():
    probs = {"promotional": 0.5, "updates": 0.3, "extra": 0.2}
    out = apply_policy(Prediction("promotional", 0.5, probs), known_contact=False, cfg=CFG)
    assert out.probs == probs and out.probs is not probs and out.cls == "updates"


# -- confidence bar and new sender ----------------------------------------------------------------
def test_confidence_bar_is_action_threshold_for_action_and_min_confidence_otherwise():
    assert confident_class(Prediction("action_needed", 0.2), CFG) == "action_needed"
    assert confident_class(Prediction("action_needed", 0.19), CFG) == "unknown"
    assert confident_class(Prediction("orders", 0.55), CFG) == "orders"
    assert confident_class(Prediction("orders", 0.5), CFG) == "unknown"
    assert confident_class(Prediction("bogus", 0.99), CFG) == "unknown"


@pytest.mark.parametrize(
    "cls,primary,new,on,want",
    [
        ("unknown", True, True, True, "new_sender"),
        ("unknown", False, True, True, "unknown"),
        ("unknown", True, False, True, "unknown"),
        ("unknown", True, True, False, "unknown"),
        ("orders", True, True, True, "orders"),
    ],
)
def test_new_sender_needs_every_condition(cls, primary, new, on, want):
    cfg = Config(new_sender=on)
    assert new_sender_class(cls, primary=primary, new_address=new, cfg=cfg) == want


def test_decide_end_to_end():
    def d(pred, *, known=False, new=False, primary=True, cfg=CFG):
        return decide(pred, known_contact=known, new_address=new, primary=primary, cfg=cfg).cls

    assert d(P(action_needed=0.3, unknown=0.7)) == "action_needed"  # passes its own 0.2 bar
    assert d(P(orders=0.5, unknown=0.3, updates=0.2)) == "unknown"  # below min_confidence
    assert d(P(orders=0.5, unknown=0.3, updates=0.2), new=True) == "new_sender"
    assert d(P(orders=0.5, unknown=0.3, updates=0.2), new=True, primary=False) == "unknown"
    assert d(P(promotional=0.95, updates=0.05), known=True) == "unknown"  # guard, then the bar
    assert d(P(promotional=0.95, updates=0.05), new=True) == "promotional"  # content label wins
    assert d(P(orders=0.6, unknown=0.4), new=True) == "orders"
    out = decide(
        P(promotional=0.5, updates=0.5), known_contact=False, new_address=False, primary=True, cfg=CFG
    )
    assert out.pred.cls == "updates" and out.pred.prob == 0.5 and out.cls == "unknown"
