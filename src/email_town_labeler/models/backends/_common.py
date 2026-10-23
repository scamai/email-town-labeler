"""Shared pieces for model backends: the numpy softmax head, prediction packing, extras checks."""

from __future__ import annotations

import hashlib
import importlib
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from ...taxonomy import CLASSES, UNKNOWN
from ...types import Prediction
from ..base import ModelManifest

# Not on PyPI yet (the name there belongs to an unrelated project): extras install from GitHub.
SOURCE = "git+https://github.com/scamai/email-town-labeler"
EXTRAS = {
    "sentence_transformers": "embed",
    "torch": "embed",
    "transformers": "embed",  # comes with sentence-transformers
    "onnxruntime": "onnx",
    "tokenizers": "onnx",
}


class MissingExtraError(ImportError):
    """A backend needs an optional dependency group that is not installed."""

    def __init__(self, module: str, extra: str, backend: str = ""):
        self.module, self.extra = module, extra
        who = f"backend {backend!r} " if backend else ""
        super().__init__(
            f'{who}needs {module!r}: install it with  pip install "email-town-labeler[{extra}] @ {SOURCE}"'
            f'  (pipx: pipx install --force "email-town-labeler[{extra}] @ {SOURCE}")'
        )


def require(module: str, backend: str = "", extra: str | None = None) -> Any:
    """Import `module` or raise MissingExtraError naming the pip extra."""
    try:
        return importlib.import_module(module)
    except ImportError as e:
        raise MissingExtraError(module, extra or EXTRAS.get(module.split(".")[0], "embed"), backend) from e


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def package_file(manifest: ModelManifest, rel: object, what: str = "file") -> Path:
    """A file named by the manifest, guaranteed to stay inside the package directory (or HubError).

    Manifest paths are package-supplied: no absolute paths, no `..`, no drive letters, and no symlink
    that resolves outside the package.
    """
    from ..hub import HubError, safe_relpath

    if not isinstance(rel, str):
        raise HubError(f"model package {what} path must be a string, got {type(rel).__name__}")
    root = Path(manifest.path or ".").resolve()
    f = (root / safe_relpath(rel)).resolve()
    if f != root and root not in f.parents:
        raise HubError(f"model package {what} {rel!r} resolves outside the package directory")
    return f


def softmax(z: np.ndarray) -> np.ndarray:
    z = np.asarray(z, dtype=np.float64)
    z = z - z.max(axis=-1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=-1, keepdims=True)


def l2_normalize(x: np.ndarray, eps: float = 1e-12) -> np.ndarray:
    return x / np.maximum(np.linalg.norm(x, axis=-1, keepdims=True), eps)


class Embedder(Protocol):
    def embed(self, texts: Sequence[str]) -> np.ndarray:  # (n, d) float
        ...


class SoftmaxHead:
    """Multinomial logistic regression: softmax(X @ coef.T + intercept). Rows follow `classes`."""

    def __init__(self, coef: np.ndarray, intercept: np.ndarray, classes: Sequence[str]):
        coef, intercept, classes = (
            np.asarray(coef, np.float64),
            np.asarray(intercept, np.float64),
            list(classes),
        )
        if coef.ndim != 2 or coef.shape[0] != len(classes) or intercept.shape != (len(classes),):
            raise ValueError(
                f"head shape mismatch: coef {coef.shape}, intercept {intercept.shape}, {len(classes)} classes"
            )
        unknown = set(classes) - set(CLASSES)
        if unknown or len(set(classes)) != len(classes):
            raise ValueError(f"head classes must be distinct members of CLASSES, got extra {sorted(unknown)}")
        self.coef, self.intercept, self.classes = coef, intercept, classes

    @property
    def dim(self) -> int:
        return int(self.coef.shape[1])

    @classmethod
    def load(cls, manifest: ModelManifest) -> SoftmaxHead:
        head = manifest.config.get("head") or {}
        if not isinstance(head, dict):
            raise TypeError("config.head must be an object {coef, intercept}")
        coef = np.load(package_file(manifest, head.get("coef", "coef.npy"), "head"), allow_pickle=False)
        intercept = np.load(
            package_file(manifest, head.get("intercept", "intercept.npy"), "head"), allow_pickle=False
        )
        return cls(coef, intercept, manifest.classes)

    def proba(self, X: np.ndarray) -> np.ndarray:
        X = np.asarray(X, dtype=np.float64)
        if X.ndim != 2 or X.shape[1] != self.dim:
            raise ValueError(f"embeddings have shape {X.shape}, head expects (n, {self.dim})")
        return softmax(X @ self.coef.T + self.intercept)


def to_predictions(
    P: np.ndarray, classes: Sequence[str], logits: np.ndarray | None = None
) -> list[Prediction]:
    """Rows of probabilities over `classes` -> Predictions whose probs cover every class in CLASSES.

    `logits` (same shape as P), when given, are kept on each Prediction in CLASSES order (a class the
    model does not have gets -inf), so the engine can store them for learn.py."""
    out = []
    Z = None if logits is None else np.asarray(logits, dtype=np.float64)
    for i, row in enumerate(np.asarray(P, dtype=np.float64)):
        probs = {c: 0.0 for c in CLASSES}
        for c, p in zip(classes, row):
            probs[c] = float(p)
        s = sum(probs.values())
        probs = {c: p / s for c, p in probs.items()} if s > 0 else {c: float(c == UNKNOWN) for c in CLASSES}
        best = max(CLASSES, key=lambda c: probs[c])  # ties resolve in CLASSES order: deterministic
        z = None
        if Z is not None:
            by = dict(zip(classes, Z[i].tolist()))
            z = [float(by.get(c, float("-inf"))) for c in CLASSES]
        out.append(Prediction(cls=best, prob=probs[best], probs=probs, logits=z))
    return out


class HeadClassifier:
    """Base for backends that are `embedder -> SoftmaxHead`. personalize.py refits `head`."""

    manifest: ModelManifest
    head: SoftmaxHead

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        raise NotImplementedError

    def predict(self, records: list) -> list[Prediction]:
        from ...features import email_text

        records = list(records)
        if not records:
            return []
        mbc = int(self.manifest.config.get("max_body_chars", 2000))
        X = np.asarray(self.embed([email_text(r, max_body_chars=mbc) for r in records]), dtype=np.float64)
        h = self.head
        return to_predictions(h.proba(X), h.classes, logits=X @ h.coef.T + h.intercept)


# ------------------------------------------------- sequence classifiers (text -> logits, no head file)


def _int_setting(cfg: dict, key: str, default: int, lo: int, hi: int, backend: str) -> int:
    v = cfg.get(key, default)
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise ValueError(f"{backend} config.{key} must be an integer in [{lo}, {hi}], got {v!r}")
    return v


def seq_labels(manifest: ModelManifest, backend: str, n_outputs: int | None = None) -> list[str]:
    """config.labels (head index -> class) checked: a list or {"0": cls, ...} covering every class in CLASSES.

    Defaults to manifest.classes. `n_outputs` (the model's number of logits), when known, must match.
    """
    raw = manifest.config.get("labels", manifest.classes)
    if isinstance(raw, dict):
        try:
            idx = {int(k): v for k, v in raw.items()}
        except (TypeError, ValueError):
            raise ValueError(f"{backend} config.labels keys must be head indices 0..n-1") from None
        if sorted(idx) != list(range(len(idx))):
            raise ValueError(f"{backend} config.labels keys must be head indices 0..n-1")
        raw = [idx[i] for i in range(len(idx))]
    if not isinstance(raw, list) or not all(isinstance(c, str) for c in raw):
        raise TypeError(f"{backend} config.labels must be a list of class names (head index order)")
    if len(set(raw)) != len(raw) or set(raw) != set(CLASSES):
        missing, extra = sorted(set(CLASSES) - set(raw)), sorted(set(raw) - set(CLASSES))
        raise ValueError(
            f"{backend} config.labels must name every class in CLASSES exactly once "
            f"(missing {missing}, unknown {extra}, {len(raw)} entries)"
        )
    if n_outputs is not None and n_outputs != len(raw):
        raise ValueError(f"{backend}: the model has {n_outputs} outputs but config.labels has {len(raw)}")
    return list(raw)


def seq_settings(manifest: ModelManifest, backend: str) -> dict:
    """The settings shared by the sequence-classifier backends, validated."""
    cfg = manifest.config
    prompt = cfg.get("prompt", "")
    if not isinstance(prompt, str):
        raise TypeError(f"{backend} config.prompt must be a string")
    return {
        "max_length": _int_setting(cfg, "max_length", 512, 8, 8192, backend),
        "batch_size": _int_setting(cfg, "batch_size", 8, 1, 1024, backend),
        "threads": _int_setting(cfg, "threads", 0, 0, 1024, backend),
        "max_body_chars": _int_setting(cfg, "max_body_chars", 2000, 1, 1_000_000, backend),
        "prompt": prompt,
    }


def length_batches(lengths: Sequence[int], batch_size: int) -> list[list[int]]:
    """Indices grouped into batches of similar length (less padding); callers restore the order."""
    order = sorted(range(len(lengths)), key=lambda i: lengths[i])
    return [order[i : i + batch_size] for i in range(0, len(order), batch_size)]


class SequenceClassifier:
    """Base for backends whose model maps text straight to logits over `labels` (no separate head)."""

    manifest: ModelManifest
    labels: list[str]
    settings: dict

    def logits(self, texts: list[str]) -> np.ndarray:  # (n, len(labels))
        raise NotImplementedError

    def texts(self, records: list) -> list[str]:
        from ...features import email_text

        mbc, prompt = self.settings["max_body_chars"], self.settings["prompt"]
        return [prompt + email_text(r, max_body_chars=mbc) for r in records]

    def checked_logits(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, len(self.labels)))
        z = np.asarray(self.logits(texts), dtype=np.float64)
        if z.shape != (len(texts), len(self.labels)):
            raise ValueError(
                f"model returned logits of shape {z.shape}, expected ({len(texts)}, {len(self.labels)})"
            )
        return z

    def proba(self, texts: list[str]) -> np.ndarray:
        return softmax(self.checked_logits(texts))

    def predict(self, records: list) -> list[Prediction]:
        records = list(records)
        if not records:
            return []
        z = self.checked_logits(self.texts(records))
        return to_predictions(softmax(z), self.labels, logits=z)
