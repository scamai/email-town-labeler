"""Fetch model packages: Hugging Face Hub repos or https/file URLs of .tar.gz/.zip archives.

Every file listed in model.json `files` is sha256-checked, then the package is installed
atomically to `models_dir/<name>/` (temp dir + rename). `fetch_url(..., sha256=)` also pins the archive
itself: it is hashed while downloading and refused on a mismatch before anything is extracted. Archives with absolute paths, `..`,
links or device entries are refused, and extraction stops at a per-file and a total size cap
(MAX_FILE_BYTES / MAX_EXTRACT_BYTES by default) so a small archive cannot fill the disk. Network calls happen only when a caller asks for them.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import tarfile
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, TextIO

from .backends._common import sha256_file
from .base import MANIFEST, ModelManifest

SOURCE = ".source.json"  # written next to model.json: where the package came from
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
MAX_BYTES = 8 << 30  # one download
MAX_EXTRACT_BYTES = 4 << 30  # everything extracted from one archive
MAX_FILE_BYTES = 2 << 30  # one extracted file (ONNX graphs over 2 GB use external data files anyway)
LOOPBACK = ("localhost", "127.0.0.1", "::1")


class HubError(RuntimeError):
    pass


@dataclass(frozen=True)
class HFRef:
    repo: str
    revision: str = "main"

    @classmethod
    def parse(cls, ref: str) -> HFRef:
        body = ref.removeprefix("hf:")
        repo, _, rev = body.partition("@")
        if not re.fullmatch(r"[A-Za-z0-9][\w.-]*/[\w.-]+", repo):
            raise HubError(f"bad Hugging Face ref {ref!r}; expected hf:<org>/<repo>[@revision]")
        return cls(repo, rev or "main")

    def __str__(self) -> str:
        return f"hf:{self.repo}@{self.revision}"


def safe_relpath(p: str) -> PurePosixPath:
    """A manifest/archive member path that stays inside its root, or HubError."""
    q = PurePosixPath(p.replace("\\", "/"))
    if not p or q.is_absolute() or ".." in q.parts or re.match(r"^[A-Za-z]:", p) or str(q) in ("", "."):
        raise HubError(f"unsafe path in model package: {p!r}")
    return q


def verify(directory: Path) -> ModelManifest:
    """Load model.json from `directory` and check every listed file's sha256."""
    man = ModelManifest.load(directory)
    if not NAME_RE.match(man.name or ""):
        raise HubError(f"bad model name {man.name!r}")
    for rel, digest in man.files.items():
        f = Path(directory) / safe_relpath(rel)
        if not f.is_file():
            raise HubError(f"model package is missing {rel!r}")
        if sha256_file(f) != str(digest).lower():
            raise HubError(f"sha256 mismatch for {rel!r}")
    return man


def install_dir(src: Path, models_dir: Path, *, source: dict | None = None, name: str | None = None) -> Path:
    """Verify `src` and move it to models_dir/<name>/ atomically (src is consumed)."""
    man = verify(src)
    name = name or man.name
    if not NAME_RE.match(name):
        raise HubError(f"bad model name {name!r}")
    models_dir = Path(models_dir)
    models_dir.mkdir(parents=True, exist_ok=True)
    if source is not None:
        (Path(src) / SOURCE).write_text(json.dumps(source, indent=2) + "\n", encoding="utf-8")
    dest, stage = models_dir / name, Path(tempfile.mkdtemp(prefix=f".stage-{name}-", dir=models_dir))
    shutil.rmtree(stage)
    shutil.move(str(src), stage)  # same filesystem as dest from here on, so the renames are atomic
    old = None
    if dest.exists():
        old = Path(tempfile.mkdtemp(prefix=f".old-{name}-", dir=models_dir))
        shutil.rmtree(old)
        os.replace(dest, old)
    try:
        os.replace(stage, dest)
    except OSError:
        if old is not None:
            os.replace(old, dest)
        raise
    if old is not None:
        shutil.rmtree(old, ignore_errors=True)
    return dest


# ------------------------------------------------------------------ transport


class Progress:
    """Download progress on stderr (bytes and %): a redrawn line on a terminal, every 10% otherwise."""

    def __init__(self, name: str, total: int | None, stream: TextIO | None = None):
        self.name, self.total, self.stream = name, total or None, stream or sys.stderr
        self.tty = bool(getattr(self.stream, "isatty", lambda: False)())
        self.last_t, self.last_step = 0.0, -1

    def _line(self, n: int) -> str:
        from .catalog import human_size

        pct = f" ({100 * n // self.total}%)" if self.total else ""
        tot = f" / {human_size(self.total)}" if self.total else ""
        return f"  downloading {self.name}: {human_size(n)}{tot}{pct}"

    def update(self, n: int) -> None:
        if self.tty:
            now = time.monotonic()
            if now - self.last_t >= 0.2:
                self.last_t = now
                print("\r" + self._line(n), end="", file=self.stream, flush=True)
        elif self.total and (step := n * 10 // self.total) > self.last_step:
            self.last_step = step
            print(self._line(n), file=self.stream, flush=True)

    def done(self, n: int) -> None:
        if self.tty:
            print("\r" + self._line(n), file=self.stream, flush=True)
        elif not self.total or self.last_step < 10:
            print(self._line(n), file=self.stream, flush=True)


def _download(
    url: str,
    dest: Path,
    *,
    token: str | None = None,
    max_bytes: int = MAX_BYTES,
    digest: Any = None,  # a hashlib object fed every block
    progress: bool = False,
) -> Path:
    """Stream `url` to `dest`, feeding `digest` (if given) and showing progress (if asked)."""
    u = urllib.parse.urlparse(url)
    scheme = u.scheme
    if scheme not in ("https", "http", "file") or (scheme == "http" and u.hostname not in LOOPBACK):
        raise HubError(f"refusing {scheme}:// URL (use https://, file://, or http:// to localhost)")
    req = urllib.request.Request(url, headers={"User-Agent": "email-town-labeler"})
    if token and scheme == "https":
        # unredirected: never forward the token to the CDN / storage host a resolve URL redirects to
        req.add_unredirected_header("Authorization", f"Bearer {token}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    try:
        with urllib.request.urlopen(req, timeout=60) as r, dest.open("wb") as f:
            try:
                total = int(r.headers.get("Content-Length") or 0)
            except ValueError:
                total = 0
            bar = Progress(dest.name, total) if progress else None
            while block := r.read(1 << 20):
                n += len(block)
                if n > max_bytes:
                    raise HubError(f"download exceeds {max_bytes} bytes: {url}")
                f.write(block)
                if digest is not None:
                    digest.update(block)
                if bar:
                    bar.update(n)
            if bar:
                bar.done(n)
    except OSError as e:
        raise HubError(f"download failed: {url}: {getattr(e, 'reason', e)}") from None
    return dest


def _hf_endpoint() -> str:
    return os.environ.get("HF_ENDPOINT", "https://huggingface.co").rstrip("/")


def _hf_file(ref: HFRef, filename: str, dest_dir: Path, use_hf_hub: bool | None) -> Path:
    safe_relpath(filename)
    if use_hf_hub is not False:
        try:
            from huggingface_hub import hf_hub_download
        except ImportError:
            if use_hf_hub:
                raise
        else:
            return Path(hf_hub_download(ref.repo, filename, revision=ref.revision, local_dir=dest_dir))
    url = (
        f"{_hf_endpoint()}/{ref.repo}/resolve/{urllib.parse.quote(ref.revision, safe='')}/"
        + urllib.parse.quote(filename)
    )
    return _download(url, dest_dir / filename, token=os.environ.get("HF_TOKEN"))


def fetch_hf(ref: str | HFRef, models_dir: Path, *, use_hf_hub: bool | None = None) -> Path:
    """Download model.json + its listed files from a HF repo; verify; install. Returns the model dir."""
    r = HFRef.parse(ref) if isinstance(ref, str) else ref
    models_dir = Path(models_dir)
    models_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".dl-", dir=models_dir))
    try:
        pkg = work / "pkg"
        pkg.mkdir()
        mj = _hf_file(r, MANIFEST, pkg, use_hf_hub)
        if mj.resolve() != (pkg / MANIFEST).resolve():
            shutil.copyfile(mj, pkg / MANIFEST)
        for rel in json.loads((pkg / MANIFEST).read_text(encoding="utf-8")).get("files", {}):
            got = _hf_file(r, rel, pkg, use_hf_hub)
            if got.resolve() != (pkg / safe_relpath(rel)).resolve():
                (pkg / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(got, pkg / rel)
        shutil.rmtree(pkg / ".cache", ignore_errors=True)  # huggingface_hub local_dir metadata
        return install_dir(pkg, models_dir, source={"ref": str(r), "repo": r.repo, "revision": r.revision})
    finally:
        shutil.rmtree(work, ignore_errors=True)


class _Budget:
    """Counts extracted bytes against the per-file and total caps (actual bytes, not archive headers)."""

    def __init__(self, max_total: int, max_file: int):
        self.max_total, self.max_file, self.total = int(max_total), int(max_file), 0

    def check_declared(self, name: str, size: int) -> None:
        if size > self.max_file:
            raise HubError(
                f"archive member {name!r} is {size} bytes, over the per-file cap of {self.max_file}"
            )
        if self.total + size > self.max_total:
            raise HubError(f"archive expands past the {self.max_total}-byte cap")

    def copy(self, name: str, src, dst) -> None:
        n = 0
        while block := src.read(1 << 20):
            n += len(block)
            self.total += len(block)
            if n > self.max_file:
                raise HubError(f"archive member {name!r} exceeds the per-file cap of {self.max_file} bytes")
            if self.total > self.max_total:
                raise HubError(f"archive expands past the {self.max_total}-byte cap")
            dst.write(block)


def _extract(
    archive: Path,
    out: Path,
    *,
    max_total_bytes: int = MAX_EXTRACT_BYTES,
    max_file_bytes: int = MAX_FILE_BYTES,
) -> None:
    out.mkdir(parents=True, exist_ok=True)
    name = archive.name.lower()
    budget = _Budget(max_total_bytes, max_file_bytes)
    if name.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                rel = safe_relpath(info.filename)
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise HubError(f"symlink in archive: {info.filename!r}")
                target = out / rel
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                budget.check_declared(info.filename, info.file_size)
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src, target.open("wb") as dst:
                    budget.copy(info.filename, src, dst)
    elif name.endswith((".tar.gz", ".tgz", ".tar")):
        with tarfile.open(archive) as t:
            for m in t:
                rel = safe_relpath(m.name)
                if not (m.isfile() or m.isdir()):
                    raise HubError(f"refusing non-regular archive member: {m.name!r}")
                target = out / rel
                if m.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                budget.check_declared(m.name, m.size)
                target.parent.mkdir(parents=True, exist_ok=True)
                src = t.extractfile(m)
                assert src is not None
                with src, target.open("wb") as dst:
                    budget.copy(m.name, src, dst)
    else:
        raise HubError(f"unsupported archive type: {archive.name!r} (want .tar.gz, .tgz, .tar or .zip)")


def _package_root(d: Path) -> Path:
    if (d / MANIFEST).is_file():
        return d
    subs = [p for p in d.iterdir() if p.is_dir()]
    if len(subs) == 1 and (subs[0] / MANIFEST).is_file():
        return subs[0]
    raise HubError("archive has no model.json at its root or in a single top-level folder")


def _check_sha256(got: str, want: str, what: str) -> None:
    if got != want.strip().lower():
        raise HubError(
            f"sha256 mismatch for {what}: expected {want.strip().lower()}, got {got}; "
            "refusing to extract it (a corrupted or tampered download)"
        )


def fetch_url(
    url: str,
    models_dir: Path,
    *,
    sha256: str | None = None,
    progress: bool = False,
    source: dict | None = None,
    max_extract_bytes: int = MAX_EXTRACT_BYTES,
    max_file_bytes: int = MAX_FILE_BYTES,
) -> Path:
    """Download an https:// or file:// archive (or use a local archive or package directory); verify; install.

    `sha256` pins the archive: it is checked before extraction and a mismatch installs nothing.
    `progress` shows download progress on stderr. `source` replaces the recorded provenance.
    `max_extract_bytes` caps the total extracted size, `max_file_bytes` each extracted file.
    """
    if sha256 is not None and not re.fullmatch(r"[0-9a-fA-F]{64}", sha256.strip()):
        raise HubError(f"bad sha256 {sha256!r}: want 64 hex characters")
    models_dir = Path(models_dir)
    models_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".dl-", dir=models_dir))
    try:
        if "://" in url:
            fname = Path(urllib.parse.urlparse(url).path).name or "package"
            h = hashlib.sha256()
            archive = _download(url, work / fname, digest=h, progress=progress)
            if sha256 is not None:
                _check_sha256(h.hexdigest(), sha256, fname)
        else:
            archive = Path(url).expanduser()
            if archive.is_dir():  # an unpacked package directory: copy it, the source stays untouched
                if sha256 is not None:
                    raise HubError("sha256= pins an archive; got a directory")
                shutil.copytree(archive, work / "x" / "pkg")
            elif not archive.exists():
                raise HubError(
                    f"no such file or directory: {url!r} (want hf:<org>/<repo>[@rev], a URL, "
                    "an archive or a package directory)"
                )
            elif sha256 is not None:
                _check_sha256(sha256_file(archive), sha256, archive.name)
        if not (work / "x").exists():
            _extract(archive, work / "x", max_total_bytes=max_extract_bytes, max_file_bytes=max_file_bytes)
        root = _package_root(work / "x")
        pkg = work / "pkg"
        shutil.move(str(root), pkg)
        src = dict(source) if source is not None else {"ref": url}
        if sha256 is not None:
            src.setdefault("sha256", sha256.strip().lower())
        return install_dir(pkg, models_dir, source=src)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def fetch(ref: str, models_dir: Path, **kw) -> Path:
    """`hf:org/repo[@rev]` or an archive URL/path -> installed model directory."""
    if ref.startswith("hf:"):
        return fetch_hf(ref, models_dir, **kw)
    return fetch_url(ref, models_dir, **kw)


def source_of(model_dir: Path) -> dict:
    try:
        return json.loads((Path(model_dir) / SOURCE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def find_installed(ref: str, models_dir: Path) -> Path | None:
    """An already-installed package that was fetched from `ref` (HF refs compare repo + revision)."""
    d = Path(models_dir)
    if not d.is_dir():
        return None
    want = str(HFRef.parse(ref)) if ref.startswith("hf:") else ref
    for sub in sorted(d.iterdir()):
        if sub.name.startswith(".") or not (sub / MANIFEST).is_file():
            continue
        if source_of(sub).get("ref") == want:
            return sub
    return None
