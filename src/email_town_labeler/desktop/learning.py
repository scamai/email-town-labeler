"""Learning from the categories you choose in the desktop workbench, with learn.py, on this device only.

Every model decision keeps the model's own scores over the classes and learn.FEATURES (numbers only, never
mail content) in learning.sqlite, keyed by the message's opaque key. A category you choose for a message
(workflow.sqlite) is a correction of that decision; a model category left alone for `confirm_days` is a weak
confirmation. Sender rules are not used: one rule would turn a single sender's mail into dozens of examples.

When enough corrections have arrived, learn.py fits a small personal head on top of the model and keeps it
only if it beats the unchanged model on held-out messages; it also re-tunes the action and promotional
thresholds. The result, personal.json, is applied to new decisions and, from the stored scores, to the mail
already listed, without running the model again. Both files are derived from your mail: keep them private.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import numpy as np

from .. import learn
from ..state import Store
from ..types import Prediction

log = logging.getLogger("email_town_labeler.desktop")

DB_NAME = "learning.sqlite"
RECHECK = 600.0  # seconds between "is a learning run due?" checks (each reads the stored decisions)


class Signals:
    """learn.py's view of the desktop: stored decisions, with your chosen categories as the corrections."""

    def __init__(self, store: Store, workflow):
        self.store, self.workflow = store, workflow

    def get(self, key, default=None):
        return self.store.get(key, default)

    def set(self, key, value) -> None:
        self.store.set(key, value)

    def delete(self, key) -> None:
        self.store.delete(key)

    def learning_rows(self, model_id: str, *, since_ts=None, limit: int = 20000) -> list[dict]:
        rows = self.store.learning_rows(model_id, since_ts=since_ts, limit=limit)
        chosen = self.workflow.read([r["msg_id"] for r in rows])
        for r in rows:
            category = chosen.get(r["msg_id"], {}).get("category")
            if category:  # read now, so Undo or choosing again is what counts, never a stale record
                r.update(fb=1, fb_predicted=r["cls"], fb_corrected=category, current=category)
        return rows


class Learner:
    def __init__(self, root: Path, model_id: str, cfg, workflow):
        self.model_id, self.cfg = model_id, cfg
        self.path = root / learn.FILENAME
        db = root / DB_NAME
        self.store = Store(db)
        db.chmod(0o600)
        self.signals = Signals(self.store, workflow)
        self.lock = threading.Lock()  # one learning run at a time
        self.next_check = 0.0
        self.artifact = learn.load(self.path, model_id)

    @property
    def head(self) -> learn.PersonalHead | None:
        return self.artifact.active_head if self.artifact else None

    @property
    def policy(self) -> dict[str, float] | None:
        return self.artifact.policy if self.artifact else None

    def stamp(self, base: str) -> str:
        """The classifier stamp with what was learned: a new head or new thresholds redo model categories.
        Nothing learned yet adds nothing, so updating the app alone reclassifies nothing."""
        policy = ",".join(f"{k}={v:g}" for k, v in sorted((self.policy or {}).items()))
        if self.head is None and not policy:
            return base
        return f"{base}|head {self.head.id if self.head else 'none'}|learned {policy or 'none'}"

    def predict(self, preds: list[Prediction], fulls: list[dict]):
        """The model's predictions through the personal head (if active), plus what to store for learning."""
        scores = [learn.base_scores(p) for p in preds]
        feats = [
            learn.features(full, known_contact=False) for full in fulls
        ]  # contacts: never across accounts
        head = self.head
        return (head.predictions(scores, feats) if head and preds else preds), scores, feats

    def record(self, key: str, cls: str, pred: Prediction, scores, feats, mail_ms) -> None:
        self.store.record_decision(
            key,
            cls,
            pred.prob,
            pred.probs,
            self.model_id,
            True,
            model_cls=pred.cls,
            scores=list(scores),
            feats=list(feats),
            head_id=self.head.id if self.head else None,
            mail_ts=mail_ms / 1000 if mail_ms else None,
        )

    def rescore(self, keys: list[str]) -> dict[str, tuple[Prediction, bool]]:
        """key -> (prediction from the stored scores, bulk mail?) for keys with a stored decision of this model."""
        out = {}
        for key in keys:
            row = self.store.decision(key)
            if (
                not row
                or row.get("model_id") != self.model_id
                or not row.get("scores")
                or not row.get("feats")
            ):
                continue
            z, f = np.asarray(row["scores"], dtype=np.float64), list(row["feats"])
            if len(z) != learn.K or len(f) != learn.F:
                continue
            head = self.head
            if head:
                pred = head.predictions([z], [f])[0]
            else:
                p = np.exp(z)
                probs = {c: float(v) for c, v in zip(learn.CLASSES, p / p.sum())}
                best = max(learn.CLASSES, key=lambda c: probs[c])
                pred = Prediction(best, probs[best], probs)
            names = learn.FEATURES
            bulk = bool(f[names.index("bulk")] or f[names.index("promotions")])  # = features.bulk_mail
            out[key] = (pred, bulk)
        return out

    def maybe_learn(self, manifest, *, now: float | None = None) -> bool:
        """The scheduled run (`[learn] auto`): True when it produced a different head or thresholds."""
        now = time.time() if now is None else now
        if not self.cfg.learn_auto or now < self.next_check or not self.lock.acquire(blocking=False):
            return False
        try:
            self.next_check = now + RECHECK
            due, _ = learn.due(self.signals, self.model_id, self.cfg, now=now)
            if not due:
                return False
            before = self.stamp("")
            result = learn.learn_once(self.signals, self.path, self.model_id, self.cfg, manifest, now=now)
            self.artifact = learn.load(self.path, self.model_id)
            log.info(
                "learned from your categories: %s; head %s (%s)",
                " ".join(f"{k}={v}" for k, v in result.examples.items()) or "no signals",
                self.head.id if self.head else "inactive",
                result.artifact.reason,
            )
            return self.stamp("") != before
        finally:
            self.lock.release()

    def close(self) -> None:
        self.store.close()
