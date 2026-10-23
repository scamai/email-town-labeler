"""Known, published model packages: a short name -> a pinned release archive (URL, sha256, size).

`town-modernbert` is the recommended default. Catalog downloads always verify the archive's sha256
before extracting it (and then every file against model.json, like any package).

`EMAIL_TOWN_LABELER_CATALOG_BASE_URL` replaces the URL prefix (e.g. `file:///path/to/dir/`), so tests,
CI and air-gapped installs never reach GitHub. It is an advanced/test override, not a config key.
"""

from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass
from pathlib import Path

from .base import MANIFEST, ModelManifest

RELEASE = "https://github.com/scamai/email-town-labeler/releases/download/v0.1.0/"
BASE_URL_ENV = "EMAIL_TOWN_LABELER_CATALOG_BASE_URL"
DEFAULT = "town-modernbert"
CARD_URL = "https://github.com/scamai/email-town-labeler/blob/main/docs/model-cards/town-modernbert.md"
EXTRA_MODULES = {"onnx": ("onnxruntime", "tokenizers"), "embed": ("torch", "transformers")}


@dataclass(frozen=True)
class CatalogEntry:
    key: str  # what you type: `model pull town-modernbert`, `[model] ref = "town-modernbert"`
    package: str  # model.json `name`, also the install directory under models_dir
    file: str  # release asset file name
    sha256: str  # of the archive
    size: int  # archive bytes
    extra: str  # pip extra the backend needs
    description: str

    @property
    def url(self) -> str:
        base = os.environ.get(BASE_URL_ENV, "").strip() or RELEASE
        return base.rstrip("/") + "/" + self.file


CATALOG: dict[str, CatalogEntry] = {
    e.key: e
    for e in (
        CatalogEntry(
            "town-modernbert",
            "town-labels-modernbert-large-onnx",
            "town-labels-modernbert-large-onnx-v1.tar.gz",
            "a08e7a0d80a714e23451da86f54ea6b6abd5ce936416496b2c61520b85f03641",
            823_760_697,
            "onnx",
            "ModernBERT-large 7-way classifier, ONNX (no PyTorch). The recommended default.",
        ),
        CatalogEntry(
            "town-modernbert-torch",
            "town-labels-modernbert-large",
            "town-labels-modernbert-large-v1.tar.gz",
            "be2ffd9e1b1b8dfa4b098d80679444628e13fc2ccbf0d9f15d155d85bc7f324d",
            634_765_924,
            "embed",
            "The same model as PyTorch bf16 weights (hf_sequence_classifier).",
        ),
    )
}


def get(key: str) -> CatalogEntry | None:
    return CATALOG.get(key.strip())


def extra_installed(extra: str) -> bool:
    """Whether the modules a pip extra provides are importable (without importing them)."""
    try:
        return all(importlib.util.find_spec(m) is not None for m in EXTRA_MODULES.get(extra, ()))
    except (ImportError, ValueError):
        return False


def installed(entry: CatalogEntry, models_dir: Path) -> Path | None:
    """The installed package directory for `entry`, if there is a valid one."""
    d = Path(models_dir) / entry.package
    if not (d / MANIFEST).is_file():
        return None
    try:
        return d if ModelManifest.load(d).name == entry.package else None
    except (ValueError, TypeError, OSError):
        return None


def fetch(entry: CatalogEntry, models_dir: Path, *, progress: bool = True) -> Path:
    """Download the pinned archive (sha256-checked before extraction) and install it."""
    from . import hub

    return hub.fetch_url(
        entry.url,
        models_dir,
        sha256=entry.sha256,
        progress=progress,
        source={"ref": entry.key, "catalog": entry.key, "url": entry.url, "sha256": entry.sha256},
    )


def human_size(n: float) -> str:
    for unit, scale in (("GB", 1e9), ("MB", 1e6), ("KB", 1e3)):
        if n >= scale:
            return f"{n / scale:.1f} {unit}" if unit == "GB" else f"{n / scale:.0f} {unit}"
    return f"{int(n)} B"
