"""An ONNX sequence-classification graph via onnxruntime and tokenizers only (the `[onnx]` extra, no torch).

The ONNX twin of `hf_sequence_classifier` (see scripts/export_onnx.py --seq-classifier).

model.json `config`:
    onnx: "model.onnx"           graph inputs: input_ids, attention_mask (+ token_type_ids if the graph has
                                 it), int64 (batch, seq); output: logits (batch, len(labels))
    tokenizer: "tokenizer.json"  HF `tokenizers` file
    output: "logits" | 0         graph output name or index (default 0)
    labels, max_length, batch_size, threads (0 = ort default), prompt, max_body_chars: as hf_sequence_classifier
    pad_id: int (optional; else the tokenizer's padding id, else 0)
Every path above is package-supplied and must stay inside the package directory (`package_file`).
"""

from __future__ import annotations

import numpy as np

from ..base import ModelManifest
from ._common import SequenceClassifier, length_batches, package_file, require, seq_labels, seq_settings

BACKEND = "onnx_sequence_classifier"


class OnnxSequenceClassifier(SequenceClassifier):
    def __init__(self, manifest: ModelManifest, *, device: str = "cpu"):
        self.manifest = manifest
        cfg = manifest.config
        self.settings = seq_settings(manifest, BACKEND)
        self.labels = seq_labels(manifest, BACKEND)
        tok_file = package_file(manifest, cfg.get("tokenizer", "tokenizer.json"), "tokenizer")
        onnx_file = package_file(manifest, cfg.get("onnx", "model.onnx"), "onnx")
        ort = require("onnxruntime", BACKEND)
        tk = require("tokenizers", BACKEND)
        self.tok = tk.Tokenizer.from_file(str(tok_file))
        self.tok.enable_truncation(self.settings["max_length"])
        pad = self.tok.padding or {}
        self.pad_id = int(cfg.get("pad_id", pad.get("pad_id", 0)))
        self.tok.no_padding()  # each batch is padded to its own longest sequence below
        so = ort.SessionOptions()
        if self.settings["threads"]:
            so.intra_op_num_threads = self.settings["threads"]
        so.log_severity_level = 3
        providers = (
            ["CUDAExecutionProvider", "CPUExecutionProvider"]
            if device.startswith("cuda")
            else ["CPUExecutionProvider"]
        )
        self.session = ort.InferenceSession(str(onnx_file), so, providers=providers)
        self.inputs = {i.name for i in self.session.get_inputs()}
        if not {"input_ids", "attention_mask"} <= self.inputs:
            raise ValueError(
                f"{BACKEND}: the graph needs inputs input_ids and attention_mask, has {sorted(self.inputs)}"
            )
        names = [o.name for o in self.session.get_outputs()]
        out = cfg.get("output", 0)
        if isinstance(out, bool) or not isinstance(out, (int, str)):
            raise TypeError(f"{BACKEND} config.output must be an output name or index")
        if isinstance(out, int):
            if not 0 <= out < len(names):
                raise ValueError(f"ONNX graph has no output {out}; outputs: {names}")
            out = names[out]
        if out not in names:
            raise ValueError(f"ONNX graph has no output {out!r}; outputs: {names}")
        self.output = out

    def logits(self, texts: list[str]) -> np.ndarray:
        enc = [e.ids for e in self.tok.encode_batch(list(texts))]
        out = np.zeros((len(texts), len(self.labels)), dtype=np.float32)
        for idx in length_batches([len(e) for e in enc], self.settings["batch_size"]):
            width = max(len(enc[i]) for i in idx)
            ids = np.full((len(idx), width), self.pad_id, dtype=np.int64)
            mask = np.zeros((len(idx), width), dtype=np.int64)
            for row, i in enumerate(idx):
                ids[row, : len(enc[i])] = enc[i]
                mask[row, : len(enc[i])] = 1
            feed = {"input_ids": ids, "attention_mask": mask, "token_type_ids": np.zeros_like(ids)}
            (z,) = self.session.run([self.output], {k: v for k, v in feed.items() if k in self.inputs})
            out[idx] = np.asarray(z, dtype=np.float32)
        return out


def load(manifest: ModelManifest, *, device: str = "cpu", **_) -> OnnxSequenceClassifier:
    return OnnxSequenceClassifier(manifest, device=device)
