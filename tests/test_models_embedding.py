from typing import ClassVar

import numpy as np
import pytest

from email_town_labeler.models.backends import embedding_probe
from email_town_labeler.models.backends._common import SoftmaxHead, softmax, to_predictions
from email_town_labeler.models.base import ModelManifest
from email_town_labeler.taxonomy import CLASSES
from tests._model_fixtures import HashEmbedder, head_package, rec


def test_head_path_matches_numpy(tmp_path):
    pkg = head_package(tmp_path / "m", dim=8)
    emb = HashEmbedder(8)
    m = embedding_probe.load(ModelManifest.load(pkg), embedder=emb)
    rs = [rec(i, subject=f"s{i}") for i in range(5)]
    preds = m.predict(rs)
    from email_town_labeler.features import email_text

    X = emb.embed([email_text(r) for r in rs])
    P = softmax(X @ np.load(pkg / "coef.npy").T + np.load(pkg / "intercept.npy"))
    for p, row in zip(preds, P):
        assert p.cls == CLASSES[int(row.argmax())]
        assert np.allclose([p.probs[c] for c in CLASSES], row)
    assert m.predict([]) == []


def test_head_validation():
    with pytest.raises(ValueError, match="shape mismatch"):
        SoftmaxHead(np.zeros((3, 4)), np.zeros(3), CLASSES)
    with pytest.raises(ValueError, match="CLASSES"):
        SoftmaxHead(np.zeros((2, 4)), np.zeros(2), ["orders", "bogus"])
    h = SoftmaxHead(np.zeros((2, 4)), np.zeros(2), ["orders", "travel"])
    with pytest.raises(ValueError, match="head expects"):
        h.proba(np.zeros((1, 5)))


def test_subset_classes_padded_to_all():
    (p,) = to_predictions(np.array([[0.2, 0.8]]), ["orders", "travel"])
    assert set(p.probs) == set(CLASSES) and p.cls == "travel" and p.probs["unknown"] == 0.0


def test_dim_mismatch_is_clear(tmp_path):
    m = embedding_probe.load(
        ModelManifest.load(head_package(tmp_path / "m", dim=8)), embedder=HashEmbedder(9)
    )
    with pytest.raises(ValueError, match="head expects"):
        m.predict([rec()])


def test_real_sentence_transformers_tiny(tmp_path):
    """Opt-in: only if sentence-transformers is installed AND a tiny model is already in the HF cache."""
    pytest.importorskip("sentence_transformers")
    import os

    repo = os.environ.get("EMAIL_TOWN_LABELER_TEST_ST_MODEL")
    if not repo:
        pytest.skip("set EMAIL_TOWN_LABELER_TEST_ST_MODEL=<cached tiny sentence-transformers repo> to run")
    pkg = head_package(
        tmp_path / "m",
        dim=int(os.environ.get("EMAIL_TOWN_LABELER_TEST_ST_DIM", "384")),
        config={"embedder": {"repo": repo}, "max_seq_length": 128},
    )
    (p,) = embedding_probe.load(ModelManifest.load(pkg)).predict([rec()])
    assert set(p.probs) == set(CLASSES)


class _FakeST:
    calls: ClassVar[list] = []

    def __init__(self, repo, **kw):
        _FakeST.calls.append((repo, kw))
        self.max_seq_length = 512


def _fake_require(monkeypatch):
    import types

    torch = types.SimpleNamespace(
        float32="f32", bfloat16="bf16", cuda=types.SimpleNamespace(is_available=lambda: False)
    )
    st = types.SimpleNamespace(SentenceTransformer=_FakeST)
    monkeypatch.setattr(embedding_probe, "require", lambda mod, backend="": torch if mod == "torch" else st)
    _FakeST.calls = []


def test_st_kwargs_allowlist_and_trust_remote_code_forced_off(monkeypatch):
    """Blocker: st_kwargs is package-supplied and must not be able to make sentence-transformers run code."""
    _fake_require(monkeypatch)
    cfg = {
        "embedder": {
            "repo": "org/enc",
            "revision": "abc",
            "st_kwargs": {
                "config_kwargs": {"attn_implementation": "eager"},
                "tokenizer_kwargs": {"model_max_length": 64},
                "model_kwargs": {"torch_dtype": "float16"},
            },
        }
    }
    embedding_probe.SentenceTransformerEmbedder(cfg)
    ((repo, kw),) = _FakeST.calls
    assert repo == "org/enc" and kw["revision"] == "abc" and kw["trust_remote_code"] is False
    assert kw["model_kwargs"] == {"torch_dtype": "float16", "trust_remote_code": False}
    assert kw["config_kwargs"] == {"attn_implementation": "eager", "trust_remote_code": False}
    assert kw["tokenizer_kwargs"] == {"model_max_length": 64, "trust_remote_code": False}
    embedding_probe.SentenceTransformerEmbedder({"embedder": {"repo": "org/enc"}})
    assert _FakeST.calls[1][1] == {
        "device": "cpu",
        "trust_remote_code": False,
        "model_kwargs": {"torch_dtype": "f32", "trust_remote_code": False},
    }


@pytest.mark.parametrize(
    "st_kwargs,match",
    [
        ({"trust_remote_code": True}, "refusing"),
        ({"modules": ["x"]}, "refusing"),
        ({"cache_folder": "/tmp"}, "refusing"),
        ({"model_kwargs": {"trust_remote_code": True}}, "refusing"),
        ({"model_kwargs": {"device_map": "auto"}}, "refusing"),
        ({"model_kwargs": {"torch_dtype": "evil.module"}}, "one of"),
        ({"config_kwargs": {"trust_remote_code": True}}, "trust_remote_code"),
        ({"tokenizer_kwargs": {"revision": "unpinned"}}, "refusing"),
        ({"tokenizer_kwargs": {"token": "hf_x"}}, "refusing"),
        ({"config_kwargs": "nope"}, "must be an object"),
        (["x"], "must be an object"),
    ],
)
def test_unsafe_st_kwargs_refused(monkeypatch, st_kwargs, match):
    _fake_require(monkeypatch)
    with pytest.raises((TypeError, ValueError), match=match):
        embedding_probe.SentenceTransformerEmbedder({"embedder": {"repo": "org/enc", "st_kwargs": st_kwargs}})
    assert _FakeST.calls == []


@pytest.mark.parametrize("bad", ["../coef.npy", "/etc/coef.npy", "sub/../../coef.npy", "C:/coef.npy"])
def test_head_paths_must_stay_in_package(tmp_path, bad):
    from email_town_labeler.models.hub import HubError

    pkg = head_package(tmp_path / "m", dim=8, config={"head": {"coef": bad, "intercept": "intercept.npy"}})
    with pytest.raises(HubError):
        embedding_probe.load(ModelManifest.load(pkg), embedder=HashEmbedder(8))


def test_head_symlink_out_of_package_refused(tmp_path):
    import os

    from email_town_labeler.models.hub import HubError

    pkg = head_package(tmp_path / "m", dim=8)
    outside = tmp_path / "outside.npy"
    np.save(outside, np.zeros((len(CLASSES), 8)))
    try:
        os.symlink(outside, pkg / "link.npy")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    man = ModelManifest.load(pkg)
    man.config["head"] = {"coef": "link.npy", "intercept": "intercept.npy"}
    with pytest.raises(HubError, match="outside"):
        embedding_probe.load(man, embedder=HashEmbedder(8))
