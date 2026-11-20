"""User-priority labeling policy: how a model's probabilities become the one class we label. Pure, no I/O.

The decision for one message, in order (engine.py calls `decide`):

1. `apply_policy`: action_needed if p[action_needed] >= action_threshold (and only then); else the top
   other class, but promotional only if p[promotional] >= promotional_threshold (else the best class
   after it); and never promotional for a known contact (fall back to the best class that is neither
   promotional nor action_needed). A rule that lands on a class with probability 0 gives `unknown`.
   Bulk mail (mailing-list headers or Gmail's Promotions tab, `features.bulk_mail`) uses the lower of
   promotional_threshold and bulk_promotional_threshold: a model trained on one work inbox is unsure
   of consumer marketing (p 0.5-0.8), and with the bar at 0.8 that mail fell through to `unknown`.
2. `confident_class`: the chosen class must reach its confidence bar or it becomes `unknown`. The bar is
   `action_threshold` for action_needed and `min_confidence` for every other class. On bulk mail a bulk
   class (promotional, newsletters, updates) may instead reach it with the three classes' combined
   probability: "newsletters 0.46, promotional 0.45" is surely bulk, only unsure which kind.
3. `new_sender_class`: an `unknown` message in Gmail's Primary category from an address you have never
   written to becomes `new_sender` (when `[policy] new_sender` is on).
4. `unknown_action` (engine.py): an `unknown` result is labeled or only recorded. `new_sender` is always labeled.

Missing classes count as probability 0. Ties go to the model's own pick, then to the earlier class in
CLASSES, so every decision is deterministic.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Protocol

from .taxonomy import CLASSES, NEW_SENDER, UNKNOWN
from .types import Prediction

ACTION, PROMO = "action_needed", "promotional"
BULK_CLASSES = (PROMO, "newsletters", "updates")


class PolicyConfig(Protocol):
    action_threshold: float
    promotional_threshold: float
    bulk_promotional_threshold: float
    protect_known_contacts: bool
    new_sender: bool
    min_confidence: float


@dataclass(frozen=True)
class Decision:
    pred: Prediction  # after apply_policy: cls = policy class, prob = its probability, probs = the model's
    cls: str  # the final class: what gets labeled (or recorded)


def _clean(pred: Prediction) -> dict[str, float]:
    """Probabilities over CLASSES; missing, negative or non-finite values count as 0."""
    raw: Mapping[str, float] = pred.probs or ({pred.cls: pred.prob} if pred.cls in CLASSES else {})
    out: dict[str, float] = {}
    for c in CLASSES:
        try:
            v = float(raw.get(c, 0.0))
        except (TypeError, ValueError):
            v = 0.0
        out[c] = v if math.isfinite(v) and v > 0.0 else 0.0
    return out


def _best(probs: dict[str, float], exclude: Collection[str], prefer: str) -> str:
    cands = [c for c in CLASSES if c not in exclude]
    return max(cands, key=lambda c: (probs[c], c == prefer, -CLASSES.index(c)))


def apply_policy(
    pred: Prediction, *, known_contact: bool, cfg: PolicyConfig, bulk: bool = False
) -> Prediction:
    """Rule 1 above. Returns the chosen class with its own probability; `probs` stays the model's."""
    probs = _clean(pred)
    promo_bar = (
        min(cfg.promotional_threshold, cfg.bulk_promotional_threshold) if bulk else cfg.promotional_threshold
    )
    if max(probs.values()) <= 0.0:  # nothing to go on
        return Prediction(UNKNOWN, 0.0, dict(pred.probs))
    if probs[ACTION] >= cfg.action_threshold:
        cls = ACTION
    else:  # below its own threshold, action_needed is never picked by ranking either
        cls = _best(probs, (ACTION,), pred.cls)
        if cls == PROMO and probs[PROMO] < promo_bar:
            cls = _best(probs, (ACTION, PROMO), pred.cls)
    if cls == PROMO and known_contact and cfg.protect_known_contacts:
        cls = _best(probs, (PROMO, ACTION), pred.cls)
    if probs[cls] <= 0.0:  # e.g. the guard removed all the mass: nothing supports the fallback
        cls = UNKNOWN
    return Prediction(cls, probs[cls], dict(pred.probs))


def confident_class(pred: Prediction, cfg: PolicyConfig, *, bulk: bool = False) -> str:
    """pred.cls if pred.prob reaches its bar (action_threshold for action_needed, else min_confidence);
    on bulk mail a bulk class may reach min_confidence with the bulk classes' combined probability."""
    if pred.cls not in CLASSES:
        return UNKNOWN
    bar = cfg.action_threshold if pred.cls == ACTION else cfg.min_confidence
    if pred.prob >= bar:
        return pred.cls
    if bulk and pred.cls in BULK_CLASSES and pred.prob > 0.0:
        probs = _clean(pred)
        if sum(probs[c] for c in BULK_CLASSES) >= cfg.min_confidence:
            return pred.cls
    return UNKNOWN


def new_sender_class(cls: str, *, primary: bool, new_address: bool, cfg: PolicyConfig) -> str:
    """`unknown` + Primary + an address you never wrote to -> `new_sender` (if enabled)."""
    return NEW_SENDER if cls == UNKNOWN and primary and new_address and cfg.new_sender else cls


def decide(
    pred: Prediction,
    *,
    known_contact: bool,
    new_address: bool,
    primary: bool,
    cfg: PolicyConfig,
    bulk: bool = False,
) -> Decision:
    """The whole post-model decision. `known_contact` / `new_address` are both False when unknowable;
    `bulk` is `features.bulk_mail` of the message."""
    p = apply_policy(pred, known_contact=known_contact, cfg=cfg, bulk=bulk)
    cls = new_sender_class(
        confident_class(p, cfg, bulk=bulk), primary=primary, new_address=new_address, cfg=cfg
    )
    return Decision(p, cls)
