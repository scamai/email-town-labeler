"""The known-model catalog, archive sha256 pinning and download progress. Local file:// only, never GitHub."""

from __future__ import annotations

import io
import re
import tarfile
from pathlib import Path

import pytest

from email_town_labeler.models import catalog, hub, registry
from email_town_labeler.models.backends._common import MissingExtraError, sha256_file
from tests._model_fixtures import head_package

BAD = "0" * 64


def _tar(pkg: Path, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(out, "w:gz") as t:
        t.add(pkg, arcname=pkg.name)
    return out


@pytest.fixture(autouse=True)
def _offline(tmp_path, monkeypatch):
    monkeypatch.setenv(catalog.BASE_URL_ENV, (tmp_path / "no-release").as_uri())


@pytest.fixture
def release(tmp_path, monkeypatch):
    """A fake release dir served via EMAIL_TOWN_LABELER_CATALOG_BASE_URL and a catalog entry `tiny-cat`."""
    rel = tmp_path / "release"
    arc = _tar(head_package(tmp_path / "src" / "tiny-pkg", name="tiny-pkg"), rel / "tiny-pkg-v1.tar.gz")
    entry = catalog.CatalogEntry(
        "tiny-cat", "tiny-pkg", arc.name, sha256_file(arc), arc.stat().st_size, "onnx", "test entry"
    )
    monkeypatch.setitem(catalog.CATALOG, entry.key, entry)
    monkeypatch.setenv(catalog.BASE_URL_ENV, rel.as_uri() + "/")
    return entry, arc


def test_published_entries_are_pinned():
    assert catalog.DEFAULT == "town-modernbert" and catalog.DEFAULT in catalog.CATALOG
    for e in catalog.CATALOG.values():
        assert re.fullmatch(r"[0-9a-f]{64}", e.sha256) and e.size > 100_000_000
        assert e.extra in catalog.EXTRA_MODULES and e.description
    assert catalog.CATALOG["town-modernbert"].extra == "onnx"
    assert catalog.CATALOG["town-modernbert-torch"].extra == "embed"
    assert catalog.CATALOG["town-modernbert-int8"].extra == "onnx"


def test_url_and_base_override(monkeypatch):
    e = catalog.CATALOG["town-modernbert"]
    monkeypatch.delenv(catalog.BASE_URL_ENV, raising=False)
    assert e.url == (
        "https://github.com/scamai/email-town-labeler/releases/download/v0.1.0/"
        "town-labels-modernbert-large-onnx-v1.tar.gz"
    )
    monkeypatch.setenv(catalog.BASE_URL_ENV, "file:///srv/mirror")
    assert e.url == "file:///srv/mirror/town-labels-modernbert-large-onnx-v1.tar.gz"


def test_fetch_url_sha256_pin(tmp_path, release):
    _, arc = release
    models = tmp_path / "models"
    for src in (arc.as_uri(), str(arc)):  # file:// download and a local archive path
        with pytest.raises(hub.HubError, match="sha256 mismatch"):
            hub.fetch_url(src, models, sha256=BAD)
        assert list(models.iterdir()) == []  # nothing extracted, temp dirs gone
    d = hub.fetch_url(arc.as_uri(), models, sha256=sha256_file(arc).upper())
    assert d == models / "tiny-pkg" and hub.source_of(d)["sha256"] == sha256_file(arc)
    with pytest.raises(hub.HubError, match="64 hex"):
        hub.fetch_url(arc.as_uri(), models, sha256="abc")


def test_resolve_catalog_name_downloads_once(tmp_path, release):
    entry, _ = release
    models = tmp_path / "models"
    with pytest.raises(registry.UnknownModelError, match="model pull tiny-cat"):
        registry.resolve("tiny-cat", models_dir=models, download=False)
    man = registry.resolve("tiny-cat", models_dir=models)
    assert man.name == "tiny-pkg" and catalog.installed(entry, models) == models / "tiny-pkg"
    assert hub.source_of(models / "tiny-pkg")["catalog"] == "tiny-cat"
    assert registry.resolve("tiny-cat", models_dir=models, download=False).name == "tiny-pkg"


def test_catalog_download_refuses_wrong_hash(tmp_path, release, monkeypatch):
    entry, _ = release
    monkeypatch.setitem(catalog.CATALOG, entry.key, catalog.CatalogEntry(**{**entry.__dict__, "sha256": BAD}))
    models = tmp_path / "models"
    with pytest.raises(hub.HubError, match="sha256 mismatch"):
        registry.resolve("tiny-cat", models_dir=models)
    assert not (models / "tiny-pkg").exists()


def test_load_model_checks_the_extra_before_downloading(tmp_path, release, monkeypatch):
    monkeypatch.setattr(catalog, "extra_installed", lambda extra: False)
    with pytest.raises(MissingExtraError, match=r"email-town-labeler\[onnx\]"):
        registry.load_model("tiny-cat", models_dir=tmp_path / "models")
    assert not (tmp_path / "models" / "tiny-pkg").exists()


def test_extra_installed(monkeypatch):
    present = {"onnxruntime"}
    monkeypatch.setattr(catalog.importlib.util, "find_spec", lambda m: object() if m in present else None)
    assert not catalog.extra_installed("onnx")  # tokenizers missing
    present.add("tokenizers")
    assert catalog.extra_installed("onnx") and not catalog.extra_installed("embed")


def test_progress_lines_without_a_tty():
    out = io.StringIO()
    bar = hub.Progress("x.tar.gz", 1_000_000_000, stream=out)
    for n in range(0, 1_000_000_001, 50_000_000):
        bar.update(n)
    bar.done(1_000_000_000)
    lines = out.getvalue().splitlines()
    assert lines[0].endswith("(0%)") and lines[-1].endswith("1.0 GB / 1.0 GB (100%)")
    assert len(lines) == 11  # every 10%, no duplicate final line


def test_download_progress_goes_to_stderr(tmp_path, release, capsys):
    _, arc = release
    hub.fetch_url(arc.as_uri(), tmp_path / "models", progress=True)
    err = capsys.readouterr().err
    assert "downloading tiny-pkg-v1.tar.gz" in err and "(100%)" in err


def test_human_size():
    assert catalog.human_size(823_760_697) == "824 MB"
    assert catalog.human_size(1_584_174_281) == "1.6 GB"
    assert catalog.human_size(512) == "512 B"
