import functools
import http.server
import io
import json
import shutil
import tarfile
import threading
import zipfile
from pathlib import Path

import pytest

from email_town_labeler.models import hub
from email_town_labeler.models.base import ModelManifest
from tests._model_fixtures import head_package


@pytest.fixture
def server(tmp_path):
    """Serve tmp_path/www over http://127.0.0.1:<port>/ (records request paths)."""
    root = tmp_path / "www"
    root.mkdir()
    seen: list[str] = []

    class H(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            seen.append(self.path)
            super().do_GET()

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(H, directory=str(root)))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield root, f"http://127.0.0.1:{srv.server_address[1]}", seen
    srv.shutdown()


def publish_hf(root: Path, pkg: Path, repo="org/tiny", rev="main") -> Path:
    dest = root / repo / "resolve" / rev
    shutil.copytree(pkg, dest)
    return dest


def test_hfref_parse():
    assert hub.HFRef.parse("hf:org/repo") == hub.HFRef("org/repo", "main")
    assert str(hub.HFRef.parse("hf:org/repo@v1.2")) == "hf:org/repo@v1.2"
    for bad in ("hf:repo", "hf:../x/y", "hf:org/repo/extra"):
        with pytest.raises(hub.HubError):
            hub.HFRef.parse(bad)


def test_fetch_hf_plain_https(tmp_path, server, monkeypatch):
    root, base, seen = server
    publish_hf(root, head_package(tmp_path / "src", extra_files={"sub/extra.txt": b"x"}), rev="abc123")
    monkeypatch.setenv("HF_ENDPOINT", base)
    models = tmp_path / "models"
    d = hub.fetch("hf:org/tiny@abc123", models, use_hf_hub=False)
    assert d == models / "tiny" and (d / "sub" / "extra.txt").read_bytes() == b"x"
    assert hub.verify(d).name == "tiny"
    assert hub.source_of(d)["ref"] == "hf:org/tiny@abc123"
    assert "/org/tiny/resolve/abc123/model.json" in seen
    assert hub.find_installed("hf:org/tiny@abc123", models) == d
    assert hub.find_installed("hf:org/tiny@other", models) is None
    assert not [p for p in models.iterdir() if p.name.startswith(".")]  # temp dirs cleaned up


def test_fetch_hf_sha_mismatch_leaves_nothing(tmp_path, server, monkeypatch):
    root, base, _ = server
    dest = publish_hf(root, head_package(tmp_path / "src"))
    (dest / "coef.npy").write_bytes(b"tampered")
    monkeypatch.setenv("HF_ENDPOINT", base)
    models = tmp_path / "models"
    with pytest.raises(hub.HubError, match="sha256 mismatch"):
        hub.fetch("hf:org/tiny", models, use_hf_hub=False)
    assert list(models.iterdir()) == []


def test_fetch_hf_missing_file(tmp_path, server, monkeypatch):
    root, base, _ = server
    dest = publish_hf(root, head_package(tmp_path / "src"))
    (dest / "intercept.npy").unlink()
    monkeypatch.setenv("HF_ENDPOINT", base)
    with pytest.raises(hub.HubError, match="download failed"):
        hub.fetch("hf:org/tiny", tmp_path / "models", use_hf_hub=False)


def _tar(pkg: Path, out: Path, prefix: str = "") -> Path:
    with tarfile.open(out, "w:gz") as t:
        for p in sorted(pkg.rglob("*")):
            t.add(p, arcname=prefix + p.relative_to(pkg).as_posix(), recursive=False)
    return out


def _zip(pkg: Path, out: Path, prefix: str = "") -> Path:
    with zipfile.ZipFile(out, "w") as z:
        for p in sorted(pkg.rglob("*")):
            if p.is_file():
                z.write(p, prefix + p.relative_to(pkg).as_posix())
    return out


@pytest.mark.parametrize("kind", ["tar", "zip"])
def test_fetch_archive_over_http_and_file(tmp_path, server, kind):
    root, base, _ = server
    pkg = head_package(tmp_path / "src", name="arch")
    make = _tar if kind == "tar" else _zip
    arc = make(pkg, root / ("arch.tar.gz" if kind == "tar" else "arch.zip"), prefix="arch-1/")
    models = tmp_path / "models"
    d = hub.fetch(f"{base}/{arc.name}", models)
    assert d == models / "arch" and ModelManifest.load(d).name == "arch"
    # reinstall over the existing copy via file://, atomically replacing it
    d2 = hub.fetch(arc.as_uri(), models)
    assert d2 == d and hub.source_of(d)["ref"] == arc.as_uri()
    assert sorted(p.name for p in models.iterdir()) == ["arch"]


def test_refuses_plain_http_off_loopback(tmp_path):
    with pytest.raises(hub.HubError, match="refusing"):
        hub.fetch("http://example.com/x.tar.gz", tmp_path)
    with pytest.raises(hub.HubError, match="refusing"):
        hub.fetch("ftp://example.com/x.tar.gz", tmp_path)


@pytest.mark.parametrize("evil", ["../evil.txt", "/abs/evil.txt", "a/../../evil.txt", "C:/evil.txt"])
def test_zip_path_traversal_refused(tmp_path, evil):
    arc = tmp_path / "evil.zip"
    with zipfile.ZipFile(arc, "w") as z:
        z.writestr("model.json", "{}")
        z.writestr(evil, "boom")
    with pytest.raises(hub.HubError, match="unsafe path"):
        hub.fetch(str(arc), tmp_path / "models")
    assert not (tmp_path / "evil.txt").exists()


def test_tar_traversal_and_links_refused(tmp_path):
    for name, member in [("dots.tar.gz", "../evil.txt"), ("abs.tar.gz", "/tmp/evil.txt")]:
        arc = tmp_path / name
        with tarfile.open(arc, "w:gz") as t:
            info = tarfile.TarInfo(member)
            info.size = 4
            t.addfile(info, io.BytesIO(b"boom"))
        with pytest.raises(hub.HubError, match="unsafe path"):
            hub.fetch(str(arc), tmp_path / "models")
    arc = tmp_path / "link.tar.gz"
    with tarfile.open(arc, "w:gz") as t:
        info = tarfile.TarInfo("model.json")
        info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
        t.addfile(info)
    with pytest.raises(hub.HubError, match="non-regular"):
        hub.fetch(str(arc), tmp_path / "models")


def test_manifest_file_paths_must_be_safe(tmp_path):
    pkg = head_package(tmp_path / "src")
    m = json.loads((pkg / "model.json").read_text())
    m["files"]["../outside"] = "0" * 64
    (pkg / "model.json").write_text(json.dumps(m))
    with pytest.raises(hub.HubError, match="unsafe path"):
        hub.verify(pkg)


def test_bad_name_refused(tmp_path):
    pkg = head_package(tmp_path / "src", name="../up")
    with pytest.raises(hub.HubError, match="bad model name"):
        hub.install_dir(pkg, tmp_path / "models")


def test_archive_without_manifest(tmp_path):
    arc = tmp_path / "x.zip"
    with zipfile.ZipFile(arc, "w") as z:
        z.writestr("a/b.txt", "x")
        z.writestr("c/d.txt", "x")
    with pytest.raises(hub.HubError, match="no model.json"):
        hub.fetch(str(arc), tmp_path / "models")


def test_fetch_local_package_directory(tmp_path):
    src = head_package(tmp_path / "src", name="localpkg")
    d = hub.fetch(str(src), tmp_path / "models")
    assert d == tmp_path / "models" / "localpkg" and ModelManifest.load(d).name == "localpkg"
    assert (src / "model.json").is_file()  # the source directory is copied, not consumed
    with pytest.raises(hub.HubError, match="no such file"):
        hub.fetch(str(tmp_path / "nope"), tmp_path / "models")


def test_hf_token_is_not_forwarded_on_redirect(tmp_path, monkeypatch):
    seen = []

    def fake_urlopen(req, timeout=None):
        seen.append(req)
        raise OSError("offline test")

    monkeypatch.setattr(hub.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(hub.HubError):
        hub._download("https://huggingface.co/o/r/resolve/main/model.json", tmp_path / "m.json", token="hf_x")
    (req,) = seen
    assert req.unredirected_hdrs.get("Authorization") == "Bearer hf_x" and "Authorization" not in req.headers


@pytest.mark.parametrize("kind", ["tar", "zip"])
def test_extraction_size_caps(tmp_path, kind):
    """A small archive must not be able to fill the disk: per-file and total extracted-size caps."""
    pkg = head_package(
        tmp_path / "src", name="big", extra_files={"a.bin": b"\0" * 6000, "b.bin": b"\0" * 6000}
    )
    arc = (_tar if kind == "tar" else _zip)(pkg, tmp_path / ("big.tar.gz" if kind == "tar" else "big.zip"))
    models = tmp_path / "models"
    with pytest.raises(hub.HubError, match="per-file cap"):
        hub.fetch(str(arc), models, max_file_bytes=5000)
    with pytest.raises(hub.HubError, match="byte cap"):
        hub.fetch(str(arc), models, max_extract_bytes=9000)
    assert not (models / "big").exists() and not [p for p in models.iterdir() if p.name.startswith(".dl-")]
    assert hub.fetch(str(arc), models, max_extract_bytes=1 << 20, max_file_bytes=1 << 20) == models / "big"
    assert hub.MAX_EXTRACT_BYTES == 4 << 30 and hub.MAX_FILE_BYTES <= hub.MAX_EXTRACT_BYTES


def test_extraction_cap_counts_copied_bytes(tmp_path):
    """Declared sizes fail fast; the copy loop counts the bytes actually written as the backstop."""
    out = tmp_path / "x"
    src = tmp_path / "a.tar"
    data = b"\1" * 3000
    with tarfile.open(src, "w") as t:
        ti = tarfile.TarInfo("model.json")
        ti.size = len(data)
        t.addfile(ti, io.BytesIO(data))
    with pytest.raises(hub.HubError, match="cap"):
        hub._extract(src, out, max_total_bytes=1000, max_file_bytes=10_000)
    b = hub._Budget(max_total=10, max_file=100)
    with pytest.raises(hub.HubError, match="byte cap"):
        b.copy("m", io.BytesIO(b"x" * 11), io.BytesIO())
