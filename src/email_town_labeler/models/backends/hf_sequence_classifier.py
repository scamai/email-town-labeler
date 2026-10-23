"""A transformers sequence-classification model stored inside the package (needs the `[embed]` extra).

model.json `config`:
    model_dir: "hf"            directory in the package holding a `save_pretrained` checkpoint:
                               config.json, model.safetensors (safetensors only, never pickles) and the
                               tokenizer files. Loaded with local_files_only=True, trust_remote_code=False.
    labels: [cls, ...] | {"0": cls, ...}   head index -> class; must name every class in CLASSES once
                               (default: manifest.classes)
    max_length: 512            tokens; longer text is truncated
    dtype: "float32" | "bfloat16" | "float16"   compute dtype (default: float32 on CPU, bfloat16 on CUDA
                               when supported). Weights stored as bf16 are upcast on load.
    threads: 0                 torch intra-op threads (0 = torch default; note: process-wide)
    batch_size: 8, prompt: "", max_body_chars: 2000
Every package path goes through `package_file`, and no file in `model_dir` may resolve outside the package.
The model's softmax over its logits is the prediction. Nothing here logs texts.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from ..base import ModelManifest
from ._common import SequenceClassifier, length_batches, package_file, require, seq_labels, seq_settings

BACKEND = "hf_sequence_classifier"
DTYPES = ("float32", "bfloat16", "float16")
# config.json keys that would make transformers fetch or run code from elsewhere
REFUSED_CONFIG_KEYS = ("auto_map", "custom_pipelines")


def checkpoint_dir(manifest: ModelManifest) -> Path:
    """The package's checkpoint directory, with every file in it confined to the package (or HubError)."""
    from ..hub import HubError

    d = package_file(manifest, manifest.config.get("model_dir", "hf"), "model_dir")
    if not d.is_dir():
        raise HubError(f"{BACKEND}: model_dir {manifest.config.get('model_dir', 'hf')!r} is not a directory")
    root = Path(manifest.path or ".").resolve()
    for f in d.rglob("*"):
        r = f.resolve()
        if root not in r.parents:
            raise HubError(f"{BACKEND}: {f.name!r} in model_dir resolves outside the package directory")
    for name in ("config.json", "model.safetensors"):
        if not (d / name).is_file() and not (name == "model.safetensors" and _sharded(d)):
            raise HubError(f"{BACKEND}: model_dir has no {name}")
    cfg = json.loads((d / "config.json").read_text(encoding="utf-8"))
    bad = sorted(k for k in REFUSED_CONFIG_KEYS if k in cfg)
    if bad:
        raise HubError(f"{BACKEND}: config.json sets {bad}; a model package never loads code")
    return d


def _sharded(d: Path) -> bool:
    return (d / "model.safetensors.index.json").is_file()


def _dtype(torch, name: object, device: str):
    if name is None:
        if device.startswith("cuda") and torch.cuda.is_bf16_supported():
            return torch.bfloat16
        return torch.float32  # bf16 matmuls are slow or unsupported on many laptop CPUs
    if name not in DTYPES:
        raise ValueError(f"{BACKEND} config.dtype must be one of {list(DTYPES)}, got {name!r}")
    return getattr(torch, name)


def _dtype_kw(transformers) -> str:
    """transformers >= 4.56 spells it `dtype`; older releases only know `torch_dtype`."""
    try:
        major, minor = (int(x) for x in transformers.__version__.split(".")[:2])
    except ValueError:
        return "dtype"
    return "dtype" if (major, minor) >= (4, 56) else "torch_dtype"


class HFSequenceClassifier(SequenceClassifier):
    def __init__(
        self, manifest: ModelManifest, *, device: str = "cpu", attn_implementation: str | None = None
    ):
        self.manifest = manifest
        self.settings = seq_settings(manifest, BACKEND)
        d = checkpoint_dir(manifest)
        torch = require("torch", BACKEND)
        tf = require("transformers", BACKEND)
        if device.startswith("cuda") and not torch.cuda.is_available():
            raise RuntimeError("device 'cuda' requested but no CUDA device is available")
        self.torch, self.device = torch, device
        dtype = _dtype(torch, manifest.config.get("dtype"), device)
        if self.settings["threads"]:
            torch.set_num_threads(self.settings["threads"])
        quiet = getattr(getattr(tf, "utils", None), "logging", None)
        if quiet is not None and hasattr(quiet, "disable_progress_bar"):
            quiet.disable_progress_bar()  # no "Loading weights" bars in the service log
        safe = {"local_files_only": True, "trust_remote_code": False}
        self.tokenizer = tf.AutoTokenizer.from_pretrained(str(d), **safe)
        kw = {_dtype_kw(tf): dtype, "use_safetensors": True, **safe}
        if attn_implementation:  # caller-supplied (e.g. the ONNX exporter), never from the package
            kw["attn_implementation"] = attn_implementation
        self.model = tf.AutoModelForSequenceClassification.from_pretrained(str(d), **kw).to(device).eval()
        self.labels = seq_labels(manifest, BACKEND, int(self.model.config.num_labels))
        id2label = getattr(self.model.config, "id2label", None) or {}
        named = [id2label.get(i) for i in range(len(self.labels))]
        if set(named) == set(self.labels) and named != self.labels:
            raise ValueError(f"{BACKEND}: config.labels disagrees with the checkpoint's id2label order")

    def encode(self, texts: list[str]) -> dict:
        return self.tokenizer(
            texts,
            truncation=True,
            max_length=self.settings["max_length"],
            padding=True,
            return_tensors="pt",
        )

    def logits(self, texts: list[str]) -> np.ndarray:
        torch = self.torch
        lengths = [len(t) for t in texts]
        out = np.zeros((len(texts), len(self.labels)), dtype=np.float32)
        with torch.inference_mode():
            for idx in length_batches(lengths, self.settings["batch_size"]):
                enc = {k: v.to(self.device) for k, v in self.encode([texts[i] for i in idx]).items()}
                z = self.model(**enc).logits
                out[idx] = z.float().cpu().numpy()
        return out


def load(manifest: ModelManifest, *, device: str = "cpu", **kw) -> HFSequenceClassifier:
    return HFSequenceClassifier(manifest, device=device, attn_implementation=kw.get("attn_implementation"))
