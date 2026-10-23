"""ONNX encoder + numpy softmax head via onnxruntime and tokenizers only (the `[onnx]` extra, no torch).

model.json `config`:
    onnx: "encoder.onnx"          graph inputs: any of input_ids / attention_mask / token_type_ids (int64)
    tokenizer: "tokenizer.json"   HF `tokenizers` file
    output: 0 | "<name>"          which graph output holds the hidden states / embedding
    pooling: "mean" | "cls" | "none"   "none" = output is already (batch, dim)
    normalize: bool, prompt: str, max_seq_length: int, batch_size: int, threads: int (0 = ort default)
    pad_id: int (optional; else the tokenizer's padding id, else 0)
    head: {coef, intercept}
Every path above is package-supplied and must stay inside the package directory (`package_file`).
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from ..base import ModelManifest
from ._common import Embedder, HeadClassifier, SoftmaxHead, l2_normalize, package_file, require

BACKEND = "onnx_classifier"
POOLINGS = ("mean", "cls", "none")


def pool(hidden: np.ndarray, mask: np.ndarray, mode: str) -> np.ndarray:
    """(batch, seq, dim) + (batch, seq) mask -> (batch, dim). `none` passes (batch, dim) through."""
    hidden = np.asarray(hidden, dtype=np.float32)
    if mode == "none":
        if hidden.ndim != 2:
            raise ValueError(f"pooling 'none' expects a (batch, dim) output, got {hidden.shape}")
        return hidden
    if hidden.ndim != 3:
        raise ValueError(f"pooling {mode!r} expects a (batch, seq, dim) output, got {hidden.shape}")
    if mode == "cls":
        return hidden[:, 0, :]
    if mode == "mean":
        m = np.asarray(mask, dtype=np.float32)[..., None]
        return (hidden * m).sum(axis=1) / np.maximum(m.sum(axis=1), 1e-9)
    raise ValueError(f"unknown pooling {mode!r}; expected one of {POOLINGS}")


class OnnxEncoder:
    def __init__(self, manifest: ModelManifest, device: str = "cpu"):
        cfg = manifest.config
        tok_file = package_file(manifest, cfg.get("tokenizer", "tokenizer.json"), "tokenizer")
        onnx_file = package_file(manifest, cfg.get("onnx", "encoder.onnx"), "onnx")
        ort = require("onnxruntime", BACKEND)
        tk = require("tokenizers", BACKEND)
        self.pooling = cfg.get("pooling", "mean")
        if self.pooling not in POOLINGS:
            raise ValueError(f"unknown pooling {self.pooling!r}; expected one of {POOLINGS}")
        self.normalize = bool(cfg.get("normalize", True))
        self.prompt = cfg.get("prompt") or ""
        self.batch_size = int(cfg.get("batch_size", 8))
        self.tok = tk.Tokenizer.from_file(str(tok_file))
        self.tok.enable_truncation(int(cfg.get("max_seq_length", 512)))
        pad = self.tok.padding or {}
        self.tok.enable_padding(
            pad_id=int(cfg.get("pad_id", pad.get("pad_id", 0))), pad_token=pad.get("pad_token", "[PAD]")
        )
        so = ort.SessionOptions()
        if cfg.get("threads"):
            so.intra_op_num_threads = int(cfg["threads"])
        so.log_severity_level = 3
        providers = (
            ["CUDAExecutionProvider", "CPUExecutionProvider"]
            if device.startswith("cuda")
            else ["CPUExecutionProvider"]
        )
        self.session = ort.InferenceSession(str(onnx_file), so, providers=providers)
        self.inputs = {i.name for i in self.session.get_inputs()}
        out = cfg.get("output", 0)
        names = [o.name for o in self.session.get_outputs()]
        self.output = names[out] if isinstance(out, int) else out
        if self.output not in names:
            raise ValueError(f"ONNX graph has no output {self.output!r}; outputs: {names}")

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        chunks = []
        for i in range(0, len(texts), self.batch_size):
            enc = self.tok.encode_batch([self.prompt + t for t in texts[i : i + self.batch_size]])
            ids = np.array([e.ids for e in enc], dtype=np.int64)
            mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
            feed = {"input_ids": ids, "attention_mask": mask, "token_type_ids": np.zeros_like(ids)}
            (h,) = self.session.run([self.output], {k: v for k, v in feed.items() if k in self.inputs})
            chunks.append(pool(h, mask, self.pooling))
        X = np.concatenate(chunks, axis=0)
        return l2_normalize(X) if self.normalize else X


class OnnxClassifier(HeadClassifier):
    def __init__(self, manifest: ModelManifest, *, device: str = "cpu", embedder: Embedder | None = None):
        self.manifest = manifest
        self.head = SoftmaxHead.load(manifest)
        self.embedder = embedder if embedder is not None else OnnxEncoder(manifest, device)

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        return self.embedder.embed(list(texts))


def load(manifest: ModelManifest, *, device: str = "cpu", embedder: Embedder | None = None) -> OnnxClassifier:
    return OnnxClassifier(manifest, device=device, embedder=embedder)
