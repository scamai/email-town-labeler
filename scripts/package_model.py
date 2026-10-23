#!/usr/bin/env python
"""Package a trained softmax head (+ embedder info) as an email-town-labeler model directory, then validate it.

    # from raw arrays
    python scripts/package_model.py --coef coef.npy --intercept intercept.npy --name my_probe --version 1 \
        --embedder-repo google/embeddinggemma-2 --revision <sha> --prompt "task: classification | query: " \
        --max-seq-length 1024 --dim 768 --out ./my_probe
    # from a directory holding config.json (config.json + coef.npy + intercept.npy)
    python scripts/package_model.py --from-config-dir path/to/embgemma_lr_v1 --out ./embgemma_lr_v1
    # ONNX encoder package
    python scripts/package_model.py --backend onnx_classifier --onnx encoder.onnx --tokenizer tokenizer.json \
        --pooling none --coef coef.npy --intercept intercept.npy --name my_onnx --out ./my_onnx
    # fine-tuned transformers classifier (a save_pretrained dir with id2label naming the seven classes)
    python scripts/package_model.py --backend hf_sequence_classifier --hf-dir path/to/checkpoint \
        --name my_bert --version 1 --weights-dtype auto --out ./my_bert

Validation loads the package through the registry and predicts on two made-up records:
`--validate head` (default) uses a deterministic stub embedder of the right width, so it needs no
ML deps; `--validate full` uses the real embedder (CPU unless --device). Only classes and
probabilities are printed. Packages trained on private mail must never be committed or published.

hf_sequence_classifier: the checkpoint is loaded with local_files_only and trust_remote_code=False and
re-saved as safetensors under `<out>/hf/` with its tokenizer. `--weights-dtype bfloat16` halves the size
(weights are upcast to float32 on CPU at load time); `auto` (default) stores bf16 only if, on 50 made-up
records, its argmax agrees 100% with the float32 checkpoint, and keeps float32 otherwise. The parity
numbers go into `metrics.parity`. Validation always loads the real model (there is no stub head).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

import numpy as np

from email_town_labeler.models.backends._common import sha256_file
from email_town_labeler.models.base import ModelManifest
from email_town_labeler.models.registry import load_model
from email_town_labeler.taxonomy import CLASSES

SEQ_BACKENDS = ("hf_sequence_classifier",)

EMBGEMMA_ST_KWARGS = {"config_kwargs": {"vision_config": None, "audio_config": None}}

FAKE_RECORDS = [
    {
        "id": "fake-1",
        "thread_id": "fake-1",
        "label_ids": ["INBOX", "CATEGORY_UPDATES"],
        "headers": {"from": "Example Shop <orders@shop.example>", "subject": "Your order #1234 has shipped"},
        "snippet": "",
        "body_text": "Hi Sam, your order of 2 widgets has shipped. Track it with code ZX12.",
        "body_source": "plain",
        "has_calendar": False,
        "attachments": [],
    },
    {
        "id": "fake-2",
        "thread_id": "fake-2",
        "label_ids": ["INBOX", "CATEGORY_PERSONAL"],
        "headers": {
            "from": "Alex Doe <alex@corp.example>",
            "subject": "Can you review the Q3 budget by Friday?",
        },
        "snippet": "",
        "body_text": "Hey, could you look over the attached budget and sign off before Friday?",
        "body_source": "plain",
        "has_calendar": False,
        "attachments": [],
    },
]


class StubEmbedder:
    """Deterministic text-hash -> unit vector. Exercises the head path without ML deps."""

    def __init__(self, dim: int):
        self.dim = dim

    def embed(self, texts):
        rows = []
        for t in texts:
            seed = int.from_bytes(hashlib.sha256(t.encode()).digest()[:8], "little")
            v = np.random.default_rng(seed).standard_normal(self.dim)
            rows.append(v / np.linalg.norm(v))
        return np.array(rows)


def build(args: argparse.Namespace) -> Path:
    out = Path(args.out)
    if out.exists() and any(out.iterdir()) and not args.force:
        sys.exit(f"{out} exists and is not empty (use --force)")
    if args.backend in SEQ_BACKENDS:
        return build_seq(args, out)
    if args.from_config_dir:
        src = Path(args.from_config_dir)
        tj = json.loads((src / "config.json").read_text(encoding="utf-8"))
        e = tj.get("embedder", {})
        args.coef, args.intercept = str(src / "coef.npy"), str(src / "intercept.npy")
        args.name = args.name or tj["name"]
        args.classes = args.classes or ",".join(tj["classes"])
        args.embedder_repo, args.revision = e.get("repo"), e.get("revision")
        args.prompt, args.max_seq_length = e.get("prompt", ""), e.get("max_seq_length", 512)
        args.normalize = e.get("normalize_embeddings", True)
        if args.st_kwargs is None and str(e.get("repo", "")).startswith("google/embeddinggemma"):
            args.st_kwargs = json.dumps(EMBGEMMA_ST_KWARGS)
        metrics = dict(tj.get("metrics", {}))
        metrics["training"] = {k: tj.get("training", {}).get(k) for k in ("n_emails", "class_counts", "date")}
        if "parity" in tj:
            metrics["parity"] = tj["parity"]
        args._metrics = metrics
        args.description = args.description or (
            "EmbeddingGemma-2 sentence embedding + multinomial logistic regression head (7-way)."
        )
    if not (args.coef and args.intercept and args.name):
        sys.exit("--coef, --intercept and --name are required (or --from-config-dir)")
    classes = args.classes.split(",") if args.classes else list(CLASSES)
    coef, intercept = np.load(args.coef, allow_pickle=False), np.load(args.intercept, allow_pickle=False)
    if coef.shape != (len(classes), coef.shape[1]) or intercept.shape != (len(classes),):
        sys.exit(
            f"head shape mismatch: coef {coef.shape}, intercept {intercept.shape}, {len(classes)} classes"
        )

    out.mkdir(parents=True, exist_ok=True)
    np.save(out / "coef.npy", coef)
    np.save(out / "intercept.npy", intercept)
    config: dict = {
        "head": {"coef": "coef.npy", "intercept": "intercept.npy"},
        "dim": int(coef.shape[1]),
        "prompt": args.prompt or "",
        "max_seq_length": int(args.max_seq_length),
        "normalize": bool(args.normalize),
        "batch_size": int(args.batch_size),
        "text": "email_town_labeler.features.email_text",
    }
    if args.backend == "embedding_probe":
        if not args.embedder_repo:
            sys.exit("--embedder-repo is required for embedding_probe")
        config["embedder"] = {
            "repo": args.embedder_repo,
            "revision": args.revision,
            **({"st_kwargs": json.loads(args.st_kwargs)} if args.st_kwargs else {}),
        }
    elif args.backend == "onnx_classifier":
        if not (args.onnx and args.tokenizer):
            sys.exit("--onnx and --tokenizer are required for onnx_classifier")
        shutil.copy2(args.onnx, out / "encoder.onnx")
        shutil.copy2(args.tokenizer, out / "tokenizer.json")
        if Path(str(args.onnx) + ".data").exists():  # external weights
            shutil.copy2(str(args.onnx) + ".data", out / "encoder.onnx.data")
        config.update(onnx="encoder.onnx", tokenizer="tokenizer.json", pooling=args.pooling, output=0)
        if args.embedder_repo:
            config["exported_from"] = {"repo": args.embedder_repo, "revision": args.revision}
    files = package_files(out)
    metrics = getattr(args, "_metrics", None) or (
        json.loads(Path(args.metrics).read_text()) if args.metrics else {}
    )
    ModelManifest(
        name=args.name,
        version=str(args.version),
        backend=args.backend,
        classes=classes,
        description=args.description or "",
        license=args.license,
        files=files,
        metrics=metrics,
        config=config,
    ).save(out)
    return out


def package_files(out: Path) -> dict[str, str]:
    """Every file under `out` (relative posix path -> sha256), except model.json and dotfiles."""
    return {
        p.relative_to(out).as_posix(): sha256_file(p)
        for p in sorted(out.rglob("*"))
        if p.is_file()
        and p.relative_to(out).as_posix() != "model.json"
        and not any(part.startswith(".") for part in p.relative_to(out).parts)
    }


def _checkpoint_labels(hf_dir: Path, override: str | None) -> list[str]:
    if override:
        return override.split(",")
    cfg = json.loads((hf_dir / "config.json").read_text(encoding="utf-8"))
    i2l = cfg.get("id2label") or {}
    labels = [i2l.get(str(i), i2l.get(i)) for i in range(len(i2l))]
    if set(labels) != set(CLASSES):
        sys.exit("the checkpoint's id2label does not name the seven classes; pass --labels in head order")
    return labels


def _seq_model(root: Path, model_dir: str, labels: list[str], config: dict, device: str):
    """An hf_sequence_classifier over `root/model_dir` (float32), built from an in-memory manifest."""
    from email_town_labeler.models.backends.hf_sequence_classifier import HFSequenceClassifier

    man = ModelManifest(
        name="src",
        version="0",
        backend="hf_sequence_classifier",
        classes=labels,
        config={**config, "model_dir": model_dir, "labels": labels, "dtype": "float32"},
        path=root,
    )
    return HFSequenceClassifier(man, device=device)


def seq_parity(P_ref: np.ndarray, P: np.ndarray) -> dict:
    return {
        "n": len(P_ref),
        "argmax_agree": float((P_ref.argmax(1) == P.argmax(1)).mean()),
        "max_abs_prob_diff": float(np.abs(P_ref - P).max()),
        "mean_abs_prob_diff": float(np.abs(P_ref - P).mean()),
    }


def build_seq(args: argparse.Namespace, out: Path) -> Path:
    """hf_sequence_classifier package from a save_pretrained checkpoint, with a bf16-vs-fp32 parity check."""
    import gc

    import torch
    from _fake_mail import fake_records

    if not (args.hf_dir and args.name):
        sys.exit("--hf-dir and --name are required for hf_sequence_classifier")
    src = Path(args.hf_dir).resolve()
    labels = _checkpoint_labels(src, args.labels)
    config: dict = {
        "model_dir": "hf",
        "labels": labels,
        "max_length": int(args.max_length),
        "batch_size": int(args.batch_size),
        "threads": int(args.threads),
        "text": "email_town_labeler.features.email_text",
    }
    if args.compute_dtype:
        config["dtype"] = args.compute_dtype
    shutil.rmtree(out / "hf", ignore_errors=True)  # only what this script writes (--force)
    out.mkdir(parents=True, exist_ok=True)

    ref = _seq_model(src.parent, src.name, labels, config, args.device)
    texts = ref.texts(fake_records(args.parity_n))
    P_ref = ref.proba(texts)
    parity: dict = {}
    stored = args.weights_dtype
    if stored in ("bfloat16", "auto"):
        ref.model.to(torch.bfloat16).save_pretrained(out / "hf", safe_serialization=True)
        ref.tokenizer.save_pretrained(out / "hf")
        del ref
        gc.collect()
        bf = _seq_model(out, "hf", labels, config, args.device)  # bf16 on disk, float32 compute
        parity["bf16_vs_fp32"] = seq_parity(P_ref, bf.proba(texts))
        del bf
        gc.collect()
        print(json.dumps({"parity_bf16_vs_fp32": parity["bf16_vs_fp32"]}))
        if stored == "auto":
            stored = "bfloat16" if parity["bf16_vs_fp32"]["argmax_agree"] == 1.0 else "float32"
            if stored == "float32":
                print("bf16 argmax agreement < 100%: keeping float32 weights")
                shutil.rmtree(out / "hf")
        ref = None
    if stored == "float32":
        ref = ref or _seq_model(src.parent, src.name, labels, config, args.device)
        ref.model.to(torch.float32).save_pretrained(out / "hf", safe_serialization=True)
        ref.tokenizer.save_pretrained(out / "hf")
        del ref
        gc.collect()
    config["stored_dtype"] = stored
    metrics = json.loads(Path(args.metrics).read_text()) if args.metrics else {}
    if parity:
        metrics["parity"] = {**metrics.get("parity", {}), **parity}
    ModelManifest(
        name=args.name,
        version=str(args.version),
        backend=args.backend,
        classes=labels,
        description=args.description or "",
        license=args.license,
        files=package_files(out),
        metrics=metrics,
        config=config,
    ).save(out)
    return out


def validate(out: Path, mode: str, device: str) -> None:
    man = ModelManifest.load(out)
    if man.backend in SEQ_BACKENDS:
        mode = "full"  # no stub: the classifier is the model
    kw = {"embedder": StubEmbedder(int(man.config["dim"]))} if mode == "head" else {}
    model = load_model(str(out), models_dir=out.parent, device=device, **kw)
    preds = model.predict(FAKE_RECORDS)
    for rec, p in zip(FAKE_RECORDS, preds):
        assert set(p.probs) >= set(CLASSES) and abs(sum(p.probs.values()) - 1) < 1e-6
        print(
            json.dumps(
                {
                    "id": rec["id"],
                    "cls": p.cls,
                    "prob": round(p.prob, 4),
                    "probs": {k: round(v, 4) for k, v in p.probs.items()},
                }
            )
        )
    print(f"ok: {man.id} ({man.backend}, validate={mode})")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--from-config-dir")
    ap.add_argument(
        "--backend",
        default="embedding_probe",
        choices=["embedding_probe", "onnx_classifier", *SEQ_BACKENDS],
    )
    ap.add_argument("--coef")
    ap.add_argument("--intercept")
    ap.add_argument("--classes", help="comma-separated, in head row order (default: taxonomy.CLASSES)")
    ap.add_argument("--name")
    ap.add_argument("--version", default="1")
    ap.add_argument("--description", default="")
    ap.add_argument("--license", default="")
    ap.add_argument("--embedder-repo")
    ap.add_argument("--revision")
    ap.add_argument("--st-kwargs", help="JSON kwargs for SentenceTransformer(...)")
    ap.add_argument("--prompt", default="")
    ap.add_argument("--max-seq-length", type=int, default=512)
    ap.add_argument("--normalize", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--batch-size", type=int, default=8)
    ap.add_argument("--onnx")
    ap.add_argument("--tokenizer")
    ap.add_argument("--pooling", default="mean", choices=["mean", "cls", "none"])
    ap.add_argument("--hf-dir", help="hf_sequence_classifier: save_pretrained checkpoint directory")
    ap.add_argument(
        "--labels", help="hf_sequence_classifier: comma-separated, head order (default: id2label)"
    )
    ap.add_argument("--max-length", type=int, default=512, help="hf_sequence_classifier: tokens")
    ap.add_argument("--threads", type=int, default=0)
    ap.add_argument(
        "--weights-dtype", default="auto", choices=["auto", "bfloat16", "float32"], help="stored weights"
    )
    ap.add_argument("--compute-dtype", choices=["float32", "bfloat16", "float16"], help="config.dtype")
    ap.add_argument("--parity-n", type=int, default=50, help="made-up records for the bf16 parity check")
    ap.add_argument("--metrics", help="JSON file of held-out metrics")
    ap.add_argument("--out", required=True)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--validate", default="head", choices=["head", "full", "none"])
    ap.add_argument("--device", default="cpu")
    args = ap.parse_args(argv)
    out = build(args)
    if args.validate != "none":
        validate(out, args.validate, args.device)


if __name__ == "__main__":
    main()
