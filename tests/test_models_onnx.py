import numpy as np
import pytest

from email_town_labeler.models.backends.onnx_classifier import pool
from email_town_labeler.models.base import ModelManifest
from email_town_labeler.taxonomy import CLASSES
from tests._model_fixtures import head_package, rec


def test_pool_modes():
    h = np.arange(2 * 3 * 2, dtype=np.float32).reshape(2, 3, 2)
    mask = np.array([[1, 1, 0], [1, 0, 0]])
    assert np.allclose(pool(h, mask, "mean"), [[1, 2], [6, 7]])
    assert np.allclose(pool(h, mask, "cls"), [[0, 1], [6, 7]])
    assert pool(h[:, 0], mask, "none").shape == (2, 2)
    with pytest.raises(ValueError):
        pool(h, mask, "max")
    with pytest.raises(ValueError):
        pool(h, mask, "none")


def _tiny_onnx(path, vocab: int, dim: int, seed: int = 0):
    """Embedding lookup graph: input_ids (b, s) -> hidden (b, s, dim), plus an unused attention_mask."""
    onnx = pytest.importorskip("onnx")
    from onnx import TensorProto, helper, numpy_helper

    table = np.random.default_rng(seed).standard_normal((vocab, dim)).astype(np.float32)
    g = helper.make_graph(
        [helper.make_node("Gather", ["table", "input_ids"], ["hidden"], axis=0)],
        "tiny",
        [
            helper.make_tensor_value_info("input_ids", TensorProto.INT64, ["b", "s"]),
            helper.make_tensor_value_info("attention_mask", TensorProto.INT64, ["b", "s"]),
        ],
        [helper.make_tensor_value_info("hidden", TensorProto.FLOAT, ["b", "s", dim])],
        [numpy_helper.from_array(table, "table")],
    )
    m = helper.make_model(g, opset_imports=[helper.make_opsetid("", 13)])
    m.ir_version = 8
    onnx.save(m, str(path))
    return table


def _tiny_tokenizer(path, words):
    tk = pytest.importorskip("tokenizers")
    from tokenizers import models, pre_tokenizers

    vocab = {"[PAD]": 0, "[UNK]": 1, **{w: i + 2 for i, w in enumerate(words)}}
    t = tk.Tokenizer(models.WordLevel(vocab, unk_token="[UNK]"))
    t.pre_tokenizer = pre_tokenizers.Whitespace()
    t.save(str(path))
    return vocab


@pytest.mark.parametrize("pooling", ["mean", "cls"])
def test_onnx_end_to_end(tmp_path, pooling):
    pytest.importorskip("onnxruntime")
    pytest.importorskip("onnx")
    pytest.importorskip("tokenizers")
    from email_town_labeler.features import email_text
    from email_town_labeler.models.backends import onnx_classifier

    src = tmp_path / "build"
    src.mkdir()
    words = ["From:", "Subject:", "hello", "invoice", "Body:", "no", "yes"]
    vocab = _tiny_tokenizer(src / "tokenizer.json", words)
    table = _tiny_onnx(src / "encoder.onnx", len(vocab), 8)
    pkg = head_package(
        tmp_path / "m",
        dim=8,
        backend="onnx_classifier",
        extra_files={
            "encoder.onnx": (src / "encoder.onnx").read_bytes(),
            "tokenizer.json": (src / "tokenizer.json").read_bytes(),
        },
        config={
            "onnx": "encoder.onnx",
            "tokenizer": "tokenizer.json",
            "pooling": pooling,
            "normalize": True,
            "max_seq_length": 64,
            "batch_size": 2,
        },
    )
    m = onnx_classifier.load(ModelManifest.load(pkg))
    rs = [rec(0, subject="hello"), rec(1, subject="invoice invoice invoice"), rec(2, subject="x")]
    preds = m.predict(rs)
    # reference: tokenize by hand, embed via the table, pool, normalise, head
    tok = m.embedder.tok
    X = []
    for r in rs:
        ids = tok.encode(email_text(r)).ids
        v = table[ids].mean(axis=0) if pooling == "mean" else table[ids[0]]
        X.append(v / np.linalg.norm(v))
    logits = np.array(X) @ np.load(pkg / "coef.npy").T + np.load(pkg / "intercept.npy")
    P = np.exp(logits - logits.max(1, keepdims=True))
    P /= P.sum(1, keepdims=True)
    for p, row in zip(preds, P):
        assert np.allclose([p.probs[c] for c in CLASSES], row, atol=1e-5)


@pytest.mark.parametrize(
    "key,bad",
    [
        ("onnx", "../encoder.onnx"),
        ("onnx", "/abs/encoder.onnx"),
        ("tokenizer", "x/../../tokenizer.json"),
        ("tokenizer", "C:\\tok.json"),
        ("onnx", 7),
    ],
)
def test_manifest_paths_must_stay_in_package(tmp_path, key, bad):
    """Package-supplied onnx/tokenizer paths go through hub.safe_relpath and stay inside the package dir."""
    from email_town_labeler.models.backends import onnx_classifier
    from email_town_labeler.models.hub import HubError

    pkg = head_package(tmp_path / "m", dim=8, backend="onnx_classifier", config={key: bad})
    with pytest.raises(HubError):
        onnx_classifier.load(ModelManifest.load(pkg))
