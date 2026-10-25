#!/usr/bin/env python
"""Quantize an `onnx_sequence_classifier` package to int8 weights, as a new package.

    python scripts/quantize_onnx.py --model path/to/town-labels-modernbert-large-onnx \
        --out path/to/town-labels-modernbert-large-onnx-int8 --card docs/model-cards/town-modernbert-int8.md

onnxruntime's dynamic quantization (`quantize_dynamic`, QInt8 weights, one scale per tensor unless
`--per-channel`): MatMul weights are stored as int8 and activations are quantized on the fly, so the
graph is about a quarter of the size and the loaded model needs about half the memory. The tokenizer
and labels are copied. model.json gets the name `<name>-int8`, `files` sha256s, `config.quantized`
(method, onnxruntime version, source package and sha256), the source's metrics moved under
`metrics.float32_source` (they were measured on the float32 weights, not on these), and
`metrics.parity.int8_vs_float32` on made-up records (argmax agreement and probability differences,
through the real backend). Parity is reported, not enforced: int8 moves probabilities a little, and
a class near a policy threshold can flip. `--card` becomes the package's README.md.

Needs the `[onnx]` extra plus `onnx`. CPU only. Prints numbers only; never prints texts.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import numpy as np

from email_town_labeler.models.backends._common import sha256_file
from email_town_labeler.models.base import ModelManifest
from email_town_labeler.models.registry import load_model


def quantize(
    model_dir: Path, out: Path, *, per_channel: bool = False, card: Path | None = None
) -> ModelManifest:
    import onnxruntime
    from onnxruntime.quantization import QuantType, quantize_dynamic

    man = ModelManifest.load(model_dir)
    if man.backend != "onnx_sequence_classifier":
        sys.exit(f"expected an onnx_sequence_classifier package, got {man.backend!r}")
    cfg = dict(man.config)
    src_onnx = model_dir / cfg.get("onnx", "model.onnx")
    out.mkdir(parents=True, exist_ok=True)
    quantize_dynamic(src_onnx, out / "model.onnx", weight_type=QuantType.QInt8, per_channel=per_channel)
    shutil.copy2(model_dir / cfg.get("tokenizer", "tokenizer.json"), out / "tokenizer.json")
    if card is not None:
        shutil.copy2(card, out / "README.md")
    cfg.update(
        onnx="model.onnx",
        tokenizer="tokenizer.json",
        quantized={
            "method": "onnxruntime.quantization.quantize_dynamic",
            "weight_type": "QInt8",
            "per_channel": per_channel,
            "onnxruntime": onnxruntime.__version__,
            "from": man.id,
            "from_sha256": sha256_file(src_onnx),
        },
    )
    files = {
        p.relative_to(out).as_posix(): sha256_file(p)
        for p in sorted(out.rglob("*"))
        if p.is_file() and p.name != "model.json"
    }
    new = ModelManifest(
        name=f"{man.name}-int8",
        version=man.version,
        backend=man.backend,
        classes=list(man.classes),
        description=f"{man.description} int8 weights (dynamic quantization).".strip(),
        license=man.license,
        files=files,
        metrics={"float32_source": {k: v for k, v in man.metrics.items()}},
        config=cfg,
    )
    new.save(out)
    return new


def parity(model_dir: Path, out: Path, n: int = 50) -> dict:
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
    """Store the parity result in the new manifest (model.json is not in `files`)."""
    man = ModelManifest.load(out)
    man.metrics = {**man.metrics, "parity": {"int8_vs_float32": res}}
    man.save(out)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--model", required=True, type=Path, help="onnx_sequence_classifier package dir")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument(
        "--per-channel", action="store_true", help="one scale per output channel (larger, closer)"
    )
    ap.add_argument("--card", type=Path, help="model card to ship as README.md")
    ap.add_argument("--parity-n", type=int, default=50)
    ap.add_argument("--no-check", action="store_true")
    args = ap.parse_args(argv)
    new = quantize(args.model, args.out, per_channel=args.per_channel, card=args.card)
    print(f"quantized {new.id} -> {args.out}")
    if not args.no_check:
        res = parity(args.model, args.out, args.parity_n)
        print(res)
        record_parity(args.out, res)


if __name__ == "__main__":
    main()
