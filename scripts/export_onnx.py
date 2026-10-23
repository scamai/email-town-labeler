#!/usr/bin/env python
"""Export an `embedding_probe` package to an `onnx_classifier` package (no torch needed at runtime).

    python scripts/export_onnx.py --model path/to/embgemma_lr_v1 --out path/to/embgemma_lr_v1-onnx
    python scripts/export_onnx.py --seq-classifier --model path/to/my_bert --out path/to/my_bert-onnx

`--seq-classifier` exports an `hf_sequence_classifier` package instead: the classifier (float32) is
traced into `model.onnx` (input_ids, attention_mask -> logits, dynamic batch and sequence) and the
package becomes an `onnx_sequence_classifier` with the same labels and tokenizer. Its parity check runs
both packages through the registry on 50 made-up records and reports argmax agreement and the
largest probability difference (it fails below 100% agreement or above 1e-3).

The whole SentenceTransformer pipeline (transformer -> pooling -> dense -> normalize) is traced
into one graph `encoder.onnx` with inputs input_ids/attention_mask (int64, dynamic batch and
sequence) and output `sentence_embedding` (batch, dim), so the package uses `pooling: "none"`.
The tokenizer is saved as `tokenizer.json`; the softmax head files are copied unchanged.

Parity check (on by default): embeds made-up texts with sentence-transformers and with
onnxruntime + tokenizers through the real `onnx_classifier` backend, and reports min cosine
and head-argmax agreement. Needs the `[embed]` and `[onnx]` extras plus `onnx`. CPU only.
Prints numbers only; never prints texts.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np

from email_town_labeler.features import email_text
from email_town_labeler.models.backends._common import sha256_file
from email_town_labeler.models.backends.embedding_probe import SentenceTransformerEmbedder
from email_town_labeler.models.base import ModelManifest
from email_town_labeler.models.registry import load_model

PARITY_TEXTS = [
    {
        "headers": {"from": "Shop <orders@shop.example>", "subject": "Your order has shipped"},
        "body_text": "Your parcel is on the way. Tracking code ZX12.",
        "has_calendar": False,
    },
    {
        "headers": {"from": "Alex <alex@corp.example>", "subject": "Can you review the budget?"},
        "body_text": "Please sign off before Friday. " * 40,
        "has_calendar": False,
    },
    {
        "headers": {"from": "News <news@list.example>", "subject": "This week in widgets", "list-id": "x"},
        "body_text": "Ten stories about widgets.",
        "has_calendar": False,
    },
    {
        "headers": {"from": "Air <noreply@air.example>", "subject": "Boarding pass"},
        "body_text": "Gate 4.",
        "has_calendar": True,
    },
]


def export(model_dir: Path, out: Path, opset: int, max_len: int | None) -> ModelManifest:
    import torch

    man = ModelManifest.load(model_dir)
    if man.backend != "embedding_probe":
        sys.exit(f"expected an embedding_probe package, got {man.backend!r}")
    st = SentenceTransformerEmbedder(man.config, "cpu").model.eval()

    class Wrapper(torch.nn.Module):
        def __init__(self, m):
            super().__init__()
            self.m = m

        def forward(self, input_ids, attention_mask):
            return self.m({"input_ids": input_ids, "attention_mask": attention_mask})["sentence_embedding"]

    out.mkdir(parents=True, exist_ok=True)
    enc = st.tokenize(["hello world", "a slightly longer made-up sentence for tracing"])
    with torch.no_grad():
        torch.onnx.export(
            Wrapper(st),
            (enc["input_ids"], enc["attention_mask"]),
            str(out / "encoder.onnx"),
            input_names=["input_ids", "attention_mask"],
            output_names=["sentence_embedding"],
            dynamic_axes={
                "input_ids": {0: "b", 1: "s"},
                "attention_mask": {0: "b", 1: "s"},
                "sentence_embedding": {0: "b"},
            },
            opset_version=opset,
            dynamo=False,
        )
    with tempfile.TemporaryDirectory() as td:
        st.tokenizer.save_pretrained(td)
        shutil.copy2(Path(td) / "tokenizer.json", out / "tokenizer.json")
    head = man.config.get("head", {})
    for rel in (head.get("coef", "coef.npy"), head.get("intercept", "intercept.npy")):
        shutil.copy2(Path(model_dir) / rel, out / rel)
    pad = st.tokenizer.pad_token_id
    cfg = {
        k: man.config[k]
        for k in ("head", "dim", "prompt", "normalize", "batch_size", "text")
        if k in man.config
    }
    cfg.update(
        onnx="encoder.onnx",
        tokenizer="tokenizer.json",
        pooling="none",
        output="sentence_embedding",
        max_seq_length=int(max_len or man.config.get("max_seq_length", 512)),
        pad_id=int(pad if pad is not None else 0),
        exported_from={**man.config.get("embedder", {}), "package": man.id, "opset": opset},
    )
    files = {p.name: sha256_file(p) for p in sorted(out.iterdir()) if p.is_file() and p.name != "model.json"}
    new = ModelManifest(
        name=f"{man.name}-onnx",
        version=man.version,
        backend="onnx_classifier",
        classes=list(man.classes),
        description=f"{man.description} (ONNX export)".strip(),
        license=man.license,
        files=files,
        metrics=man.metrics,
        config=cfg,
    )
    new.save(out)
    return new


def parity(model_dir: Path, out: Path) -> dict:
    a = load_model(str(model_dir), models_dir=model_dir.parent)
    b = load_model(str(out), models_dir=out.parent)
    texts = [email_text(r) for r in PARITY_TEXTS]
    Ea, Eb = a.embed(texts), b.embed(texts)
    cos = (Ea * Eb).sum(1) / (np.linalg.norm(Ea, axis=1) * np.linalg.norm(Eb, axis=1))
    agree = (a.head.proba(Ea).argmax(1) == b.head.proba(Eb).argmax(1)).mean()
    return {
        "n": len(texts),
        "min_cos": float(cos.min()),
        "mean_cos": float(cos.mean()),
        "argmax_agree": float(agree),
    }


def export_seq(model_dir: Path, out: Path, opset: int, attn: str = "eager") -> ModelManifest:
    """hf_sequence_classifier package -> onnx_sequence_classifier package (float32 graph)."""
    import dataclasses

    import torch

    from email_town_labeler.models.backends.hf_sequence_classifier import HFSequenceClassifier, checkpoint_dir

    man = ModelManifest.load(model_dir)
    if man.backend != "hf_sequence_classifier":
        sys.exit(f"expected an hf_sequence_classifier package, got {man.backend!r}")
    fp32 = dataclasses.replace(man, config={**man.config, "dtype": "float32"})
    clf = HFSequenceClassifier(fp32, device="cpu", attn_implementation=attn)

    class Wrapper(torch.nn.Module):
        def __init__(self, m):
            super().__init__()
            self.m = m

        def forward(self, input_ids, attention_mask):
            return self.m(input_ids=input_ids, attention_mask=attention_mask).logits

    out.mkdir(parents=True, exist_ok=True)
    enc = clf.encode(["hello world", "a slightly longer made-up sentence for tracing the graph"])
    with torch.no_grad():
        torch.onnx.export(
            Wrapper(clf.model),
            (enc["input_ids"], enc["attention_mask"]),
            str(out / "model.onnx"),
            input_names=["input_ids", "attention_mask"],
            output_names=["logits"],
            dynamic_axes={
                "input_ids": {0: "b", 1: "s"},
                "attention_mask": {0: "b", 1: "s"},
                "logits": {0: "b"},
            },
            opset_version=opset,
            dynamo=False,
        )
    tok_src = checkpoint_dir(man) / "tokenizer.json"
    if tok_src.is_file():
        shutil.copy2(tok_src, out / "tokenizer.json")
    else:
        with tempfile.TemporaryDirectory() as td:
            clf.tokenizer.save_pretrained(td)
            shutil.copy2(Path(td) / "tokenizer.json", out / "tokenizer.json")
    pad = clf.tokenizer.pad_token_id
    cfg = {
        k: man.config[k]
        for k in ("labels", "max_length", "batch_size", "threads", "prompt", "max_body_chars", "text")
        if k in man.config
    }
    cfg.update(
        onnx="model.onnx",
        tokenizer="tokenizer.json",
        output="logits",
        pad_id=int(pad if pad is not None else 0),
        exported_from={"package": man.id, "opset": opset, "dtype": "float32"},
    )
    files = {
        p.relative_to(out).as_posix(): sha256_file(p)
        for p in sorted(out.rglob("*"))
        if p.is_file() and p.name != "model.json"
    }
    metrics = {k: v for k, v in man.metrics.items() if k != "parity"}
    new = ModelManifest(
        name=f"{man.name}-onnx",
        version=man.version,
        backend="onnx_sequence_classifier",
        classes=list(man.classes),
        description=f"{man.description} (ONNX export)".strip(),
        license=man.license,
        files=files,
        metrics=metrics,
        config=cfg,
    )
    new.save(out)
    return new


def parity_seq(model_dir: Path, out: Path, n: int = 50) -> dict:
    from _fake_mail import fake_records

    records = fake_records(n)
    b = load_model(str(out), models_dir=out.parent)
    Pb = b.proba(b.texts(records))
    del b
    a = load_model(str(model_dir), models_dir=model_dir.parent)
    Pa = a.proba(a.texts(records))
    return {
        "n": len(records),
        "argmax_agree": float((Pa.argmax(1) == Pb.argmax(1)).mean()),
        "max_abs_prob_diff": float(np.abs(Pa - Pb).max()),
        "mean_abs_prob_diff": float(np.abs(Pa - Pb).mean()),
    }


def record_parity(out: Path, res: dict) -> None:
    """Store the parity result in the exported manifest (model.json is not in `files`)."""
    man = ModelManifest.load(out)
    man.metrics = {**man.metrics, "parity": {**man.metrics.get("parity", {}), "onnx_vs_torch": res}}
    man.save(out)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--model", required=True, type=Path, help="embedding_probe (or --seq-classifier) package dir"
    )
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--opset", type=int, default=17)
    ap.add_argument("--max-seq-length", type=int)
    ap.add_argument("--seq-classifier", action="store_true", help="export an hf_sequence_classifier package")
    ap.add_argument("--attn", default="eager", help="--seq-classifier: attention implementation to trace")
    ap.add_argument("--parity-n", type=int, default=50)
    ap.add_argument("--no-check", action="store_true")
    args = ap.parse_args(argv)
    if args.seq_classifier:
        new = export_seq(args.model, args.out, args.opset, args.attn)
        print(f"exported {new.id} -> {args.out}")
        if not args.no_check:
            res = parity_seq(args.model, args.out, args.parity_n)
            print(res)
            record_parity(args.out, res)
            if res["argmax_agree"] < 1.0 or res["max_abs_prob_diff"] > 1e-3:
                sys.exit("parity check FAILED")
        return
    new = export(args.model, args.out, args.opset, args.max_seq_length)
    print(f"exported {new.id} -> {args.out}")
    if not args.no_check:
        res = parity(args.model, args.out)
        print(res)
        if res["min_cos"] < 0.999 or res["argmax_agree"] < 1.0:
            sys.exit("parity check FAILED")


if __name__ == "__main__":
    main()
