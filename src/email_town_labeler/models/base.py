"""Model plug-in contract. A model package is a directory with `model.json` plus its files."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from ..taxonomy import CLASSES
from ..types import EmailRecord, Prediction

MANIFEST = "model.json"


@dataclass
class ModelManifest:
    name: str
    version: str
    backend: str  # "rules" | "embedding_probe" | "onnx_classifier" | "hf_sequence_classifier" | ...
    classes: list[str] = field(default_factory=lambda: list(CLASSES))
    description: str = ""
    license: str = ""
    files: dict[str, str] = field(default_factory=dict)  # relative path -> sha256
    metrics: dict = field(default_factory=dict)  # held-out numbers, data card summary
    config: dict = field(default_factory=dict)  # backend-specific settings
    path: Path | None = None  # directory it was loaded from (not serialised)

    @property
    def id(self) -> str:
        return f"{self.name}@{self.version}"

    @classmethod
    def load(cls, directory: Path) -> ModelManifest:
        data = json.loads((Path(directory) / MANIFEST).read_text(encoding="utf-8"))
        known = {k: data[k] for k in cls.__dataclass_fields__ if k in data and k != "path"}
        return cls(**known, path=Path(directory))

    def save(self, directory: Path) -> Path:
        d = asdict(self)
        d.pop("path")
        out = Path(directory) / MANIFEST
        out.write_text(json.dumps(d, indent=2) + "\n", encoding="utf-8")
        return out


@runtime_checkable
class Classifier(Protocol):
    manifest: ModelManifest

    def predict(self, records: list[EmailRecord]) -> list[Prediction]:
        """One Prediction per record, same order. probs covers every class in CLASSES and sums to 1."""
        ...
