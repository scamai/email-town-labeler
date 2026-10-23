"""Refit a model's numpy softmax head on the user's own corrections.

Objective (pure numpy, accelerated gradient descent, strongly convex so it always converges):

    sum_i CE(softmax(W x_i + b), y_i) + lambda/2 |W - W0|^2 + 10 lambda/2 |b - b0|^2,  lambda = 1/strength

warm-started from the current head and anchored to the *shipped* head (W0, b0), so a handful of
corrections nudges the decision boundary near those emails instead of overwriting the model.
With L2-normalised embeddings one correction moves its own logits by at most ~2 * strength.
Re-personalising a `<name>-personal` model re-anchors to the original shipped head (kept as
base_coef.npy / base_intercept.npy), so feed it all corrections collected so far.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Sequence
from pathlib import Path

import numpy as np

from .features import email_text
from .models import hub
from .models.backends._common import Embedder, SoftmaxHead, package_file, sha256_file, softmax
from .models.base import ModelManifest
from .models.registry import get_backend
from .types import EmailRecord

HEAD_BACKENDS = ("embedding_probe", "onnx_classifier")
SUFFIX = "-personal"
BIAS_SCALE = 10.0  # the intercept shifts every email, so it is anchored 10x harder than the weights


def bump_version(v: str) -> str:
    head, _, last = str(v).rpartition(".")
    if last.isdigit():
        return f"{head}.{int(last) + 1}" if head else str(int(last) + 1)
    return f"{v}.1"


def fit_head(
    X: np.ndarray,
    y: np.ndarray,
    W0: np.ndarray,
    b0: np.ndarray,
    *,
    lam: float,
    bias_scale: float = BIAS_SCALE,
    W_init: np.ndarray | None = None,
    b_init: np.ndarray | None = None,
    iters: int = 500,
    tol: float = 1e-7,
) -> tuple[np.ndarray, np.ndarray]:
    """Minimise sum CE + lam/2 |W - W0|^2 + bias_scale*lam/2 |b - b0|^2 (Nesterov, strongly convex)."""
    X, W0, b0 = np.asarray(X, np.float64), np.asarray(W0, np.float64), np.asarray(b0, np.float64)
    if lam <= 0:
        raise ValueError("lam must be > 0")
    Y = np.eye(W0.shape[0])[y]
    lam_b = lam * bias_scale
    L = max(lam, lam_b) + 0.5 * float(((X**2).sum(axis=1) + 1.0).sum())  # Lipschitz bound of the gradient
    step, q = 1.0 / L, np.sqrt(min(lam, lam_b) / L)
    beta = (1 - q) / (1 + q)
    W = (W0 if W_init is None else np.asarray(W_init, np.float64)).copy()
    b = (b0 if b_init is None else np.asarray(b_init, np.float64)).copy()
    Wv, bv = W.copy(), b.copy()
    for _ in range(iters):
        R = softmax(X @ Wv.T + bv) - Y
        gW, gb = R.T @ X + lam * (Wv - W0), R.sum(axis=0) + lam_b * (bv - b0)
        Wn, bn = Wv - step * gW, bv - step * gb
        Wv, bv = Wn + beta * (Wn - W), bn + beta * (bn - b)
        W, b = Wn, bn
        if float(np.sqrt((gW**2).sum() + (gb**2).sum())) < tol:
            break
    return W, b


def personalize(
    model_dir: Path,
    examples: Sequence[tuple[EmailRecord, str]],
    out_dir: Path,
    *,
    strength: float = 1.0,
    embedder: Embedder | None = None,
    device: str = "cpu",
    iters: int = 500,
) -> ModelManifest:
    """Write `out_dir/<name>-personal/` (version + 1) with a head refit on `examples`; return its manifest.

    `examples` are (record, corrected class). `embedder` overrides the model's own (tests)."""
    man = ModelManifest.load(Path(model_dir))
    if man.backend not in HEAD_BACKENDS:
        raise ValueError(f"cannot personalise backend {man.backend!r}; only {', '.join(HEAD_BACKENDS)}")
    if not examples:
        raise ValueError("personalize needs at least one (record, class) example")
    if strength <= 0:
        raise ValueError("strength must be > 0")
    bad = sorted({c for _, c in examples if c not in man.classes})
    if bad:
        raise ValueError(f"examples use classes the model does not have: {bad}")

    src = Path(man.path or model_dir)
    head_cfg = dict(man.config.get("head") or {})
    cur = SoftmaxHead.load(man)
    pers = dict(man.config.get("personalization") or {})
    if pers.get("base_coef"):
        W0 = np.load(package_file(man, pers["base_coef"], "personalization"), allow_pickle=False)
        b0 = np.load(package_file(man, pers.get("base_intercept"), "personalization"), allow_pickle=False)
        base_id = pers.get("base_id", man.id)
    else:
        W0, b0, base_id = cur.coef, cur.intercept, man.id

    clf = get_backend(man.backend)(man, device=device, embedder=embedder)
    mbc = int(man.config.get("max_body_chars", 2000))
    X = np.asarray(clf.embed([email_text(r, max_body_chars=mbc) for r, _ in examples]), np.float64)
    y = np.array([man.classes.index(c) for _, c in examples])
    lam = 1.0 / strength
    W, b = fit_head(X, y, W0, b0, lam=lam, W_init=cur.coef, b_init=cur.intercept, iters=iters)
    acc = lambda W_, b_: float(((X @ W_.T + b_).argmax(axis=1) == y).mean())

    name = man.name if man.name.endswith(SUFFIX) else man.name + SUFFIX
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".build-{name}-", dir=out_dir))
    try:
        coef_f, icpt_f = head_cfg.get("coef", "coef.npy"), head_cfg.get("intercept", "intercept.npy")
        skip = {coef_f, icpt_f, "base_coef.npy", "base_intercept.npy"}
        for rel in man.files:
            if rel not in skip:
                (stage / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src / hub.safe_relpath(rel), stage / rel)
        np.save(stage / "coef.npy", W.astype(np.float32 if cur.coef.dtype == np.float32 else np.float64))
        np.save(stage / "intercept.npy", b)
        np.save(stage / "base_coef.npy", W0)
        np.save(stage / "base_intercept.npy", b0)
        counts = {c: int((y == i).sum()) for i, c in enumerate(man.classes) if (y == i).any()}
        config = dict(
            man.config,
            head={**head_cfg, "coef": "coef.npy", "intercept": "intercept.npy"},
            personalization={
                "base_id": base_id,
                "base_coef": "base_coef.npy",
                "base_intercept": "base_intercept.npy",
            },
        )
        files = {
            rel: sha256_file(stage / rel)
            for rel in sorted(
                {r for r in man.files if r not in skip}
                | {"coef.npy", "intercept.npy", "base_coef.npy", "base_intercept.npy"}
            )
        }
        base_metrics = man.metrics.get("base", man.metrics) if man.name.endswith(SUFFIX) else man.metrics
        new = ModelManifest(
            name=name,
            version=bump_version(man.version),
            backend=man.backend,
            classes=list(man.classes),
            description=f"{man.description} Personalised from {base_id} on the user's corrections.".strip(),
            license=man.license,
            files=files,
            config=config,
            metrics={
                "personalized": {
                    "n_examples": len(examples),
                    "class_counts": counts,
                    "strength": strength,
                    "lambda": lam,
                    "train_acc_before": acc(cur.coef, cur.intercept),
                    "train_acc_after": acc(W, b),
                    "from": man.id,
                    "base": base_id,
                    "coef_shift_l2": float(np.linalg.norm(W - W0)),
                },
                "base": base_metrics,
            },
        )
        new.save(stage)
        dest = hub.install_dir(stage, out_dir, name=name, source={"ref": "personalized", "from": man.id})
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return ModelManifest.load(dest)
