"""`Email Town --self-check`: what a built app bundle can load, without a window or a data directory.

A frozen app fails at runtime, not at build time, when the bundler missed a module, a data file or a
library: onnxruntime, the Gmail discovery document, a keyring backend, the web assets, the CA certificates.
This imports each one the way the app does, runs the model worker through the app binary itself (the
frozen `--email-town-model-worker` path) with the built-in `rules` model, and exits 1 if anything failed.
`--network` also fetches the default model's release URL headers over HTTPS (no download).
`--output FILE` writes the report there too: a windowed Windows build has no stdout.

It reads and writes no mail, account, credential or data directory.
"""

from __future__ import annotations

import argparse
import os
import platform
import ssl
import sys
from collections.abc import Callable

from .. import __version__

WEB_ASSETS = ("index.html", "app.js", "mail.js", "images.js", "style.css", "logo.svg", "app-icon.png")
VAULTS = {"darwin": "keyring.backends.macOS", "win32": "keyring.backends.Windows"}
SAMPLE = {  # made-up mail for the rules model
    "id": "self-check",
    "thread_id": "self-check",
    "label_ids": ["INBOX"],
    "internal_date": 0,
    "history_id": "1",
    "size_estimate": 1,
    "snippet": "",
    "headers": {"from": "Example Shop <orders@shop.example>", "subject": "Your order has shipped"},
    "body_text": "Made-up self-check message.",
    "body_source": "plain",
    "has_calendar": False,
    "attachments": [],
}


def _runtime() -> str:
    frozen = "frozen" if getattr(sys, "frozen", False) else "source"
    return f"email-town-labeler {__version__}, Python {platform.python_version()}, {sys.platform}, {frozen}"


def _web_assets() -> str:
    from importlib.resources import files

    web = files("email_town_labeler.desktop").joinpath("web")
    return (
        f"{sum(len(web.joinpath(name).read_bytes()) for name in WEB_ASSETS)} bytes in {len(WEB_ASSETS)} files"
    )


def _onnx() -> str:
    from ..models import catalog

    if not catalog.extra_installed("onnx"):  # what the app asks before it downloads a model
        raise RuntimeError("onnxruntime/tokenizers are not discoverable (find_spec)")
    import onnxruntime
    import tokenizers

    return f"onnxruntime {onnxruntime.__version__} {onnxruntime.get_available_providers()}, tokenizers {tokenizers.__version__}"


def _gmail() -> str:
    import google_auth_oauthlib.flow  # noqa: F401 - imported as the sign-in does
    import httplib2
    from googleapiclient.discovery_cache import get_static_doc

    if not get_static_doc("gmail", "v1"):
        raise RuntimeError("the gmail v1 discovery document is missing")
    if not os.path.isfile(httplib2.CA_CERTS):
        raise RuntimeError(f"httplib2 CA file missing: {httplib2.CA_CERTS}")
    return "gmail v1 discovery document, OAuth flow, httplib2 CA file"


def _keychain() -> str:
    import keyring

    active = type(keyring.get_keyring())
    name = f"{active.__module__}.{active.__name__}"
    want = VAULTS.get(sys.platform)
    if want and not name.startswith(want):
        raise RuntimeError(f"active backend {name}, expected {want}")
    if not want:  # Linux: the Secret Service may not run here (CI); the backend must still be bundled
        import keyring.backends.SecretService
        import secretstorage  # noqa: F401
    return name


def _tls() -> str:
    from .window import trust_store

    trust_store()  # what the app does at startup
    if sys.platform == "win32":
        count = sum(len(ssl.enum_certificates(store)) for store in ("ROOT", "CA"))
        if not count:
            raise RuntimeError("the Windows certificate store is empty")
        return f"Windows certificate store ({count} certificates)"
    paths = ssl.get_default_verify_paths()
    source = os.environ.get("SSL_CERT_FILE") or paths.cafile or paths.capath
    if not source:
        raise RuntimeError(f"no CA certificates: {paths}")
    return source


def _window() -> str:
    import webview  # noqa: F401 - the package first, as webview.start() loads its platform module

    try:
        from webview._version import __version__ as version
    except ImportError:  # pywebview 5 has no _version module
        version = "?"
    module = {"darwin": "cocoa", "win32": "winforms"}.get(sys.platform, "gtk")
    __import__(f"webview.platforms.{module}")
    return f"pywebview {version} ({module})"


def _model_worker() -> str:
    from ..models import worker
    from ..models.backends.rules import MANIFEST as RULES

    model = worker.WorkerModel(RULES, idle_seconds=60)
    try:
        predictions = model.predict([SAMPLE])
    finally:
        model.release()
    return f"{' '.join(worker.command()[1:])}: {len(predictions)} prediction"


def _network() -> str:
    import urllib.request

    from ..models import catalog

    url = catalog.get(catalog.DEFAULT).url
    with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=30) as response:
        return f"HTTPS {response.status} for {url}"


CHECKS: list[tuple[str, Callable[[], str]]] = [
    ("runtime", _runtime),
    ("web assets", _web_assets),
    ("onnx backend", _onnx),
    ("gmail client", _gmail),
    ("keychain", _keychain),
    ("tls", _tls),
    ("window", _window),
    ("model worker", _model_worker),
]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="Email Town --self-check")
    parser.add_argument("--network", action="store_true", help="also check HTTPS to the model release")
    parser.add_argument("--output", help="also write the report to this file")
    args = parser.parse_args(argv)
    checks = CHECKS + ([("network", _network)] if args.network else [])
    lines, failed = [], 0
    for name, check in checks:
        try:
            lines.append(f"ok    {name}: {check()}")
        except Exception as e:  # noqa: BLE001 - report every failure, then exit 1
            failed += 1
            lines.append(f"FAIL  {name}: {type(e).__name__}: {e}")
    lines.append(f"{len(checks) - failed}/{len(checks)} checks passed")
    report = "\n".join(lines) + "\n"
    if sys.stdout is not None:
        sys.stdout.write(report)
        sys.stdout.flush()
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(report)
    return 1 if failed else 0
