"""Helpers for model tests: made-up mail, a deterministic hash embedder, tiny head packages."""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np

from email_town_labeler.models.backends._common import sha256_file
from email_town_labeler.models.base import ModelManifest
from email_town_labeler.taxonomy import CLASSES


def rec(
    i: int = 0,
    *,
    subject: str = "hello",
    sender: str = "a@example.com",
    labels=("INBOX",),
    body: str = "made-up body",
    **headers: str,
) -> dict:
    h = {"from": sender, "subject": subject, **{k.replace("_", "-"): v for k, v in headers.items()}}
    return {
        "id": f"m{i}",
        "thread_id": f"t{i}",
        "label_ids": list(labels),
        "internal_date": 0,
        "history_id": "1",
        "size_estimate": 1,
        "snippet": "",
        "headers": h,
        "body_text": body,
        "body_source": "plain",
        "has_calendar": False,
        "attachments": [],
    }


class HashEmbedder:
    """Deterministic text -> unit vector (sha256-seeded). Counts calls."""

    def __init__(self, dim: int = 16):
        self.dim, self.calls = dim, 0

    def embed(self, texts):
        self.calls += 1
        out = []
        for t in texts:
            seed = int.from_bytes(hashlib.sha256(t.encode()).digest()[:8], "little")
            v = np.random.default_rng(seed).standard_normal(self.dim)
            out.append(v / np.linalg.norm(v))
        return np.array(out)


def head_package(
    d: Path,
    *,
    dim: int = 16,
    backend: str = "embedding_probe",
    name: str = "tiny",
    seed: int = 0,
    config: dict | None = None,
    extra_files: dict[str, bytes] | None = None,
    version: str = "1",
) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    np.save(d / "coef.npy", rng.standard_normal((len(CLASSES), dim)) * 0.5)
    np.save(d / "intercept.npy", rng.standard_normal(len(CLASSES)) * 0.1)
    for rel, data in (extra_files or {}).items():
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_bytes(data)
    files = {
        p.relative_to(d).as_posix(): sha256_file(p)
        for p in sorted(d.rglob("*"))
        if p.is_file() and p.name != "model.json"
    }
    cfg = {
        "head": {"coef": "coef.npy", "intercept": "intercept.npy"},
        "dim": dim,
        "embedder": {"repo": "example/none", "revision": "0"},
        **(config or {}),
    }
    ModelManifest(
        name=name, version=version, backend=backend, files=files, config=cfg, metrics={"accuracy": 0.5}
    ).save(d)
    return d
