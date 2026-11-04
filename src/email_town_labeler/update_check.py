"""Say when a newer release exists: one GET of a public file from GitHub Releases, at most daily.

What is sent: a GET of LATEST_URL with the User-Agent `email-town-labeler` (no version), and no
query string, body, cookie, token or install id. GitHub sees the IP address, the time and the
request headers. Nothing is ever installed, and no exit code is ever changed.

When (`after_command`, called by cli.main after the command has finished): only for the
INTERACTIVE commands, only when stdout is a terminal and `--json` is not given, at most once per
INTERVAL; the result, a failure too, is cached in `<data_dir>/update-check.json`. Never on a
development version (anything but X.Y.Z), with `[updates] check = false`,
EMAIL_TOWN_LABELER_NO_UPDATE_CHECK=1, DO_NOT_TRACK=1 or CI set. Before the first check it prints
one line saying what it fetches and how to turn it off. `run` only reads the cache
(`log_known_update`); `once`, `backfill` and `--version` never get here.

latest.json is not signed, so it is trusted for one thing only: a version matching X.Y.Z (ASCII
digits). The URLs and the upgrade command printed come from the constants below. The PyPI name
`email-town-labeler` belongs to an unrelated project, so the command always installs this
repository's tag, never a bare `pip install email-town-labeler`.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from . import __version__

REPO_URL = "https://github.com/scamai/email-town-labeler"
LATEST_URL = f"{REPO_URL}/releases/latest/download/latest.json"
USER_AGENT = "email-town-labeler"  # the product name only: no version, platform or Python
TIMEOUT = 2.0  # seconds for the whole check: name lookup, connect, redirects, read
INTERVAL = 24 * 3600  # at most one check per this many seconds
MAX_BYTES = 4096  # latest.json is about 25 bytes
CACHE_FILE = "update-check.json"
DISABLE_ENV = "EMAIL_TOWN_LABELER_NO_UPDATE_CHECK"
OFF_ENVS = (DISABLE_ENV, "DO_NOT_TRACK", "CI")
# what a person runs in a terminal ("<command> <subcommand>"); nothing else ever checks
INTERACTIVE = frozenset({"setup", "status", "doctor", "model list", "model pull"})
VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")  # used with fullmatch: no "v", suffix or newline
PIPX_UPGRADE = 'pipx install --force "email-town-labeler{extras} @ git+{repo}@v{version}"'
DOCKER_UPGRADE = (
    "in your clone, git fetch --tags && git checkout v{version} && "
    "docker compose -f docker/compose.yaml build && docker compose -f docker/compose.yaml up -d"
)
FIRST_RUN = (
    f"email-town-labeler looks for a newer version at most once a day by fetching {LATEST_URL} "
    "(it sends no mail data, account, install ID or version); turn this off with "
    f"[updates] check = false in config.toml or {DISABLE_ENV}=1"
)
log = logging.getLogger("email_town_labeler.update_check")


# -- versions and text -----------------------------------------------------------------------------
def is_release(v: Any) -> bool:
    """A plain X.Y.Z version (what a release is tagged with); False for dev/pre-releases and non-strings."""
    return isinstance(v, str) and VERSION_RE.fullmatch(v) is not None


def newer(latest: Any, installed: Any) -> bool:
    """Whether `latest` is a higher release than `installed` (numerically: 0.10.0 > 0.9.9)."""
    if not (is_release(latest) and is_release(installed)):
        return False
    return tuple(map(int, latest.split("."))) > tuple(map(int, installed.split(".")))


def clean(text: str) -> str:
    """`text` without control and format characters (escape sequences, bells, bidi overrides)."""
    return "".join(c for c in text if unicodedata.category(c) not in ("Cc", "Cf"))


def _describe(e: BaseException) -> str:
    return clean(f"{type(e).__name__}: {e}")[:200]


def _error_name(e: BaseException) -> str:
    """What the cache records about a failure: the type (and HTTP status), never server text."""
    return f"HTTPError {e.code}" if isinstance(e, urllib.error.HTTPError) else type(e).__name__


def upgrade_command(version: str) -> str:
    if not is_release(version):
        raise ValueError("not a release version")
    if _in_container():
        return DOCKER_UPGRADE.format(version=version)
    from .models import catalog

    extras = [e for e in ("embed", "onnx") if catalog.extra_installed(e)]
    return PIPX_UPGRADE.format(
        extras=f"[{','.join(extras)}]" if extras else "", repo=REPO_URL, version=version
    )


def notice(latest: str, installed: str) -> str:
    """The one line shown when `latest` is newer: built from constants and two validated versions."""
    return clean(
        f"email-town-labeler {latest} is available (you have {installed}; release notes: "
        f"{REPO_URL}/releases/tag/v{latest}). Upgrade: {upgrade_command(latest)}"
    )


def _in_container() -> bool:
    return any(Path(p).exists() for p in ("/.dockerenv", "/run/.containerenv"))


# -- when ----------------------------------------------------------------------------------------
def command_of(args: Any) -> str:
    """The command as INTERACTIVE names it: `model list` -> "model list", `status` -> "status"."""
    cmd = getattr(args, "cmd", None) or ""
    sub = getattr(args, "model_cmd", None) if cmd == "model" else None
    return f"{cmd} {sub}" if sub else cmd


def _env_on(environ: Mapping[str, str], name: str) -> bool:
    return environ.get(name, "").strip().lower() not in ("", "0", "false", "no", "off")


def disabled_reason(cfg: Any, version: str, environ: Mapping[str, str]) -> str | None:
    """Why no check may run (and `run` logs nothing): None when one may."""
    if not is_release(version):
        return f"development version {clean(version)}"
    for name in OFF_ENVS:
        if _env_on(environ, name):
            return f"{name} is set"
    if cfg is None:
        return "the config could not be read"
    if not cfg.update_check:
        return "[updates] check = false"
    return None


def _config_or_none(load_config: Callable[[], Any]) -> Any:
    try:
        return load_config()
    except Exception as e:  # noqa: BLE001 - an unreadable config means: do not check
        log.debug("update check: config unreadable (%s)", type(e).__name__)
        return None


def _stdout() -> Any:
    return sys.stdout  # looked up per call (tests swap in a terminal)


def _isatty(stream: Any) -> bool:
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError, OSError):  # None under pythonw, or a closed stream
        return False


# -- the request ---------------------------------------------------------------------------------
class _HttpsOnlyRedirect(urllib.request.HTTPRedirectHandler):
    """GitHub answers with a redirect to the file's host; follow it only to an https URL."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.lower().startswith("https://"):
            raise urllib.error.HTTPError(newurl, code, "refused a redirect to a non-https URL", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def make_opener() -> urllib.request.OpenerDirector:
    """A fresh opener, never the process-wide one: no cookie jar, no credentials, https-only
    redirects. Proxies come from the environment (HTTPS_PROXY), as for model downloads."""
    return urllib.request.build_opener(_HttpsOnlyRedirect)


def fetch_latest(opener: Any = None, *, url: str = LATEST_URL, timeout: float = TIMEOUT) -> str:
    """GET latest.json and return its version (X.Y.Z). Raises on anything else."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT}, method="GET")
    with (opener or make_opener()).open(req, timeout=timeout) as resp:
        body = resp.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise ValueError(f"latest.json is larger than {MAX_BYTES} bytes")
    data = json.loads(body.decode("utf-8"))
    version = data.get("version") if isinstance(data, dict) else None
    if not is_release(version):
        raise ValueError("latest.json has no X.Y.Z version")
    return version


def _fetch_within(timeout: float, opener: Any) -> str:
    """fetch_latest, given up after `timeout` seconds in all: urllib's timeout applies to each
    socket operation and not to the name lookup, so the request runs in a daemon thread."""
    box: dict[str, Any] = {}

    def work() -> None:
        try:
            box["version"] = fetch_latest(opener, timeout=timeout)
        except BaseException as e:  # noqa: BLE001 - re-raised in the caller's thread
            box["error"] = e

    t = threading.Thread(target=work, name="email-town-labeler-update-check", daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError(f"no answer within {timeout:g} s")
    if "error" in box:
        raise box["error"]
    return box["version"]


# -- the cache -----------------------------------------------------------------------------------
def cache_path(data_dir: str | Path) -> Path:
    return Path(data_dir) / CACHE_FILE


def read_cache(path: Path) -> dict | None:
    """The last check, {"checked_at", "latest" (X.Y.Z or None), "error"}; None if absent or unreadable."""
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    ts = d.get("checked_at") if isinstance(d, dict) else None
    if isinstance(ts, bool) or not isinstance(ts, (int, float)) or not math.isfinite(ts):
        return None
    latest = d.get("latest")
    error = d.get("error")
    return {
        "checked_at": float(ts),
        "latest": latest if is_release(latest) else None,  # the file is ours, but re-check it anyway
        "error": clean(error)[:100] if isinstance(error, str) else None,
    }


def write_cache(path: Path, rec: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(rec, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def fresh(rec: dict, now: float) -> bool:
    """Checked less than INTERVAL ago (either way: a clock set back must not stop checks for years)."""
    return abs(now - rec["checked_at"]) < INTERVAL


def _check(path: Path, now: float, first: bool, opener: Any, timeout: float, say: Callable[[str], None]):
    # Record the attempt before making it: a crash, Ctrl-C or an unwritable data directory then
    # means no check for a day, never a check on every command.
    try:
        write_cache(path, {"checked_at": now, "latest": None, "error": "interrupted"})
    except OSError as e:
        log.debug("update check skipped: cannot write %s (%s)", path.name, type(e).__name__)
        return None
    if first:
        say(FIRST_RUN)
    try:
        rec = {"checked_at": now, "latest": _fetch_within(timeout, opener), "error": None}
    except Exception as e:  # noqa: BLE001 - every failure is cached and shown at debug level only
        log.debug("update check failed: %s", _describe(e))
        rec = {"checked_at": now, "latest": None, "error": _error_name(e)}
    try:
        write_cache(path, rec)
    except OSError as e:
        log.debug("update check: cannot write %s (%s)", path.name, type(e).__name__)
    return rec


# -- entry points --------------------------------------------------------------------------------
def _stderr_line(line: str) -> None:
    print(line, file=sys.stderr)


def after_command(
    args: Any,
    paths: Any,
    load_config: Callable[[], Any],
    *,
    now: float | None = None,
    opener: Any = None,
    timeout: float = TIMEOUT,
    say: Callable[[str], None] | None = None,
) -> None:
    """Called by cli.main once a command has finished. Never raises an Exception; prints at most
    two lines, to stderr (`say`)."""
    try:
        _after_command(args, paths, load_config, now, opener, timeout, say or _stderr_line)
    except Exception as e:  # noqa: BLE001 - an update check never breaks a command
        log.debug("update check failed: %s", _describe(e))


def _after_command(args, paths, load_config, now, opener, timeout, say) -> None:
    if command_of(args) not in INTERACTIVE or getattr(args, "json", False):
        return
    if not _isatty(_stdout()):
        return
    why = disabled_reason(_config_or_none(load_config), __version__, os.environ)
    if why:
        log.debug("update check off: %s", why)
        return
    path = cache_path(paths.data_dir)
    t = time.time() if now is None else now
    rec = read_cache(path)
    if rec is None or not fresh(rec, t):
        rec = _check(path, t, rec is None, opener, timeout, say)
    if rec is not None and newer(rec["latest"], __version__):
        say(notice(rec["latest"], __version__))


def log_known_update(paths: Any, load_config: Callable[[], Any]) -> None:
    """For `run`: one INFO line when the cache (written by an interactive command) knows a newer
    release. Reads the cache only; never fetches anything."""
    try:
        if disabled_reason(_config_or_none(load_config), __version__, os.environ):
            return
        rec = read_cache(cache_path(paths.data_dir))
        if rec is not None and newer(rec["latest"], __version__):
            log.info("%s", notice(rec["latest"], __version__))
    except Exception as e:  # noqa: BLE001 - informational only
        log.debug("update check: %s", _describe(e))
