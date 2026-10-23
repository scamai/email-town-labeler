import functools
import http.server
import shutil
import threading

import pytest

from email_town_labeler.models import registry
from email_town_labeler.models.backends._common import MissingExtraError
from email_town_labeler.models.base import Classifier, ModelManifest
from email_town_labeler.taxonomy import CLASSES
from tests._model_fixtures import HashEmbedder, head_package, rec


def test_rules_builtin(tmp_path):
    m = registry.load_model("rules", models_dir=tmp_path)
    assert isinstance(m, Classifier) and m.manifest.id == "rules@1"
    assert set(m.predict([rec()])[0].probs) == set(CLASSES)


def test_local_dir_and_installed_name(tmp_path):
    pkg = head_package(tmp_path / "models" / "tiny")
    emb = HashEmbedder(16)
    for ref in (str(pkg), "tiny"):
        m = registry.load_model(ref, models_dir=tmp_path / "models", embedder=emb)
        (p,) = m.predict([rec()])
        assert m.manifest.name == "tiny" and set(p.probs) == set(CLASSES)
        assert abs(sum(p.probs.values()) - 1) < 1e-9


def test_unknown_ref_and_backend(tmp_path):
    with pytest.raises(registry.UnknownModelError, match="no model"):
        registry.load_model("nope", models_dir=tmp_path)
    pkg = head_package(tmp_path / "x", backend="quantum_magic")
    with pytest.raises(registry.UnknownModelError, match=r"email-town-labeler\[embed\]"):
        registry.load_model(str(pkg), models_dir=tmp_path)


def test_missing_extra_names_it(tmp_path):
    try:
        import sentence_transformers  # noqa: F401

        pytest.skip("sentence-transformers installed")
    except ImportError:
        pass
    pkg = head_package(tmp_path / "x")
    with pytest.raises(
        MissingExtraError,
        match=r'pip install "email-town-labeler\[embed\] @ git\+https://github.com/scamai/email-town-labeler"',
    ):
        registry.load_model(str(pkg), models_dir=tmp_path)


def test_register_custom_backend(tmp_path):
    class Const:
        def __init__(self, man):
            self.manifest = man

        def predict(self, records):
            from email_town_labeler.models.backends._common import to_predictions

            return to_predictions([[1.0] + [0.0] * 6 for _ in records], CLASSES)

    registry.register_backend("const", lambda man, device="cpu", **kw: Const(man))
    try:
        d = tmp_path / "c"
        d.mkdir()
        ModelManifest(name="c", version="1", backend="const").save(d)
        assert registry.load_model(str(d), models_dir=tmp_path).predict([rec()])[0].cls == CLASSES[0]
        assert "const" in registry.backend_names()
    finally:
        registry._custom.pop("const", None)


def test_list_models(tmp_path):
    head_package(tmp_path / "b", name="b")
    head_package(tmp_path / "a", name="a")
    (tmp_path / ".stage-x").mkdir()
    (tmp_path / "junk").mkdir()
    assert [m.name for m in registry.list_models(tmp_path)] == ["rules", "a", "b"]
    assert [m.name for m in registry.list_models(tmp_path / "missing")] == ["rules"]


def test_hf_ref_downloads_once(tmp_path, monkeypatch):
    root = tmp_path / "www"
    shutil.copytree(head_package(tmp_path / "src"), root / "org" / "tiny" / "resolve" / "v1")
    hits: list[str] = []

    class H(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            hits.append(self.path)
            super().do_GET()

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(H, directory=str(root)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("HF_ENDPOINT", f"http://127.0.0.1:{srv.server_address[1]}")
        monkeypatch.setattr(registry.hub, "fetch", functools.partial(registry.hub.fetch, use_hf_hub=False))
        models = tmp_path / "models"
        with pytest.raises(registry.UnknownModelError, match="not installed"):
            registry.resolve("hf:org/tiny@v1", models_dir=models, download=False)
        for _ in range(2):
            m = registry.load_model("hf:org/tiny@v1", models_dir=models, embedder=HashEmbedder(16))
            assert m.manifest.path == models / "tiny"
        assert len(hits) == 3  # model.json + 2 head files, fetched once
    finally:
        srv.shutdown()
