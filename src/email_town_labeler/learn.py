"""Improving from use: a small personal head on the model's own scores, and re-tuned policy thresholds.

Works for every model, the default `town-modernbert` included, because it never touches the model: at
decision time the engine stores the model's log-probabilities over CLASSES (`scores`) and a few yes/no
features (`FEATURES`) next to the decision in state.sqlite. Learning reads only those numbers and the
signals below, so it needs no Gmail access, no mail content and no second pass of the model.

Signals (one per message; earlier kinds win):

    correction    you moved our label to another of ours          -> that class          correction_weight
    removal       you removed our label and added none of ours    -> "not that class"    removal_weight
    teacher       a teacher label (Town's, `[teacher_labels]`) on mail we decided -> its class
    teacher_none  the teacher was running but left the message unlabeled for teacher_wait_days -> unknown
    confirm       our label stayed untouched for confirm_days     -> that class (weak)   confirm_weight

Reading, archiving or starring mail is never used.

The personal head is multinomial logistic regression on x = [scores, features]:

    p = softmax(W x + c),   W0 = [I 0], c0 = 0   (W0, c0 reproduce the model exactly)
    minimise  sum_i w_i loss_i  +  prior_strength/2 * mean_i |(W - W0) x_i + c|^2

loss_i is cross-entropy for a class and -log(1 - p_k) for "not class k". The prior is on what the head does,
not on its weights: the mean squared change of the logits over the training messages, so it does not depend
on the inputs' scale or number. `prior_strength` acts like that many messages on which the model is right:
a few signals barely move it; many consistent ones do. A head is fit only with at least `min_examples` strong
signals (correction, removal, teacher) and is activated only if, on 5-fold out-of-fold predictions, it beats
the unchanged model: lower weighted log-loss, accuracy on strong signals no worse, accuracy on confirmed
labels at most 2 points worse, action-needed recall no worse. Otherwise the model is used as is.

The same out-of-fold probabilities re-tune `action_threshold` (the highest that keeps action-needed recall
at `action_recall_target`) and `promotional_threshold` (the lowest at which at most
`promo_important_max_rate` of mail labeled promotional is important: action needed, personal/unknown, or a
promotional label you removed). Moves toward safety (more action-needed recall, fewer promotional labels) are
taken at once; moves the other way need more evidence and are rate-limited. Keys set in config.toml win.

The result is one small JSON file next to state.sqlite (`personal.json`): weights, thresholds, counts and
scores. It is derived from your mail: keep it private.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .taxonomy import CLASSES, NEW_SENDER, UNKNOWN
from .types import EmailRecord, Prediction

log = logging.getLogger("email_town_labeler.learn")

FORMAT = 1
FILENAME = "personal.json"
FLOOR = -20.0  # log-probabilities are clipped here (p ~ 2e-9), in storage, training and use alike
DAY = 86_400.0
K = len(CLASSES)
A, PROMO, UNK = CLASSES.index("action_needed"), CLASSES.index("promotional"), CLASSES.index(UNKNOWN)
# yes/no facts the engine already has at decision time; none is mail content
FEATURES = [
    "known_contact",  # sender is someone you have written to
    "bulk",  # List-Unsubscribe or List-Id header present
    "primary",  # Gmail category: Primary
    "promotions",  # Gmail category: Promotions
    "social",
    "updates_tab",  # Gmail category: Updates
    "forums",
    "calendar",  # has a calendar invite
]
F = len(FEATURES)
_CATEGORY = {
    "primary": "CATEGORY_PERSONAL",
    "promotions": "CATEGORY_PROMOTIONS",
    "social": "CATEGORY_SOCIAL",
    "updates_tab": "CATEGORY_UPDATES",
    "forums": "CATEGORY_FORUMS",
}
STRONG = ("correction", "removal", "teacher")
KINDS = ("correction", "removal", "teacher", "teacher_none", "confirm")
FOLDS = 5
CONFIRM_TOLERANCE = 0.02
MIN_TUNE = 10  # positives of a class before its threshold may move toward safety
MIN_TUNE_RELAX = 30  # ...and before action_threshold may rise
MIN_TUNE_PROMO_RELAX = 20  # ...and before promotional_threshold may fall
ACT_GRID = np.round(np.arange(0.02, 0.5001, 0.01), 2)
PROMO_GRID = np.round(np.append(np.arange(0.5, 0.9901, 0.01), 1.0), 2)
ACT_MAX_RISE, PROMO_MAX_FALL = 0.05, 0.10
GAPS_KEY = "history_gaps"  # kv: [[start, end], ...] when history expired (teacher events may be missing)
LAST_TS, LAST_N, RESET_TS = "learn_last_ts", "learn_last_n", "learn_reset_ts"


# -- what the engine stores per decision ------------------------------------------------------------
def model_class(cls: str | None) -> str | None:
    """A label class as the model sees it: new_sender is the model's unknown; anything else not a class: None."""
    if cls == NEW_SENDER:
        return UNKNOWN
    return cls if cls in CLASSES else None


def base_scores(pred: Prediction) -> np.ndarray:
    """The model's log-probabilities over CLASSES, clipped at FLOOR (from its logits when it has them)."""
    z = None
    if pred.logits is not None and len(pred.logits) == K:
        z = np.asarray(pred.logits, dtype=np.float64)
        z = np.where(np.isfinite(z), z, -np.inf)
        if not np.isfinite(z).any():
            z = None
        else:
            m = z[np.isfinite(z)].max()
            z = z - (m + np.log(np.exp(z - m).sum()))
    if z is None:
        p = np.array([_num(pred.probs.get(c, 0.0)) for c in CLASSES])
        s = p.sum()
        p = p / s if s > 0 else np.eye(K)[UNK]
        with np.errstate(divide="ignore"):
            z = np.log(p)
    return np.maximum(np.nan_to_num(z, nan=FLOOR, neginf=FLOOR), FLOOR)


def features(rec: EmailRecord, *, known_contact: bool) -> list[float]:
    labs = set(rec.get("label_ids") or [])
    h = rec.get("headers") or {}
    vals = {
        "known_contact": known_contact,
        "bulk": bool(h.get("list-unsubscribe") or h.get("list-id")),
        "calendar": bool(rec.get("has_calendar")),
        **{k: lab in labs for k, lab in _CATEGORY.items()},
    }
    return [float(vals[k]) for k in FEATURES]


def _num(v: Any) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return f if math.isfinite(f) and f > 0 else 0.0


def softmax(Z: np.ndarray) -> np.ndarray:
    Z = Z - Z.max(axis=-1, keepdims=True)
    E = np.exp(Z)
    return E / E.sum(axis=-1, keepdims=True)


# -- the personal head ------------------------------------------------------------------------------
@dataclass
class PersonalHead:
    W: np.ndarray  # (K, K + F)
    c: np.ndarray  # (K,)
    id: str
    base_model: str

    def proba(self, scores: np.ndarray, feats: np.ndarray) -> np.ndarray:
        X = np.hstack([np.atleast_2d(scores), np.atleast_2d(feats)])
        return softmax(X @ self.W.T + self.c)

    def predictions(self, scores: Sequence[np.ndarray], feats: Sequence[Sequence[float]]) -> list[Prediction]:
        if not len(scores):
            return []
        P = self.proba(np.asarray(scores, dtype=np.float64), np.asarray(feats, dtype=np.float64))
        out = []
        for row in P:
            probs = {c: float(p) for c, p in zip(CLASSES, row)}
            best = max(CLASSES, key=lambda c: probs[c])
            out.append(Prediction(best, probs[best], probs))
        return out


@dataclass
class Artifact:
    """personal.json: what learning produced for one base model. No mail content."""

    base_model: str
    created: float
    active: bool = False
    head: PersonalHead | None = None
    policy: dict[str, float] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    signals: dict[str, int] = field(default_factory=dict)
    reason: str = ""

    @property
    def active_head(self) -> PersonalHead | None:
        return self.head if self.active else None

    def to_json(self) -> dict:
        d: dict[str, Any] = {
            "format": FORMAT,
            "base_model": self.base_model,
            "created": self.created,
            "classes": list(CLASSES),
            "features": list(FEATURES),
            "active": bool(self.active and self.head is not None),
            "policy": self.policy,
            "metrics": self.metrics,
            "signals": self.signals,
            "reason": self.reason,
        }
        if self.head is not None:
            d["head"] = {
                "id": self.head.id,
                "W": self.head.W.round(6).tolist(),
                "c": self.head.c.round(6).tolist(),
            }
        return d

    @classmethod
    def from_json(cls, d: Mapping[str, Any]) -> Artifact:
        if d.get("format") != FORMAT or list(d.get("classes") or []) != CLASSES:
            raise ValueError("personal.json: unknown format or class list")
        if list(d.get("features") or []) != FEATURES:
            raise ValueError("personal.json: made with a different feature list")
        head = None
        if d.get("head"):
            W = np.asarray(d["head"]["W"], dtype=np.float64)
            c = np.asarray(d["head"]["c"], dtype=np.float64)
            if (
                W.shape != (K, K + F)
                or c.shape != (K,)
                or not (np.isfinite(W).all() and np.isfinite(c).all())
            ):
                raise ValueError("personal.json: head has the wrong shape")
            head = PersonalHead(W, c, str(d["head"].get("id", "personal")), str(d["base_model"]))
        return cls(
            base_model=str(d["base_model"]),
            created=float(d.get("created") or 0),
            active=bool(d.get("active")) and head is not None,
            head=head,
            policy=dict(d.get("policy") or {}),
            metrics=dict(d.get("metrics") or {}),
            signals={k: int(v) for k, v in (d.get("signals") or {}).items()},
            reason=str(d.get("reason") or ""),
        )


def load(path: Path, model_id: str | None = None) -> Artifact | None:
    """The artifact at `path` (for `model_id`, when given), or None if absent, unreadable or for another model."""
    try:
        art = Artifact.from_json(json.loads(Path(path).read_text(encoding="utf-8")))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError) as e:
        log.warning("ignoring %s: %s", Path(path).name, e)
        return None
    if model_id is not None and art.base_model != model_id:
        return None
    return art


def save(path: Path, art: Artifact) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(art.to_json(), indent=1, sort_keys=True) + "\n", encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:  # pragma: no cover - e.g. some Windows filesystems
        pass
    os.replace(tmp, path)
    return path


# -- examples ---------------------------------------------------------------------------------------
@dataclass
class Examples:
    X: np.ndarray  # (n, K + F): scores then features
    y: np.ndarray  # class index, or -1
    neg: np.ndarray  # "not this class" index, or -1
    w: np.ndarray
    kind: list[str]
    ids: list[str]

    def __len__(self) -> int:
        return len(self.kind)

    def take(self, idx: np.ndarray) -> Examples:
        return Examples(
            self.X[idx],
            self.y[idx],
            self.neg[idx],
            self.w[idx],
            [self.kind[i] for i in idx],
            [self.ids[i] for i in idx],
        )

    def counts(self) -> dict[str, int]:
        c = Counter(self.kind)
        return {k: c.get(k, 0) for k in KINDS}

    @property
    def n_strong(self) -> int:
        return sum(1 for k in self.kind if k in STRONG)


def history_gaps(store: Any) -> list[tuple[float, float]]:
    try:
        return [(float(a), float(b)) for a, b in json.loads(store.get(GAPS_KEY) or "[]")]
    except (ValueError, TypeError):
        return []


def add_history_gap(store: Any, start: float, end: float, keep: int = 50) -> None:
    gaps = history_gaps(store) + [(float(start), float(end))]
    store.set(GAPS_KEY, json.dumps(gaps[-keep:]))


def teacher_enabled(cfg: Any) -> bool:
    return cfg.learn_teacher != "off"


def collect(store: Any, model_id: str, cfg: Any, *, now: float | None = None) -> Examples:
    """Every usable signal for `model_id` since the last reset, newest `max_examples` decisions."""
    now = time.time() if now is None else now
    reset = _float(store.get(RESET_TS))
    rows = store.learning_rows(model_id, since_ts=reset, limit=cfg.learn_max_examples)
    use_teacher = teacher_enabled(cfg)
    t_latest = (
        max((r["ts"] for r in rows if r["t"] and r["t_cls"] is not None), default=None)
        if use_teacher
        else None
    )
    gaps = history_gaps(store)
    wait = cfg.learn_teacher_wait_days * DAY
    X, y, neg, w, kind, ids = [], [], [], [], [], []

    def add(r: dict, k: str, weight: float, pos: str | None = None, not_cls: str | None = None) -> None:
        if weight <= 0:
            return
        z, f = r["scores"], r["feats"]
        if len(z) != K or len(f) != F:
            return
        X.append(list(z) + list(f))
        y.append(CLASSES.index(pos) if pos else -1)
        neg.append(CLASSES.index(not_cls) if not_cls else -1)
        w.append(weight)
        kind.append(k)
        ids.append(r["msg_id"])

    for r in rows:
        if r["fb"]:
            if r["fb_corrected"] is not None:
                c = model_class(r["fb_corrected"])
                if c:
                    add(r, "correction", cfg.learn_correction_weight, pos=c)
            else:
                was = r["fb_predicted"]
                if was != NEW_SENDER and model_class(was):  # "not new sender" says nothing about a class
                    add(r, "removal", cfg.learn_removal_weight, not_cls=was)
            continue
        if use_teacher and r["t"]:
            c = model_class(r["t_cls"])
            if c:
                add(r, "teacher", cfg.learn_teacher_weight, pos=c)
            continue  # a teacher label with no class (awaiting reply): no signal either way
        age = now - r["ts"]
        if (
            t_latest is not None
            and age >= wait
            and r["ts"] < t_latest
            and not any(a - wait <= r["ts"] <= b for a, b in gaps)
        ):
            add(r, "teacher_none", cfg.learn_teacher_none_weight, pos=UNKNOWN)
            continue
        c = model_class(r["cls"])
        if (
            c
            and r["applied"]
            and not r["dry_run"]
            and r["current"] == r["cls"]
            and age >= cfg.learn_confirm_days * DAY
        ):
            add(r, "confirm", cfg.learn_confirm_weight, pos=c)
    return Examples(
        np.asarray(X, dtype=np.float64).reshape(-1, K + F),
        np.asarray(y, dtype=np.int64),
        np.asarray(neg, dtype=np.int64),
        np.asarray(w, dtype=np.float64),
        kind,
        ids,
    )


def _float(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


# -- fitting ----------------------------------------------------------------------------------------
def identity() -> tuple[np.ndarray, np.ndarray]:
    return np.hstack([np.eye(K), np.zeros((K, F))]), np.zeros(K)


def prior_metric(X: np.ndarray, eps: float = 1e-4) -> np.ndarray:
    """E[x~ x~^T] over the training inputs, x~ = [x, 1] (plus eps I): the prior's metric. With it,
    |dTheta|^2_Sigma is the mean squared change of the logits over those messages."""
    Xt = np.hstack([X, np.ones((len(X), 1))])
    return Xt.T @ Xt / max(len(X), 1) + eps * np.eye(Xt.shape[1])


def _loss_grad(
    T: np.ndarray, ex: Examples, lam: float, T0: np.ndarray, Sigma: np.ndarray, need_grad: bool = True
) -> tuple[float, np.ndarray | None]:
    """T = [W c] (K, D+1). Weighted loss + lam/2 tr((T - T0) Sigma (T - T0)^T); and its gradient."""
    W, c = T[:, :-1], T[:, -1]
    P = softmax(ex.X @ W.T + c)
    n = len(ex)
    idx = np.arange(n)
    pos, negm = ex.y >= 0, ex.neg >= 0
    loss = np.zeros(n)
    loss[pos] = -np.log(np.maximum(P[idx[pos], ex.y[pos]], 1e-300))
    pk = np.minimum(P[idx[negm], ex.neg[negm]], 1 - 1e-12)
    loss[negm] = -np.log1p(-pk)
    D = T - T0
    DS = D @ Sigma
    total = float((ex.w * loss).sum() + 0.5 * lam * (DS * D).sum())
    if not need_grad:
        return total, None
    G = np.zeros_like(P)
    G[pos] = P[pos]
    G[idx[pos], ex.y[pos]] -= 1.0
    if negm.any():
        onehot = np.zeros((int(negm.sum()), K))
        onehot[np.arange(len(pk)), ex.neg[negm]] = 1.0
        G[negm] = (pk / (1 - pk))[:, None] * (onehot - P[negm])
    G *= ex.w[:, None]
    return total, np.hstack([G.T @ ex.X, G.sum(axis=0)[:, None]]) + lam * DS


def fit(
    ex: Examples,
    *,
    lam: float,
    iters: int = 400,
    tol: float = 1e-7,
    rel_tol: float = 1e-7,
    init: tuple[np.ndarray, np.ndarray] | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Minimise the weighted loss + lam/2 * mean squared logit change vs the unchanged model (accelerated
    gradient with backtracking and restarts). Starts at `init` (default: the unchanged model)."""
    W0, c0 = identity()
    if not len(ex):
        return W0, c0
    T0 = np.hstack([W0, c0[:, None]])
    Sigma = prior_metric(ex.X)
    x = T0.copy() if init is None else np.hstack([init[0], init[1][:, None]])
    yv = x.copy()
    fx = _loss_grad(x, ex, lam, T0, Sigma, need_grad=False)[0]
    L, t, stall = 1.0, 1.0, 0
    for _ in range(iters):
        fy, g = _loss_grad(yv, ex, lam, T0, Sigma)
        assert g is not None
        g2 = float((g**2).sum())
        if g2 < tol**2:
            x = yv
            break
        while True:
            xn = yv - g / L
            fn = _loss_grad(xn, ex, lam, T0, Sigma, need_grad=False)[0]
            if fn <= fy - 0.5 * g2 / L + 1e-12 * max(1.0, abs(fy)) or L > 1e12:
                break
            L *= 2.0
        if fn > fx:  # momentum overshot: restart from the last good point
            yv, t = x.copy(), 1.0
            continue
        t_next = (1 + math.sqrt(1 + 4 * t * t)) / 2
        step = math.sqrt(float(((xn - x) ** 2).sum()))
        yv = xn + ((t - 1) / t_next) * (xn - x)
        stall = stall + 1 if fx - fn <= rel_tol * max(1.0, abs(fx)) else 0
        x, fx, t = xn, fn, t_next
        L = max(L * 0.9, 1e-6)
        if step < tol or stall >= 5:  # converged, or the objective stopped moving
            break
    return x[:, :-1], x[:, -1]


# -- evaluation -------------------------------------------------------------------------------------
def _fold(msg_id: str) -> int:
    return int(hashlib.sha256(msg_id.encode("utf-8")).hexdigest()[:8], 16) % FOLDS


def out_of_fold(ex: Examples, lam: float, init: tuple[np.ndarray, np.ndarray] | None = None) -> np.ndarray:
    """Each example's head probabilities from a head fit without its fold (warm-started at `init`)."""
    P = np.zeros((len(ex), K))
    folds = np.array([_fold(i) for i in ex.ids], dtype=np.int64)
    for k in range(FOLDS):
        test = np.flatnonzero(folds == k)
        if not len(test):
            continue
        W, c = fit(ex.take(np.flatnonzero(folds != k)), lam=lam, init=init)
        P[test] = softmax(ex.X[test] @ W.T + c)
    return P


def base_proba(ex: Examples) -> np.ndarray:
    return softmax(ex.X[:, :K])


def metrics(P: np.ndarray, ex: Examples, *, action_threshold: float) -> dict[str, Any]:
    n = len(ex)
    idx = np.arange(n)
    pos, negm = ex.y >= 0, ex.neg >= 0
    loss = np.zeros(n)
    loss[pos] = -np.log(np.maximum(P[idx[pos], ex.y[pos]], 1e-12))
    loss[negm] = -np.log(np.maximum(1 - P[idx[negm], ex.neg[negm]], 1e-12))
    top = P.argmax(axis=1)
    right = np.where(pos, top == ex.y, np.where(negm, top != ex.neg, False))
    kinds = np.array(ex.kind)
    strong = np.isin(kinds, STRONG)
    conf = kinds == "confirm"
    act = ex.y == A
    out: dict[str, Any] = {
        "n": n,
        "log_loss": float((ex.w * loss).sum() / max(ex.w.sum(), 1e-12)),
        "acc_strong": float(right[strong].mean()) if strong.any() else None,
        "n_strong": int(strong.sum()),
        "acc_confirm": float(right[conf].mean()) if conf.any() else None,
        "n_confirm": int(conf.sum()),
        "action_recall": float((P[act, A] >= action_threshold).mean()) if act.any() else None,
        "n_action": int(act.sum()),
    }
    return out


def better(head: dict, base: dict, *, recall_target: float = 1.0) -> tuple[bool, str]:
    """Whether the head's out-of-fold metrics beat the unchanged model's; and why not.

    Action-needed recall (each at its own re-tuned threshold) must reach the target, or the model's own
    recall when that is lower."""
    if not head["log_loss"] < base["log_loss"]:
        return False, f"held-out log-loss not better ({head['log_loss']:.4f} vs {base['log_loss']:.4f})"
    if head["acc_strong"] is not None and head["acc_strong"] < base["acc_strong"]:
        return False, f"held-out accuracy worse ({head['acc_strong']:.3f} vs {base['acc_strong']:.3f})"
    if head["acc_confirm"] is not None and head["acc_confirm"] < base["acc_confirm"] - CONFIRM_TOLERANCE:
        return False, f"changes too many kept labels ({head['acc_confirm']:.3f} vs {base['acc_confirm']:.3f})"
    if head["action_recall"] is not None and head["action_recall"] < min(
        base["action_recall"], recall_target
    ):
        return (
            False,
            f"action needed recall worse ({head['action_recall']:.3f} vs {base['action_recall']:.3f})",
        )
    return True, "better than the model on held-out signals"


# -- thresholds -------------------------------------------------------------------------------------
def tune_policy(
    P: np.ndarray,
    ex: Examples,
    *,
    current: Mapping[str, float],
    cfg: Any,
) -> tuple[dict[str, float], dict[str, Any]]:
    """New (action_threshold, promotional_threshold) from out-of-fold probabilities; and what was seen."""
    out = dict(current)
    info: dict[str, Any] = {}
    w = ex.w
    # action needed: the highest threshold that keeps recall at the target. Recall is counted per message
    # (hits / all action-needed signals): kept labels are the hits, corrections the misses. Kept labels all
    # sit above the current threshold, so raising it also needs unbiased evidence (corrections, teacher).
    kinds = np.array(ex.kind)
    act = ex.y == A
    n_act = int(act.sum())
    n_act_unbiased = int((act & (kinds != "confirm")).sum())
    info["n_action"], info["n_action_unbiased"] = n_act, n_act_unbiased
    t_cur = float(current["action_threshold"])
    if not cfg.is_explicit("action_threshold") and n_act >= MIN_TUNE:
        rec = np.array([(P[act, A] >= t).mean() for t in ACT_GRID])
        ok = ACT_GRID[rec >= cfg.learn_action_recall_target]
        t_best = float(ok.max()) if len(ok) else float(ACT_GRID[0])
        info["action_threshold_fit"] = t_best
        if t_best < t_cur:
            out["action_threshold"] = t_best
        elif t_best > t_cur and n_act >= MIN_TUNE_RELAX and n_act_unbiased >= MIN_TUNE:
            out["action_threshold"] = round(min(t_best, t_cur + ACT_MAX_RISE), 2)
    # promotional: the lowest threshold at which (almost) nothing important is labeled promotional
    t_act = out["action_threshold"]
    others = P.copy()
    others[:, A] = -1.0
    top_promo = others.argmax(axis=1) == PROMO
    known = ex.X[:, K + FEATURES.index("known_contact")] > 0.5
    guard = known & bool(cfg.protect_known_contacts)
    important = (ex.y == A) | (ex.y == UNK) | (ex.neg == PROMO)
    n_promo = int((ex.y == PROMO).sum())
    n_promo_unbiased = int(((ex.y == PROMO) & (kinds != "confirm")).sum())
    info["n_promotional"], info["n_promotional_unbiased"] = n_promo, n_promo_unbiased
    p_cur = float(current["promotional_threshold"])
    if not cfg.is_explicit("promotional_threshold"):
        base_mask = (P[:, A] < t_act) & top_promo & ~guard

        def rate(t: float) -> tuple[float, float]:
            lab = base_mask & (P[:, PROMO] >= max(t, cfg.min_confidence))
            tot = float(w[lab].sum())
            bad = float(w[lab & important].sum())
            return (bad / tot if tot else 0.0), bad

        ok = [t for t in PROMO_GRID if rate(t)[0] <= cfg.learn_promo_important_max_rate]
        t_best = float(min(ok)) if ok else 1.0
        info["promotional_threshold_fit"] = t_best
        info["important_in_promotional_now"] = rate(p_cur)[1]
        if t_best > p_cur and rate(p_cur)[1] > 0:  # important mail is being labeled promotional: raise now
            out["promotional_threshold"] = t_best
        elif t_best < p_cur and n_promo >= MIN_TUNE_PROMO_RELAX and n_promo_unbiased >= MIN_TUNE:
            out["promotional_threshold"] = round(max(t_best, p_cur - PROMO_MAX_FALL), 2)
    return {k: round(float(v), 2) for k, v in out.items()}, info


# -- one learning run -------------------------------------------------------------------------------
@dataclass
class Result:
    artifact: Artifact
    examples: dict[str, int]
    changed_policy: dict[str, tuple[float, float]]  # key -> (before, after)


def run(
    store: Any,
    model_id: str,
    cfg: Any,
    *,
    current_policy: Mapping[str, float],
    base_policy: Mapping[str, float],
    now: float | None = None,
) -> Result:
    """Collect signals, fit and check a head, re-tune thresholds. Pure: the caller saves the artifact.

    `current_policy`: thresholds in effect now (learned ones included); `base_policy`: what they would be
    without learning (config, else the model's recommendation, else the default)."""
    now = time.time() if now is None else now
    ex = collect(store, model_id, cfg, now=now)
    counts = ex.counts()
    art = Artifact(base_model=model_id, created=now, signals=counts)
    lam = cfg.learn_prior_strength
    tune = cfg.learn_tune_policy and len(ex) > 0

    def tuned(P: np.ndarray) -> tuple[dict[str, float], dict[str, Any]]:
        if tune:
            return tune_policy(P, ex, current=current_policy, cfg=cfg)
        return {k: round(float(v), 2) for k, v in current_policy.items()}, {}

    P_use = base_proba(ex)
    policy, info = tuned(P_use)
    if ex.n_strong < cfg.learn_min_examples:
        art.reason = (
            f"collecting signals: {ex.n_strong} of {cfg.learn_min_examples} corrections/teacher labels"
        )
    else:
        # each side is scored with the thresholds it would run with (re-tuned on its own probabilities)
        W, c = fit(ex, lam=lam)
        P_head = out_of_fold(ex, lam, init=(W, c))
        pol_head, info_head = tuned(P_head)
        m_head = metrics(P_head, ex, action_threshold=pol_head["action_threshold"])
        m_base = metrics(P_use, ex, action_threshold=policy["action_threshold"])
        ok, why = better(m_head, m_base, recall_target=cfg.learn_action_recall_target)
        art.metrics = {"heldout_head": m_head, "heldout_base": m_base, "prior_strength": lam}
        art.reason = why
        digest = hashlib.sha256(np.round(np.hstack([W.ravel(), c]), 6).tobytes()).hexdigest()[:10]
        art.head = PersonalHead(W, c, f"personal-{digest}", model_id)
        art.active = ok
        art.metrics["shift_l2"] = float(np.sqrt(((W - identity()[0]) ** 2).sum() + (c**2).sum()))
        if ok:
            policy, info = pol_head, info_head
    changed: dict[str, tuple[float, float]] = {}
    if tune:
        art.metrics["policy"] = info
        for k, v in policy.items():
            if v != round(float(base_policy[k]), 2):
                art.policy[k] = v
            if v != round(float(current_policy[k]), 2):
                changed[k] = (float(current_policy[k]), v)
    return Result(art, counts, changed)


def due(store: Any, model_id: str, cfg: Any, *, now: float | None = None) -> tuple[bool, int]:
    """Whether the scheduled run is due (interval passed and enough new signals); and the signal count."""
    now = time.time() if now is None else now
    last = _float(store.get(LAST_TS))
    if last is not None and now - last < cfg.learn_interval_hours * 3600:
        return False, -1
    n = len(collect(store, model_id, cfg, now=now))
    prev = int(_float(store.get(LAST_N)) or 0)
    return n - prev >= max(cfg.learn_min_new_signals, 1), n


def mark_run(store: Any, n: int, *, now: float | None = None) -> None:
    store.set(LAST_TS, repr(time.time() if now is None else now))
    store.set(LAST_N, str(int(n)))


def reset(store: Any, path: Path, *, now: float | None = None) -> bool:
    """Forget the personal head and learned thresholds; later runs use only signals from after now."""
    now = time.time() if now is None else now
    existed = Path(path).exists()
    if existed:
        Path(path).unlink()
    store.set(RESET_TS, repr(now))
    store.set(LAST_N, "0")
    store.delete(LAST_TS)
    return existed


def thresholds(
    cfg: Any, manifest: Any = None, learned: Mapping[str, float] | None = None
) -> dict[str, float]:
    return {k: v for k, (v, _) in cfg.policy_sources(manifest, dict(learned or {})).items()}


def learn_once(
    store: Any,
    path: Path,
    model_id: str,
    cfg: Any,
    manifest: Any = None,
    *,
    now: float | None = None,
    save_it: bool = True,
) -> Result:
    """One run for `model_id`: thresholds from config.toml + the previous artifact + the model; save unless told not to.

    `cfg` is the config as loaded (not one that `for_model` already filled in)."""
    now = time.time() if now is None else now
    prev = load(path, model_id)
    res = run(
        store,
        model_id,
        cfg,
        current_policy=thresholds(cfg, manifest, prev.policy if prev else None),
        base_policy=thresholds(cfg, manifest),
        now=now,
    )
    if save_it:
        save(path, res.artifact)
        mark_run(store, sum(res.examples.values()), now=now)
    a = res.artifact
    log.info(
        "learn%s: signals %s; head %s (%s); thresholds %s",
        "" if save_it else " [dry-run]",
        " ".join(f"{k}={v}" for k, v in res.examples.items()),
        a.head.id if a.active_head else "inactive",
        a.reason,
        ", ".join(f"{k} {b:g}->{n:g}" for k, (b, n) in res.changed_policy.items()) or "unchanged",
    )
    return res
