"""Built-in no-ML baseline: Gmail's own CATEGORY_* labels plus a few header and subject rules.

Deterministic, no dependencies beyond numpy, works offline; the CI and first-run default.
Each rule adds evidence (a logit) to one class; probabilities are a tempered softmax, so they
are only roughly calibrated. Subjects are matched against fixed keyword lists and never stored.
"""

from __future__ import annotations

import re

import numpy as np

from ...taxonomy import CLASSES
from ...types import EmailRecord, Prediction
from ..base import ModelManifest
from ._common import softmax, to_predictions

ORDERS = re.compile(
    r"\b(receipts?|invoices?|orders?|order confirmation|shipped|shipping|shipment|delivered|"
    r"out for delivery|tracking number|payment received|your purchase|refund)\b",
    re.IGNORECASE,
)
TRAVEL = re.compile(
    r"\b(flights?|boarding pass|itinerary|check-?in|hotel|reservation|booking|trip|"
    r"e-?ticket|rental car|car rental|airbnb|stay at)\b",
    re.IGNORECASE,
)
PROMO = re.compile(
    r"(\d+\s?% off|\bsale\b|\bdeals?\b|\boffer\b|\bdiscount|\bfree shipping\b|\blimited time\b|"
    r"\bcoupon|\bpromo\b|\bexclusive\b|\bsave \$?\d+|\blast chance\b)",
    re.IGNORECASE,
)
NEWSLETTER = re.compile(
    r"\b(newsletter|digest|weekly|monthly|issue\s*#?\d+|edition|roundup|this week in)\b", re.IGNORECASE
)
UPDATES = re.compile(
    r"\b(security alert|sign-?in|password|verify|verification|account|notification|"
    r"statement|reminder|alert|has been|was successfully|welcome to|your .* is ready)\b",
    re.IGNORECASE,
)
ACTION = re.compile(
    r"\b(action required|please (review|sign|approve|confirm|respond|reply)|signature requested|"
    r"approval|rsvp|invitation:|asap|urgent|deadline|due (today|tomorrow)|can you|could you)\b",
    re.IGNORECASE,
)
NOREPLY = re.compile(
    r"(no-?reply|do-?not-?reply|donotreply|notifications?@|alerts?@|mailer-daemon|bounce)", re.IGNORECASE
)

TEMPERATURE = 1.2  # softens the summed rule evidence; probabilities are only roughly calibrated
BASE = {"unknown": 1.0}

MANIFEST = ModelManifest(
    name="rules",
    version="1",
    backend="rules",
    description="Built-in baseline: Gmail categories + header/subject keyword rules (no ML). "
    "Baseline; no published evaluation.",
    license="MIT",
    metrics={},  # baseline; no published evaluation
)


def logits(rec: EmailRecord) -> dict[str, float]:
    lids = set(rec.get("label_ids") or [])
    h = {k.lower(): v for k, v in (rec.get("headers") or {}).items()}
    subj = h.get("subject", "") or ""
    sender = h.get("from", "") or ""
    precedence = (h.get("precedence", "") or "").strip().lower()
    bulk = bool(h.get("list-unsubscribe") or h.get("list-id") or precedence in ("bulk", "list", "junk"))
    noreply = bool(NOREPLY.search(sender))
    promo_cat, updates_cat = "CATEGORY_PROMOTIONS" in lids, "CATEGORY_UPDATES" in lids
    s = {c: BASE.get(c, 0.0) for c in CLASSES}

    s["promotional"] += 3.0 * promo_cat + 0.6 * bulk + 2.0 * bool(PROMO.search(subj))
    s["updates"] += (
        3.0 * updates_cat
        + 2.0 * ("CATEGORY_SOCIAL" in lids)
        + 1.0 * ("CATEGORY_FORUMS" in lids)
        + 1.0 * noreply
        + 1.0 * bool(UPDATES.search(subj))
        + 2.0 * bool(rec.get("has_calendar"))
    )  # calendar invites and notices lean "updates"
    s["newsletters"] += 2.2 * (bulk and not promo_cat) + 1.5 * bool(NEWSLETTER.search(subj))
    s["orders"] += 4.5 * bool(ORDERS.search(subj))
    s["travel"] += 4.5 * bool(TRAVEL.search(subj))
    personal = not bulk and not noreply
    s["action_needed"] += 2.0 * (bool(ACTION.search(subj)) and personal) + 0.8 * (
        "CATEGORY_PERSONAL" in lids and personal
    )
    s["unknown"] += 1.0 * ("CATEGORY_PERSONAL" in lids and personal) + 0.8 * (
        personal
        and not lids & {"CATEGORY_PROMOTIONS", "CATEGORY_UPDATES", "CATEGORY_SOCIAL", "CATEGORY_FORUMS"}
    )
    return s


class RulesClassifier:
    def __init__(self, manifest: ModelManifest = MANIFEST):
        self.manifest = manifest

    def predict(self, records: list[EmailRecord]) -> list[Prediction]:
        if not records:
            return []
        Z = np.array([[logits(r)[c] for c in CLASSES] for r in records]) / TEMPERATURE
        return to_predictions(softmax(Z), CLASSES, logits=Z)


def load(manifest: ModelManifest | None = None, *, device: str = "cpu", **_: object) -> RulesClassifier:
    return RulesClassifier(manifest or MANIFEST)
