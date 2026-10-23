"""hf_sequence_classifier / onnx_sequence_classifier: tiny random-init models built in tmp dirs."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest

from email_town_labeler.models import registry
from email_town_labeler.models.backends._common import (
    MissingExtraError,
    length_batches,
    seq_labels,
    seq_settings,
    sha256_file,
    softmax,
)
from email_town_labeler.models.base import ModelManifest
from email_town_labeler.models.hub import HubError
from email_town_labeler.taxonomy import CLASSES
from tests._model_fixtures import rec

WORDS = [
    "From:",
    "Subject:",
    "Body:",
    "Mailing",
    "list:",
    "Calendar",
    "invite:",
    "yes",
    "no",
    "hello",
    "invoice",
    "order",
    "shipped",
    "gate",
]
LABELS = list(reversed(CLASSES))  # head index -> class, deliberately not CLASSES order
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def records():
    return [
        rec(0, subject="hello"),
        rec(1, subject="invoice invoice order shipped", body="order shipped " * 40),
        rec(2, subject="gate", list_unsubscribe="<mailto:x@example.com>"),
        rec(3, subject="", body=""),
        rec(4, subject="unknown words only", body="zzz qqq"),
    ]


def _tokenizer():
    tk = pytest.importorskip("tokenizers")
    from tokenizers import models, pre_tokenizers, processors

    vocab = {"[PAD]": 0, "[UNK]": 1, "[CLS]": 2, "[SEP]": 3, "[MASK]": 4}
    for w in WORDS:
        vocab.setdefault(w, len(vocab))
    t = tk.Tokenizer(models.WordLevel(vocab, unk_token="[UNK]"))
    t.pre_tokenizer = pre_tokenizers.Whitespace()
    t.post_processor = processors.TemplateProcessing(
        single="[CLS] $A [SEP]", special_tokens=[("[CLS]", 2), ("[SEP]", 3)]
    )
    return t, len(vocab)


def _tiny_checkpoint(d: Path, labels=LABELS, *, seed: int = 0) -> Path:
    """A random-init ModernBERT (or BERT) classifier, hidden 32, 2 layers, 7 labels, saved like save_pretrained."""
    torch = pytest.importorskip("torch")
    tf = pytest.importorskip("transformers")
    t, vocab_size = _tokenizer()
    common = {
        "vocab_size": vocab_size,
        "hidden_size": 32,
        "num_hidden_layers": 2,
        "num_attention_heads": 2,
        "intermediate_size": 64,
        "max_position_embeddings": 128,
        "num_labels": len(labels),
        "id2label": dict(enumerate(labels)),
        "label2id": {c: i for i, c in enumerate(labels)},
        "pad_token_id": 0,
    }
    if hasattr(tf, "ModernBertConfig"):
        cfg = tf.ModernBertConfig(
            **common, cls_token_id=2, sep_token_id=3, bos_token_id=2, eos_token_id=3, local_attention=16
        )
    else:
        cfg = tf.BertConfig(**common)
    torch.manual_seed(seed)
    model = tf.AutoModelForSequenceClassification.from_config(cfg).eval()
    d.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(d, safe_serialization=True)
    tf.PreTrainedTokenizerFast(
        tokenizer_object=t,
        pad_token="[PAD]",
        unk_token="[UNK]",
        cls_token="[CLS]",
        sep_token="[SEP]",
        mask_token="[MASK]",
    ).save_pretrained(d)
    return d


def _manifest(pkg: Path, *, backend: str = "hf_sequence_classifier", **config) -> Path:
    files = {
        p.relative_to(pkg).as_posix(): sha256_file(p)
        for p in sorted(pkg.rglob("*"))
        if p.is_file() and p.name != "model.json"
    }
    cfg = {"model_dir": "hf", "labels": LABELS, "max_length": 64, "batch_size": 2, **config}
    ModelManifest(
        name="tiny-seq", version="1", backend=backend, classes=LABELS, files=files, config=cfg
    ).save(pkg)
    return pkg


def _fake_checkpoint(d: Path, config: dict | None = None) -> Path:
    """Files only (no weights inside): enough for the path/config checks that run before any import."""
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text(json.dumps(config or {"model_type": "bert"}))
    (d / "model.safetensors").write_bytes(b"")
    return d


def _reference(pkg: Path, records_, labels=LABELS) -> np.ndarray:
    """Probabilities straight from transformers, head order."""
    import torch
    import transformers as tf

    from email_town_labeler.features import email_text

    tok = tf.AutoTokenizer.from_pretrained(pkg / "hf")
    m = tf.AutoModelForSequenceClassification.from_pretrained(pkg / "hf").eval()
    rows = []
    with torch.no_grad():
        for r in records_:
            enc = tok([email_text(r)], truncation=True, max_length=64, return_tensors="pt")
            rows.append(softmax(m(**enc).logits.float().numpy())[0])
    return np.array(rows)


# ------------------------------------------------------------------ no ML deps needed


def test_registered():
    assert {"hf_sequence_classifier", "onnx_sequence_classifier"} <= set(registry.backend_names())


def test_seq_labels_forms_and_checks():
    man = ModelManifest(
        name="x", version="1", backend="b", config={"labels": {str(i): c for i, c in enumerate(LABELS)}}
    )
    assert seq_labels(man, "b") == LABELS
    assert seq_labels(ModelManifest(name="x", version="1", backend="b"), "b") == list(CLASSES)
    for bad in (
        LABELS[:-1],
        [*LABELS[:-1], "bogus"],
        [*LABELS[:-1], LABELS[0]],
        {"1": "orders"},
        "orders",
        [1, 2, 3],
    ):
        with pytest.raises((ValueError, TypeError)):
            seq_labels(ModelManifest(name="x", version="1", backend="b", config={"labels": bad}), "b")
    with pytest.raises(ValueError, match="8 outputs"):
        seq_labels(ModelManifest(name="x", version="1", backend="b"), "b", n_outputs=7 + 1)


@pytest.mark.parametrize(
    "key,bad", [("max_length", 0), ("max_length", "512"), ("batch_size", 0), ("threads", True)]
)
def test_seq_settings_validated(key, bad):
    with pytest.raises(ValueError, match=key):
        seq_settings(ModelManifest(name="x", version="1", backend="b", config={key: bad}), "b")


def test_length_batches_cover_every_index_once():
    lengths = [5, 1, 9, 3, 3, 7, 2]
    batches = length_batches(lengths, 3)
    assert sorted(i for b in batches for i in b) == list(range(7)) and all(len(b) <= 3 for b in batches)
    assert [lengths[i] for b in batches for i in b] == sorted(lengths)


@pytest.mark.parametrize("bad", ["../hf", "/abs/hf", "C:\\hf", "x/../../hf", 7])
def test_model_dir_must_stay_in_package(tmp_path, bad):
    from email_town_labeler.models.backends import hf_sequence_classifier as hsc

    pkg = tmp_path / "m"
    _fake_checkpoint(pkg / "hf")
    _manifest(pkg, model_dir=bad)
    with pytest.raises(HubError):
        hsc.load(ModelManifest.load(pkg))


def test_symlink_out_of_package_refused(tmp_path):
    from email_town_labeler.models.backends import hf_sequence_classifier as hsc

    outside = tmp_path / "outside.safetensors"
    outside.write_bytes(b"")
    pkg = tmp_path / "m"
    d = _fake_checkpoint(pkg / "hf")
    (d / "model.safetensors").unlink()
    try:
        (d / "model.safetensors").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks not available")
    _manifest(pkg)
    with pytest.raises(HubError, match="outside the package"):
        hsc.load(ModelManifest.load(pkg))


def test_code_loading_config_refused(tmp_path):
    from email_town_labeler.models.backends import hf_sequence_classifier as hsc

    pkg = tmp_path / "m"
    _fake_checkpoint(pkg / "hf", {"model_type": "bert", "auto_map": {"AutoModel": "evil.Model"}})
    _manifest(pkg)
    with pytest.raises(HubError, match="auto_map"):
        hsc.load(ModelManifest.load(pkg))


def test_pickle_weights_refused(tmp_path):
    """Only safetensors: a checkpoint with just pytorch_model.bin (a pickle) is refused before loading."""
    from email_town_labeler.models.backends import hf_sequence_classifier as hsc

    pkg = tmp_path / "m"
    d = _fake_checkpoint(pkg / "hf")
    (d / "model.safetensors").unlink()
    (d / "pytorch_model.bin").write_bytes(b"")
    _manifest(pkg)
    with pytest.raises(HubError, match="model.safetensors"):
        hsc.load(ModelManifest.load(pkg))


def test_missing_transformers_names_embed_extra(tmp_path):
    if importlib.util.find_spec("transformers") and importlib.util.find_spec("torch"):
        pytest.skip("transformers installed")
    pkg = tmp_path / "m"
    _fake_checkpoint(pkg / "hf")
    _manifest(pkg)
    with pytest.raises(MissingExtraError, match=r'pip install "email-town-labeler\[embed\] @'):
        registry.load_model(str(pkg), models_dir=tmp_path)


def test_missing_onnxruntime_names_onnx_extra(tmp_path):
    if importlib.util.find_spec("onnxruntime") and importlib.util.find_spec("tokenizers"):
        pytest.skip("onnxruntime installed")
    pkg = tmp_path / "m"
    pkg.mkdir()
    (pkg / "model.onnx").write_bytes(b"")
    (pkg / "tokenizer.json").write_text("{}")
    _manifest(pkg, backend="onnx_sequence_classifier", onnx="model.onnx", tokenizer="tokenizer.json")
    with pytest.raises(MissingExtraError, match=r'pip install "email-town-labeler\[onnx\] @'):
        registry.load_model(str(pkg), models_dir=tmp_path)


@pytest.mark.parametrize("key,bad", [("onnx", "../model.onnx"), ("tokenizer", "/abs/tok.json"), ("onnx", 3)])
def test_onnx_paths_must_stay_in_package(tmp_path, key, bad):
    from email_town_labeler.models.backends import onnx_sequence_classifier as osc

    pkg = tmp_path / "m"
    pkg.mkdir()
    _manifest(pkg, backend="onnx_sequence_classifier", **{key: bad})
    with pytest.raises(HubError):
        osc.load(ModelManifest.load(pkg))


# ------------------------------------------------------------------ torch + transformers


def test_hf_end_to_end(tmp_path):
    pkg = tmp_path / "m"
    _tiny_checkpoint(pkg / "hf")
    _manifest(pkg)
    m = registry.load_model(str(pkg), models_dir=tmp_path)
    assert m.manifest.backend == "hf_sequence_classifier" and m.labels == LABELS
    rs = records()
    preds = m.predict(rs)
    assert len(preds) == len(rs) and m.predict([]) == []
    ref = _reference(pkg, rs)
    for p, row in zip(preds, ref):
        assert set(p.probs) == set(CLASSES)
        assert abs(sum(p.probs.values()) - 1) < 1e-6
        # head index j is class LABELS[j]
        assert np.allclose([p.probs[c] for c in LABELS], row, atol=1e-5)
        assert p.cls == LABELS[int(row.argmax())] and p.prob == p.probs[p.cls]
    # length-sorted batching must not reorder results
    one = registry.load_model(str(_manifest(pkg, batch_size=1)), models_dir=tmp_path).predict(rs)
    for a, b in zip(preds, one):
        assert np.allclose([a.probs[c] for c in CLASSES], [b.probs[c] for c in CLASSES], atol=1e-5)


def test_hf_bf16_weights_upcast_on_cpu(tmp_path):
    pkg = tmp_path / "m"
    _tiny_checkpoint(pkg / "hf")
    import torch
    import transformers as tf

    tf.AutoModelForSequenceClassification.from_pretrained(pkg / "hf").to(torch.bfloat16).save_pretrained(
        pkg / "hf"
    )
    _manifest(pkg)
    m = registry.load_model(str(pkg), models_dir=tmp_path)
    assert next(m.model.parameters()).dtype == torch.float32
    bf = registry.load_model(str(_manifest(pkg, dtype="bfloat16")), models_dir=tmp_path)
    assert next(bf.model.parameters()).dtype == torch.bfloat16
    for p in bf.predict(records()):
        assert abs(sum(p.probs.values()) - 1) < 1e-6
    with pytest.raises(ValueError, match="dtype"):
        registry.load_model(str(_manifest(pkg, dtype="int8")), models_dir=tmp_path)


def test_hf_label_checks(tmp_path):
    pkg = tmp_path / "m"
    _tiny_checkpoint(pkg / "hf")
    with pytest.raises(ValueError, match="id2label"):  # a permutation of the checkpoint's own names
        registry.load_model(str(_manifest(pkg, labels=list(CLASSES))), models_dir=tmp_path)
    with pytest.raises(ValueError, match="every class"):
        registry.load_model(str(_manifest(pkg, labels=LABELS[:6])), models_dir=tmp_path)


def test_hf_num_labels_must_match(tmp_path):
    pytest.importorskip("transformers")
    pkg = tmp_path / "m"
    _tiny_checkpoint(pkg / "hf", labels=[*LABELS, "extra"])
    with pytest.raises(ValueError, match="8 outputs"):
        registry.load_model(str(_manifest(pkg)), models_dir=tmp_path)


# ------------------------------------------------------------------ ONNX


def _tiny_logits_onnx(path: Path, vocab: int, n: int, seed: int = 0) -> np.ndarray:
    """logits = sum over tokens of table[input_ids] * attention_mask: padding must be masked out."""
    onnx = pytest.importorskip("onnx")
    from onnx import TensorProto, helper, numpy_helper

    table = np.random.default_rng(seed).standard_normal((vocab, n)).astype(np.float32)
    nodes = [
        helper.make_node("Gather", ["table", "input_ids"], ["emb"], axis=0),
        helper.make_node("Cast", ["attention_mask"], ["maskf"], to=TensorProto.FLOAT),
        helper.make_node("Unsqueeze", ["maskf", "axes"], ["mask3"]),
        helper.make_node("Mul", ["emb", "mask3"], ["masked"]),
        helper.make_node("ReduceSum", ["masked", "axis1"], ["logits"], keepdims=0),
    ]
    g = helper.make_graph(
        nodes,
        "tiny-seq",
        [
            helper.make_tensor_value_info("input_ids", TensorProto.INT64, ["b", "s"]),
            helper.make_tensor_value_info("attention_mask", TensorProto.INT64, ["b", "s"]),
        ],
        [helper.make_tensor_value_info("logits", TensorProto.FLOAT, ["b", n])],
        [
            numpy_helper.from_array(table, "table"),
            numpy_helper.from_array(np.array([2], dtype=np.int64), "axes"),
            numpy_helper.from_array(np.array([1], dtype=np.int64), "axis1"),
        ],
    )
    m = helper.make_model(g, opset_imports=[helper.make_opsetid("", 13)])
    m.ir_version = 8
    onnx.save(m, str(path))
    return table


def test_onnx_end_to_end_hand_built_graph(tmp_path):
    pytest.importorskip("onnxruntime")
    from email_town_labeler.features import email_text

    t, vocab = _tokenizer()
    pkg = tmp_path / "m"
    pkg.mkdir()
    t.save(str(pkg / "tokenizer.json"))
    table = _tiny_logits_onnx(pkg / "model.onnx", vocab, len(LABELS))
    _manifest(
        pkg, backend="onnx_sequence_classifier", onnx="model.onnx", tokenizer="tokenizer.json", pad_id=0
    )
    m = registry.load_model(str(pkg), models_dir=tmp_path)
    rs = records()
    preds = m.predict(rs)
    for r, p in zip(rs, preds):
        ids = m.tok.encode(email_text(r)).ids  # truncated to max_length, special tokens kept
        row = softmax(table[ids].sum(0))
        assert np.allclose([p.probs[c] for c in LABELS], row, atol=1e-5)
        assert abs(sum(p.probs.values()) - 1) < 1e-6 and set(p.probs) == set(CLASSES)
    with pytest.raises(ValueError, match="no output"):
        registry.load_model(
            str(_manifest(pkg, backend="onnx_sequence_classifier", output="nope")), models_dir=tmp_path
        )


def _load_script(name: str):
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location(f"_script_{name}", SCRIPTS / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(SCRIPTS))


def test_package_and_export_scripts(tmp_path, monkeypatch, capsys):
    """package_model.py (bf16 auto) -> export_onnx.py --seq-classifier -> parity, all on the tiny model."""
    for mod in ("torch", "transformers", "onnx", "onnxruntime"):
        pytest.importorskip(mod)
    monkeypatch.syspath_prepend(str(SCRIPTS))
    src = _tiny_checkpoint(tmp_path / "ckpt")
    pkg, onnx_pkg = tmp_path / "pkg", tmp_path / "pkg-onnx"
    pm = _load_script("package_model")
    pm.main(
        ["--backend", "hf_sequence_classifier", "--hf-dir", str(src), "--name", "tiny-seq", "--max-length", "64",
         "--parity-n", "20", "--out", str(pkg)]
    )  # fmt: skip
    man = ModelManifest.load(pkg)
    par = man.metrics["parity"]["bf16_vs_fp32"]
    assert man.config["stored_dtype"] == ("bfloat16" if par["argmax_agree"] == 1.0 else "float32")
    assert man.config["labels"] == LABELS and "hf/model.safetensors" in man.files
    from email_town_labeler.models.hub import verify

    verify(pkg)  # every file hashed, nested paths included

    ex = _load_script("export_onnx")
    ex.main(["--seq-classifier", "--model", str(pkg), "--out", str(onnx_pkg), "--parity-n", "20"])
    oman = verify(onnx_pkg)
    assert oman.backend == "onnx_sequence_classifier" and oman.config["labels"] == LABELS
    res = oman.metrics["parity"]["onnx_vs_torch"]
    assert res["argmax_agree"] == 1.0 and res["max_abs_prob_diff"] < 1e-3
    assert "Subject" not in capsys.readouterr().out  # numbers only, never texts
