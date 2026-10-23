import json

import numpy as np
import pytest

from email_town_labeler.models import registry
from email_town_labeler.models.backends._common import sha256_file
from email_town_labeler.personalize import bump_version, fit_head, personalize
from tests._model_fixtures import HashEmbedder, head_package, rec


@pytest.fixture
def base(tmp_path):
    return head_package(tmp_path / "models" / "tiny", dim=16, name="tiny", version="3")


def corrections(n, cls="travel"):
    return [(rec(100 + i, subject=f"made-up subject {i}"), cls) for i in range(n)]


def test_bump_version():
    assert [bump_version(v) for v in ("1", "1.9", "2026.10.3", "beta")] == [
        "2",
        "1.10",
        "2026.10.4",
        "beta.1",
    ]


def test_fit_head_anchor_and_strength():
    rng = np.random.default_rng(0)
    X = rng.standard_normal((4, 5))
    W0, b0 = rng.standard_normal((3, 5)), np.zeros(3)
    y = np.array([2, 2, 2, 2])
    weak = fit_head(X, y, W0, b0, lam=100.0)
    strong = fit_head(X, y, W0, b0, lam=0.1)
    d_weak, d_strong = np.linalg.norm(weak[0] - W0), np.linalg.norm(strong[0] - W0)
    assert 0 < d_weak < d_strong
    # first-order optimality: gradient ~ 0
    W, b = strong
    Z = X @ W.T + b
    P = np.exp(Z - Z.max(1, keepdims=True))
    P /= P.sum(1, keepdims=True)
    g = (P - np.eye(3)[y]).T @ X + 0.1 * (W - W0)
    assert np.abs(g).max() < 1e-4


def test_personalize_nudges_and_writes_new_version(tmp_path, base):
    emb = HashEmbedder(16)
    ex = corrections(5)
    before = registry.load_model(str(base), models_dir=tmp_path, embedder=emb).predict([r for r, _ in ex])
    out = tmp_path / "models"
    man = personalize(base, ex, out, embedder=emb)
    assert man.name == "tiny-personal" and man.version == "4" and man.path == out / "tiny-personal"
    pm = man.metrics["personalized"]
    assert pm["n_examples"] == 5 and pm["class_counts"] == {"travel": 5} and pm["base"] == "tiny@3"
    assert pm["train_acc_after"] == 1.0 and man.metrics["base"] == {"accuracy": 0.5}
    for rel, digest in man.files.items():
        assert sha256_file(man.path / rel) == digest
    after = registry.load_model("tiny-personal", models_dir=out, embedder=emb).predict([r for r, _ in ex])
    assert all(a.cls == "travel" for a in after)
    assert all(a.probs["travel"] > b.probs["travel"] for a, b in zip(after, before))
    # original untouched; unrelated mail mostly keeps its class
    assert json.loads((base / "model.json").read_text())["version"] == "3"
    others = [rec(500 + i, subject=f"other {i}") for i in range(50)]
    b0 = registry.load_model(str(base), models_dir=tmp_path, embedder=emb).predict(others)
    a0 = registry.load_model(str(man.path), models_dir=tmp_path, embedder=emb).predict(others)
    assert sum(x.cls == y.cls for x, y in zip(a0, b0)) >= 35


def test_one_correction_is_a_nudge_not_an_overwrite(tmp_path, base):
    emb = HashEmbedder(16)
    man = personalize(base, corrections(1), tmp_path / "out", strength=0.05, embedder=emb)
    assert man.metrics["personalized"]["coef_shift_l2"] < 0.2


def test_repersonalize_anchors_to_shipped_head(tmp_path, base):
    emb = HashEmbedder(16)
    out = tmp_path / "models"
    m1 = personalize(base, corrections(3), out, embedder=emb)
    m2 = personalize(m1.path, corrections(3) + corrections(2, "orders"), out, embedder=emb)
    assert m2.name == "tiny-personal" and m2.version == "5" and m2.metrics["personalized"]["base"] == "tiny@3"
    assert np.allclose(np.load(m2.path / "base_coef.npy"), np.load(base / "coef.npy"))
    assert m2.metrics["base"] == {"accuracy": 0.5}
    assert not [p for p in out.iterdir() if p.name.startswith(".")]


def test_rejects_bad_input(tmp_path, base):
    with pytest.raises(ValueError, match="at least one"):
        personalize(base, [], tmp_path, embedder=HashEmbedder(16))
    with pytest.raises(ValueError, match="classes"):
        personalize(base, [(rec(), "bogus")], tmp_path, embedder=HashEmbedder(16))
    d = tmp_path / "r"
    d.mkdir()
    from email_town_labeler.models.base import ModelManifest

    ModelManifest(name="r", version="1", backend="rules").save(d)
    with pytest.raises(ValueError, match="cannot personalise"):
        personalize(d, corrections(1), tmp_path)
