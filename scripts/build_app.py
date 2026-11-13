"""Build the Email Town desktop app for this machine's platform with PyInstaller (scripts/email-town.spec).

Run from the repository root, in an environment with the `app` extra and the `app-build` group:
    uv sync --extra app --group app-build && uv run python scripts/build_app.py --zip
    pip install ".[app]" --group app-build && python scripts/build_app.py --zip     (pip >= 25.1)
Linux also needs PyGObject (`pip install "pywebview[gtk]"`) and the GTK 3 / WebKitGTK 4.1 packages.

Output:  macOS dist/Email Town.app, Windows dist/Email Town/, Linux dist/email-town/
--zip:   dist/Email-Town-<version>-<os>-<arch>.zip, the file a release attaches (macOS: made with ditto,
         which keeps the bundle's symlinks), and its SHA-256.
--google-client PATH (or EMAIL_TOWN_GOOGLE_CLIENT_FILE): the Desktop OAuth client JSON downloaded from the
         project's Google Cloud console. It is copied into the package as desktop/sign_in.json for this
         build only (removed afterwards, never committed), so the app offers Google sign-in out of the box.
The build is unsigned: macOS Gatekeeper and Windows SmartScreen warn on first open (docs/desktop-workbench.en.md).
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "scripts" / "email-town.spec"
SIGN_IN = ROOT / "src" / "email_town_labeler" / "desktop" / "sign_in.json"
NEEDED = {
    "PyInstaller": "the app-build dependency group",
    "webview": "the app extra (pywebview)",
    "onnxruntime": "the app extra (onnx)",
    "tokenizers": "the app extra (onnx)",
    "keyring": "the app extra (desktop)",
    "email_town_labeler": "this package (pip install '.[app]')",
}


def target() -> tuple[Path, str]:
    """The built bundle and the archive's platform tag."""
    arch = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64"}.get(platform.machine().lower())
    arch = arch or platform.machine().lower()
    if sys.platform == "darwin":
        return ROOT / "dist" / "Email Town.app", f"macos-{arch}"
    if sys.platform == "win32":
        return ROOT / "dist" / "Email Town", f"windows-{arch}"
    return ROOT / "dist" / "email-town", f"linux-{arch}"


def size(path: Path) -> int:
    return sum(p.lstat().st_size for p in path.rglob("*") if p.is_file() and not p.is_symlink())


def archive(bundle: Path, tag: str, version: str) -> Path:
    out = ROOT / "dist" / f"Email-Town-{version}-{tag}.zip"
    out.unlink(missing_ok=True)
    if sys.platform == "darwin":
        cmd = ["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(bundle), str(out)]
        subprocess.run(cmd, check=True)
    else:
        shutil.make_archive(str(out.with_suffix("")), "zip", bundle.parent, bundle.name)
    return out


def shipped_sign_in(client_file: str) -> dict:
    """The sign_in.json content for a downloaded Desktop OAuth client file (refuses anything else)."""
    data = json.loads(Path(client_file).expanduser().read_text())
    client = data.get("installed") if isinstance(data, dict) else None
    if not isinstance(client, dict) or not client.get("client_id") or not client.get("client_secret"):
        raise SystemExit(f"{client_file}: not a Desktop app OAuth client file (no installed client)")
    return {"google": {"client_id": client["client_id"], "client_secret": client["client_secret"]}}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--zip", action="store_true", help="also write the release archive")
    parser.add_argument(
        "--google-client",
        default=os.environ.get("EMAIL_TOWN_GOOGLE_CLIENT_FILE", ""),
        help="Desktop OAuth client JSON to ship (default: $EMAIL_TOWN_GOOGLE_CLIENT_FILE)",
    )
    args = parser.parse_args()

    missing = [
        f"{module} ({why})" for module, why in NEEDED.items() if importlib.util.find_spec(module) is None
    ]
    if missing:
        print("Missing: " + ", ".join(missing), file=sys.stderr)
        print("Install with: uv sync --extra app --group app-build", file=sys.stderr)
        return 2
    from email_town_labeler import __version__

    bundle, tag = target()
    shipped = shipped_sign_in(args.google_client) if args.google_client else None
    leftover = SIGN_IN.exists() and shipped is None
    if leftover:
        print(
            f"Refusing to build: {SIGN_IN.relative_to(ROOT)} is left from an earlier build", file=sys.stderr
        )
        return 2
    if shipped:
        SIGN_IN.write_text(json.dumps(shipped))
    started = time.monotonic()
    try:
        build()
    finally:
        SIGN_IN.unlink(missing_ok=True)  # only the built app carries it
    seconds = time.monotonic() - started
    if not bundle.exists():
        print(f"PyInstaller finished but {bundle} is missing", file=sys.stderr)
        return 1
    if sys.platform == "darwin":  # the folder PyInstaller assembles the .app from; only the .app ships
        shutil.rmtree(ROOT / "dist" / "Email Town", ignore_errors=True)
    print(f"Built {bundle.relative_to(ROOT)} in {seconds:.0f} s: {size(bundle) / 1e6:.0f} MB")
    carried = list(bundle.rglob("desktop/sign_in.json"))
    if shipped and not carried:
        print("The build does not carry the Google sign-in client", file=sys.stderr)
        return 1
    print(
        f"Google sign-in: {shipped['google']['client_id'].split('-')[0]}-… shipped"
        if shipped
        else "Google sign-in: none shipped (users bring a client file or use app passwords)"
    )
    if args.zip:
        out = archive(bundle, tag, __version__)
        digest = hashlib.sha256(out.read_bytes()).hexdigest()
        print(f"Archive {out.relative_to(ROOT)}: {out.stat().st_size / 1e6:.0f} MB, sha256 {digest}")
    return 0


def build() -> None:
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath",
            str(ROOT / "dist"),
            "--workpath",
            str(ROOT / "build" / "pyinstaller"),
            str(SPEC),
        ],
        cwd=ROOT,
        check=True,
    )


if __name__ == "__main__":
    sys.exit(main())
