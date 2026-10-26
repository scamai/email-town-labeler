"""`email-town-labeler` command line. Gmail/model modules are imported lazily inside commands.

Output carries ids, classes, probabilities and counts only — never mail content.
Exit codes: 0 ok, 1 error, 2 usage.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import signal
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from . import __version__
from .config import Config, ConfigError, Paths, load_config, save_config
from .taxonomy import CONTENT_LABELS, LABEL_CLASSES, NEW_SENDER, UNKNOWN

DOCS_GCP = "docs/google-cloud-setup.md"
DOCS_URL = f"https://github.com/scamai/email-town-labeler/blob/main/{DOCS_GCP}"
OK, BAD, SKIP = "✓", "✗", "–"
FIRST_POLL_GRACE = 900  # seconds after setup/bootstrap before "never polled" counts as unhealthy
log = logging.getLogger("email_town_labeler.cli")


class CLIError(Exception):
    """A user-facing failure (exit 1). The message says how to fix it."""


# -- factories (tests monkeypatch these) ----------------------------------------------------------
def _auth() -> Any:
    from .gmail import auth

    return auth


def make_client(paths: Paths) -> Any:
    creds = _auth().load_credentials(paths.token)
    if creds is None:
        raise CLIError(
            "not logged in: run `email-town-labeler login` (or `email-town-labeler login --headless` on a server)"
        )
    from .gmail.client import GmailClient

    return GmailClient.from_credentials(creds)


def make_labels(client: Any, cfg: Config) -> Any:
    from .gmail.labels import LabelMap

    return LabelMap(client, cfg.label_prefix, cfg.display_names(), colors=cfg.colors())


def make_model(cfg: Config, paths: Paths) -> Any:
    from .models.registry import load_model

    return load_model(
        cfg.model_ref,
        models_dir=paths.models_dir,
        device=cfg.device,
        allow_remote_endpoint=cfg.remote_endpoint_allowed(),
    )


def make_service_model(cfg: Config, paths: Paths) -> Any:
    """The model for `run`: a large one lives in a worker process that exits when idle (models/worker.py)."""
    from .models.worker import load_service_model

    return load_service_model(
        cfg.model_ref,
        models_dir=paths.models_dir,
        device=cfg.device,
        allow_remote_endpoint=cfg.remote_endpoint_allowed(),
        keep_loaded_minutes=cfg.keep_loaded_minutes,
    )


def make_store(paths: Paths) -> Any:
    from .state import Store

    return Store(paths.state_db)


def service_target(paths: Paths) -> Any:
    from . import service

    home = paths.config_dir.parent
    single = (
        paths.config_dir == home / "config" and paths.data_dir == home / "data"
    )  # --home / EMAIL_TOWN_LABELER_HOME
    return service.default_target(paths.logs_dir, inbox_home=str(home) if single else None)


def service_runner() -> Callable[..., Any]:
    import subprocess

    return subprocess.run


# -- context ------------------------------------------------------------------------------------
@dataclass
class Ctx:
    args: argparse.Namespace
    paths: Paths
    input_fn: Callable[[str], str] = input
    print_fn: Callable[..., None] = print
    _cfg: Config | None = field(default=None, repr=False)

    def out(self, *a: Any) -> None:
        if getattr(self.args, "redact", False):
            a = tuple(redact(str(x)) for x in a)
        self.print_fn(*a)

    @property
    def cfg(self) -> Config:
        if self._cfg is None:
            self._cfg = load_config(self.paths.config_file)
        return self._cfg

    def ask(self, prompt: str, default: str = "") -> str:
        try:
            ans = self.input_fn(f"{prompt}{f' [{default}]' if default else ''}: ").strip()
        except EOFError:
            ans = ""
        return ans or default

    def confirm(self, prompt: str, default: bool = False) -> bool:
        if getattr(self.args, "yes", False):
            return True
        ans = self.ask(f"{prompt} ({'Y/n' if default else 'y/N'})").lower()
        return default if not ans else ans in ("y", "yes")

    def engine(self, *, dry_run: bool | None = None, with_model: bool = True, service: bool = False) -> Any:
        from .engine import Engine

        cfg = self.cfg
        if dry_run:
            cfg.dry_run = True
        client = make_client(self.paths)
        load = make_service_model if service else make_model
        model = load(cfg, self.paths) if with_model else None  # undo must work even if the model can't load
        return Engine(
            client,
            make_labels(client, cfg),
            model,
            make_store(self.paths),
            cfg,
            learn_path=self.paths.learned,
        )


EMAIL_RE = re.compile(r"[\w.%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")


def redact(text: str, home: str | None = None) -> str:
    """Mask email addresses and the user's home directory, for pasting `status` / `doctor` output publicly."""
    text = EMAIL_RE.sub("<email>", text)
    home = home if home is not None else str(Path.home())
    if len(home) > 1:
        # also the JSON-escaped form (Windows paths print as C:\\\\Users\\\\... inside --json output)
        for h in sorted({home, home.replace("\\", "/"), home.replace("\\", "\\\\")}, key=len, reverse=True):
            text = text.replace(h, "~")
    return text


def _copy_private(src: Path, dst: Path) -> None:
    """Copy a secret file, readable by the owner only (0600 where the OS supports modes)."""
    data = Path(src).read_bytes()
    fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    try:
        os.chmod(dst, 0o600)  # the file may have existed with wider permissions
    except OSError:
        pass


def _counts_line(c: dict) -> str:
    keys = (
        "scanned" if "scanned" in c else "new",
        "labeled",
        "recorded",
        "skipped",
        "already_decided",
        "feedback",
    )
    parts = [f"{k}={c.get(k, 0)}" for k in keys]
    if c.get("by_class"):
        parts.append("classes=" + ",".join(f"{k}:{v}" for k, v in sorted(c["by_class"].items())))
    return (
        " ".join(parts)
        + (" (bootstrapped)" if c.get("bootstrapped") else "")
        + (" (recovered)" if c.get("recovered") else "")
    )


def wanted_labels(cfg: Config) -> list[str]:
    """The classes whose Gmail labels setup creates and doctor expects."""
    return (
        CONTENT_LABELS
        + ([UNKNOWN] if cfg.unknown_action == "label" else [])
        + ([NEW_SENDER] if cfg.new_sender else [])
    )


def _default_model(ctx: Ctx) -> str:
    """The catalog default when its pip extra is importable, else `rules` (saying how to get the extra)."""
    from .models import catalog
    from .models.backends._common import SOURCE

    e = catalog.CATALOG[catalog.DEFAULT]
    if catalog.extra_installed(e.extra):
        return e.key
    ctx.out(
        f"  {e.key} needs the [{e.extra}] extra, which is not installed; using `rules`. To add it:\n"
        f'    pipx install --force "email-town-labeler[{e.extra}] @ {SOURCE}"'
    )
    return "rules"


def _manifest_or_none(cfg: Config, paths: Paths) -> Any:
    """The configured model's manifest without downloading or loading it (None when unavailable)."""
    try:
        from .models.registry import resolve

        return resolve(cfg.model_ref, models_dir=paths.models_dir, download=False)
    except Exception as e:  # noqa: BLE001 - informational
        log.debug("resolve failed: %s", type(e).__name__)
        return None


def _learned_policy(paths: Paths, manifest: Any) -> dict[str, float] | None:
    """Thresholds `learn` tuned for this model (personal.json), if any."""
    if manifest is None:
        return None
    from . import learn

    art = learn.load(paths.learned, manifest.id)
    return dict(art.policy) if art else None


def _policy_line(sources: dict[str, tuple[float, str]]) -> str:
    return ", ".join(f"{k} {v:g} ({src})" for k, (v, src) in sources.items())


def _is_auth_error(e: BaseException) -> bool:
    return type(e).__name__ in ("AuthError", "RefreshError")


def _known_error(e: BaseException) -> tuple[str, str] | None:
    """(what is wrong, how to fix it) for an error whose raw text is long and says little, else None."""
    from .gmail.client import api_disabled_project

    project = api_disabled_project(e)
    if project is None:
        return None
    if project:
        where = f"Google Cloud project {project}"
        url = (
            f"https://console.developers.google.com/apis/api/gmail.googleapis.com/overview?project={project}"
        )
    else:
        where, url = (
            "your Google Cloud project",
            "https://console.cloud.google.com/apis/library/gmail.googleapis.com",
        )
    return (
        f"the Gmail API is not enabled in {where}",
        f"click Enable at {url}, wait a minute, then run this command again",
    )


# -- logging ------------------------------------------------------------------------------------
_HANDLER_TAG = "_email_town_labeler_cli"


def setup_logging(verbose: int, logs_dir: Path | None = None) -> None:
    root = logging.getLogger("email_town_labeler")
    for h in [h for h in root.handlers if getattr(h, _HANDLER_TAG, False)]:
        root.removeHandler(h)
        h.close()
    level = logging.DEBUG if verbose > 1 else logging.INFO if (verbose or logs_dir) else logging.WARNING
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if logs_dir is not None:
        logs_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                logs_dir / "email-town-labeler.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8"
            )
        )
    for h in handlers:
        setattr(h, _HANDLER_TAG, True)
        h.setFormatter(fmt)
        root.addHandler(h)
    root.setLevel(level)
    logging.getLogger("googleapiclient").setLevel(logging.WARNING)


# -- commands -----------------------------------------------------------------------------------
def _valid_client_secret(p: Path) -> bool:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(d, dict) and ("installed" in d or "web" in d)


def _do_login(ctx: Ctx, headless: bool) -> None:
    auth, p = _auth(), ctx.paths
    if not p.client_secret.exists():
        raise CLIError(
            f"no OAuth client at {p.client_secret}; run `email-town-labeler setup` (see {DOCS_URL})"
        )
    if headless:
        url = auth.headless_auth_url(p.client_secret, p.pending_auth)
        ctx.out("Open this URL in any browser, approve access, then paste the full http://localhost/... URL")
        ctx.out("your browser was sent to (the page itself will not load; that is expected):\n")
        ctx.out(f"  {url}\n")
        redirect = ctx.ask("redirect URL")
        if not redirect:
            raise CLIError("no redirect URL pasted; run `email-town-labeler login --headless` again")
        auth.headless_finish(p.client_secret, p.pending_auth, p.token, redirect)
    else:
        ctx.out("Opening your browser for Google sign-in (use --headless on a machine without one)...")
        auth.login_local(p.client_secret, p.token)
    ctx.out(f"{OK} logged in; token saved to {p.token}")


def cmd_login(ctx: Ctx) -> int:
    ctx.paths.ensure()
    _do_login(ctx, ctx.args.headless)
    return 0


def cmd_setup(ctx: Ctx) -> int:
    a, p = ctx.args, ctx.paths.ensure()
    ctx.out(f"email-town-labeler {__version__} setup — config in {p.config_dir}, data in {p.data_dir}\n")

    ctx.out("1/7  Google OAuth client")
    src = Path(a.client_secret).expanduser() if a.client_secret else None
    while src is None and not p.client_secret.exists():
        if a.yes:
            raise CLIError(f"no OAuth client yet: pass --client-secret PATH (how to get one: {DOCS_URL})")
        ctx.out(f"  Create a Desktop-app OAuth client and download its JSON. Guide: {DOCS_URL}")
        ans = ctx.ask("  path to the downloaded client_secret JSON")
        src = Path(ans).expanduser() if ans else None
        if src is None:
            raise CLIError("setup needs the OAuth client JSON")
    if src is not None:
        if not _valid_client_secret(src):
            raise CLIError(f"{src} is not an OAuth client JSON (expected an 'installed' section; {DOCS_URL})")
        if src.resolve() != p.client_secret.resolve():
            _copy_private(src, p.client_secret)
        ctx.out(f"  {OK} copied to {p.client_secret}")
    else:
        ctx.out(f"  {OK} found {p.client_secret}")

    ctx.out("2/7  Sign in to Gmail")
    try:
        creds = _auth().load_credentials(p.token)
    except Exception as e:
        if not _is_auth_error(e):
            raise
        ctx.out(f"  {BAD} saved token unusable ({type(e).__name__}); signing in again")
        creds = None
    if creds is not None:
        ctx.out(f"  {OK} already signed in ({p.token})")
    else:
        _do_login(ctx, a.headless)

    ctx.out("3/7  Config")
    cfg = ctx.cfg
    from .models import catalog

    if a.model:
        cfg.model_ref = a.model
    elif a.yes:
        if not cfg.is_explicit("model_ref"):  # keep a model chosen earlier; else the safe default
            cfg.model_ref = _default_model(ctx)
    else:
        e = catalog.CATALOG[catalog.DEFAULT]
        have = catalog.extra_installed(e.extra)
        ctx.out(
            f"  recommended: {e.key} ({catalog.human_size(e.size)} download, once; needs the [{e.extra}] extra: "
            f"{'installed' if have else 'not installed'}). {e.description}"
        )
        ctx.out(f"  model card (trained on one person's consenting inbox): {catalog.CARD_URL}")
        try:
            from .models.registry import list_models

            names = [m.name for m in list_models(p.models_dir)]
            ctx.out(
                f"  installed models: {', '.join(names)}  (or hf:<org>/<repo>, see `email-town-labeler model list`)"
            )
        except Exception as ex:  # noqa: BLE001 - listing is a convenience
            log.debug("list_models failed: %s", type(ex).__name__)
        default = cfg.model_ref if cfg.is_explicit("model_ref") else _default_model(ctx)
        cfg.model_ref = ctx.ask("  model", default)
    save_config(cfg, p.config_file)
    ctx.out(f"  {OK} wrote {p.config_file} (model.ref = {cfg.model_ref})")

    ctx.out("4/7  Load the model")
    entry = catalog.get(cfg.model_ref)
    if entry is not None and catalog.installed(entry, p.models_dir) is None:
        ctx.out(
            f"  downloading {entry.key} ({catalog.human_size(entry.size)}, once; sha256-pinned); progress on stderr"
        )
    model = make_model(cfg, p)
    ctx.out(f"  {OK} {model.manifest.id} ({model.manifest.backend})")
    ctx.out(f"  policy: {_policy_line(cfg.policy_sources(model.manifest))}")

    ctx.out("5/7  Gmail labels")
    from .engine import Engine

    client = make_client(p)
    from .gmail.labels import foreign_labels

    if foreign := foreign_labels(client.list_labels(), cfg.label_prefix, cfg.display_names()):
        ctx.out(
            f"  ! {len(foreign)} label(s) under {cfg.label_prefix}/ were not made by email-town-labeler, which "
            f"counts everything under {cfg.label_prefix}/ as its own (`undo` would remove them too)"
        )
        if not ctx.confirm(f"  Use {cfg.label_prefix}/ anyway?"):
            raise CLIError(
                f"set [gmail] label_prefix in {p.config_file} to a name you do not use; then run setup"
            )
    labels = make_labels(client, cfg)
    store = make_store(p)
    eng = Engine(client, labels, model, store, cfg)
    if cfg.uses_contacts() and not eng.contacts_ready():
        ctx.out(
            f"  reading who you wrote to in up to {cfg.contacts_scan_limit} sent messages (last "
            f"{cfg.contacts_scan_days} days, headers only); this can take a few minutes"
        )
    if store.get("history_id") is None:
        eng.bootstrap()  # from now on new mail is seen even before the service starts
    eng.ensure_contacts()
    prof_email = store.get("account_email")
    wanted = wanted_labels(cfg)
    for c in wanted:
        labels.ensure(c)
    recolored = labels.sync_colors()  # existing labels from an earlier setup or another color config
    ctx.out(f"  {OK} account {prof_email}; {len(wanted)} labels under {cfg.label_prefix}/")
    if recolored:
        ctx.out(f"  {OK} updated the color of {recolored} label(s)")
    if cfg.uses_contacts():
        ctx.out(f"  {OK} {store.contacts_count()} contacts (addresses you have sent mail to)")

    ctx.out("6/7  Dry run on the 5 newest inbox messages (nothing is changed)")
    rows = eng.preview(5)
    for r in rows:
        note = f"  (model said {r['cls']})" if r["final"] != r["cls"] else ""
        ctx.out(f"  {r['id']}  {r['final']:<14} {r['prob']:.2f}{note}")
    if not rows:
        ctx.out("  (inbox is empty)")

    ctx.out("7/7  Background service")
    if a.install_service or (
        not a.yes and ctx.confirm("  Install a background service that starts at login?")
    ):
        from . import service

        ok = service.install(service_target(p), run=service_runner(), echo=lambda s: ctx.out("  " + s))
        ctx.out(f"  {OK if ok else BAD} service {'installed' if ok else 'not installed'}")
    else:
        ctx.out(
            "  skipped; later: `email-town-labeler service install`, or run `email-town-labeler run` in a terminal"
        )
    ctx.out(
        "\nDone. New mail will be labeled from now on. Label recent mail with `email-town-labeler backfill --days 7`;"
        "\ncheck on it with `email-town-labeler status`. Move a wrong label in Gmail and it is recorded as a correction; the service learns from your corrections daily, locally (`email-town-labeler learn --reset` undoes it)."
    )
    return 0


def cmd_run(ctx: Ctx) -> int:
    setup_logging(ctx.args.verbose, ctx.paths.ensure().logs_dir)
    eng = ctx.engine(dry_run=ctx.args.dry_run, service=True)
    stop = threading.Event()
    prev: dict[int, Any] = {}
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGINT, signal.SIGTERM):
            prev[sig] = signal.signal(sig, lambda *_: stop.set())
    try:
        eng.run_forever(stop)
    finally:
        for sig, h in prev.items():
            signal.signal(sig, h)
    return 0


def cmd_once(ctx: Ctx) -> int:
    c = ctx.engine(dry_run=ctx.args.dry_run).sync_once()
    ctx.out(json.dumps(c, sort_keys=True) if ctx.args.json else _counts_line(c))
    return 0


def cmd_backfill(ctx: Ctx) -> int:
    a = ctx.args
    days = a.days if a.days is not None or a.limit is not None else 7
    c = ctx.engine(dry_run=a.dry_run).backfill(days=days, limit=a.limit)
    ctx.out(
        json.dumps(c, sort_keys=True)
        if a.json
        else f"backfill{' [dry-run]' if ctx.cfg.dry_run else ''}: " + _counts_line(c)
    )
    return 0


def cmd_undo(ctx: Ctx) -> int:
    if not ctx.confirm(f"Remove every {ctx.cfg.label_prefix}/* label from every message?"):
        ctx.out("cancelled")
        return 1
    n = ctx.engine(dry_run=ctx.args.dry_run, with_model=False).undo()
    ctx.out(
        f"{'would remove' if ctx.cfg.dry_run else 'removed'} {ctx.cfg.label_prefix}/* labels from {n} messages"
    )
    return 0


def cmd_labels(ctx: Ctx) -> int:
    """`labels rename-prefix NEW`: rename every label under label_prefix in place, then save the config."""
    from .gmail.labels import prefix_renames

    a, p, cfg = ctx.args, ctx.paths, ctx.cfg
    old, new = cfg.label_prefix, replace(cfg, label_prefix=a.prefix).validate().label_prefix
    client = make_client(p)
    try:
        moves = prefix_renames(client.list_labels(), old, new, cfg.display_names())
    except ValueError as e:
        raise CLIError(f"{e}: pick another prefix, or rename or remove them in Gmail first") from None
    if a.dry_run:
        ctx.out(
            f"would rename {len(moves)} label(s) from {old}/ to {new}/ and set gmail.label_prefix = {new}"
        )
        return 0
    for lid, name in moves:  # same ids: messages keep their labels, and recorded decisions stay valid
        client.rename_label(lid, name)
    cfg.label_prefix = new
    save_config(cfg, p.config_file)
    ctx.out(f"{OK} renamed {len(moves)} label(s) from {old}/ to {new}/; messages keep them")
    ctx.out(f"{OK} wrote {p.config_file} (gmail.label_prefix = {new})")
    ctx.out("  if you stopped the background service for this: `email-town-labeler service install`")
    return 0


def _fmt_ts(ts: float | None) -> str:
    if ts is None:
        return "never"
    age = max(0, int(time.time() - ts))
    return f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts))} ({age}s ago)"


def _float(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _open_error(store: Any, last_poll: float | None) -> dict | None:
    """The last poll failure while no poll has succeeded since; None once one has (or there was none)."""
    name, ts = store.get("last_error"), _float(store.get("last_error_ts"))
    if not name:
        return None
    if ts is None:  # older versions stored "<error> at <epoch seconds>"
        head, sep, tail = name.rpartition(" at ")
        if sep and (ts := _float(tail)) is not None:
            name = head
    if last_poll is not None and (ts is None or ts <= last_poll):
        return None
    n = store.get("poll_failures")
    return {"error": name, "at": ts, "failures_in_a_row": int(n) if n and n.isdigit() else None}


def status_info(ctx: Ctx) -> dict:
    p, cfg = ctx.paths, ctx.cfg
    store = make_store(p)
    st = store.stats()
    last = store.get("last_poll")
    last_ts = float(last) if last else None
    try:
        from . import service

        svc = service.status(service_target(p), run=service_runner())
    except Exception as e:  # noqa: BLE001 - status must never crash on the service probe
        svc = {"state": f"unknown ({type(e).__name__})", "installed": False, "running": False}
    now = time.time()
    stale_after = max(10 * cfg.poll_seconds, FIRST_POLL_GRACE)
    age = None if last_ts is None else now - last_ts
    logged_in = p.token.exists()
    boot = store.get("bootstrap_ts")
    setup_ts = float(boot) if boot else (p.token.stat().st_mtime if logged_in else None)  # else: login time
    if not logged_in:
        problem = "not logged in"
    elif age is None:
        problem = (
            None
            if setup_ts is not None and now - setup_ts <= FIRST_POLL_GRACE
            else f"no successful poll yet, {FIRST_POLL_GRACE // 60} min after setup (is `email-town-labeler run` running?)"
        )
    elif age > stale_after:
        problem = f"no successful poll in the last {int(age)}s (limit {stale_after}s)"
    else:
        problem = None
    err = _open_error(store, last_ts)
    if problem and err and logged_in:
        problem += f"; last error: {err['error']}"
    man = _manifest_or_none(cfg, p)
    learning = learning_info(cfg, p, store, man)
    pol = cfg.policy_sources(man, learning.pop("_policy"))
    return {
        "version": __version__,
        "account": store.get("account_email"),
        "logged_in": logged_in,
        "model": store.get("model_id"),
        "model_ref": cfg.model_ref,
        "dry_run": cfg.dry_run,
        "policy": {k: {"value": v, "source": src} for k, (v, src) in pol.items()},
        "last_poll": last_ts,
        "last_poll_age_s": None if age is None else round(age, 1),
        "last_error": err,  # only while polls are failing
        "decisions": st["decisions"],
        "labeled": st["applied"],
        "by_class": {c: st["by_class"].get(c, 0) for c in LABEL_CLASSES},  # final classes
        "feedback": st["feedback"],
        "contacts": st["contacts"],
        "learning": learning,
        "service": svc,
        "config_dir": str(p.config_dir),
        "data_dir": str(p.data_dir),
        "healthy": problem is None,
        "problem": problem,
    }


def cmd_status(ctx: Ctx) -> int:
    i = status_info(ctx)
    if ctx.args.json:
        ctx.out(json.dumps(i, sort_keys=True))
        return 0 if i["healthy"] else 1
    rows = [
        (
            "account",
            i["account"] or ("(not set up: run `email-town-labeler setup`)" if not i["logged_in"] else "?"),
        ),
        ("model", f"{i['model'] or '(not run yet)'}  (config: {i['model_ref']})"),
        ("policy", ", ".join(f"{k} {d['value']:g} ({d['source']})" for k, d in i["policy"].items())),
        ("mode", "dry-run (records only)" if i["dry_run"] else "live"),
        ("last poll", _fmt_ts(i["last_poll"])),
    ]
    if err := i["last_error"]:  # cleared by the next successful poll
        n = err["failures_in_a_row"]
        when = _fmt_ts(err["at"]) if err["at"] is not None else "time unknown"
        rows.append(
            (
                "last error",
                f"{err['error']} {when}" + (f", {n} failed poll(s) in a row" if n else "") + "; retrying",
            )
        )
    rows += [("decisions", f"{i['decisions']} ({i['labeled']} labeled)")]
    rows += [(f"  {c}", str(n)) for c, n in i["by_class"].items() if n]
    rows += [
        ("feedback", f"{i['feedback']} corrections"),
        ("contacts", f"{i['contacts']} addresses you have sent mail to"),
    ]
    rows += _learning_rows(i["learning"])
    rows += [("service", i["service"].get("state", "?"))]
    for k, v in rows:
        ctx.out(f"{k:<14} {v}")
    if not i["healthy"]:
        ctx.out(f"\n{BAD} unhealthy: {i['problem']} — try `email-town-labeler doctor`")
    return 0 if i["healthy"] else 1


def cmd_doctor(ctx: Ctx) -> int:
    p = ctx.paths
    results: list[tuple[str, bool | None, str]] = []

    def check(name: str, fn: Callable[[], str], fix: str, needs: bool = True) -> Any:
        if not needs:
            results.append((name, None, "skipped"))
            return None
        try:
            detail = fn()
            results.append((name, True, detail))
            return True
        except Exception as e:  # noqa: BLE001 - every failure becomes a checklist line
            lines = [ln.strip(" -") for ln in str(e).splitlines() if ln.strip(" -")]
            msg = " ".join(lines) if lines else type(e).__name__
            msg, how = _known_error(e) or (msg, fix)
            results.append((name, False, f"{msg}\n      fix: {how}"))
            return False

    def config() -> str:
        ctx.cfg  # noqa: B018 - loads and validates
        return str(p.config_file) + ("" if p.config_file.exists() else " (missing: using defaults)")

    cfg_ok = check("config", config, f"edit {p.config_file}, or delete it to use defaults")

    def secret() -> str:
        if not p.client_secret.exists():
            raise CLIError(f"missing {p.client_secret}")
        if not _valid_client_secret(p.client_secret):
            raise CLIError(f"{p.client_secret} is not a Desktop-app OAuth client JSON")
        return str(p.client_secret)

    check("oauth client", secret, f"email-town-labeler setup --client-secret PATH  (guide: {DOCS_URL})")
    creds: list[Any] = []

    def token() -> str:
        c = _auth().load_credentials(p.token)
        if c is None:
            raise CLIError(f"no token at {p.token}")
        creds.append(c)
        return str(p.token)

    tok_ok = check("token", token, "email-town-labeler login  (or: email-town-labeler login --headless)")

    def scopes() -> str:
        want = set(_auth().SCOPES)
        have = set(getattr(creds[0], "scopes", None) or [])
        if not want <= have:
            raise CLIError(f"token scopes {sorted(have)} lack {sorted(want - have)}")
        return "gmail.modify"

    check("scopes", scopes, "email-town-labeler login  (grant the Gmail permission)", needs=bool(tok_ok))
    client: list[Any] = []

    def gmail() -> str:
        c = make_client(p)
        prof = c.profile()
        client.append(c)
        return f"{prof.get('emailAddress')} ({prof.get('messagesTotal', '?')} messages)"

    gm_ok = check(
        "gmail reachable", gmail, "check your network; then `email-town-labeler login`", needs=bool(tok_ok)
    )
    loaded: list[Any] = []

    def model_loads() -> str:
        loaded.append(make_model(ctx.cfg, p))
        return loaded[0].manifest.id

    check(
        "model loads",
        model_loads,
        'email-town-labeler model list / model pull <ref>, or set model.ref = "rules"',
        needs=bool(cfg_ok),
    )
    check(
        "policy",
        lambda: _policy_line(
            ctx.cfg.policy_sources(
                loaded[0].manifest if loaded else None,
                _learned_policy(p, loaded[0].manifest if loaded else None),
            )
        ),
        f"edit [policy] in {p.config_file}",
        needs=bool(cfg_ok),
    )

    def labels() -> str:
        cfg = ctx.cfg
        names = {lb["name"].lower() for lb in client[0].list_labels()}
        need = wanted_labels(cfg)
        dn = cfg.display_names()
        missing = [
            f"{cfg.label_prefix}/{dn[c]}" for c in need if f"{cfg.label_prefix}/{dn[c]}".lower() not in names
        ]
        if missing:
            raise CLIError(f"missing {', '.join(missing)}")
        return f"{len(need)} under {cfg.label_prefix}/"

    check(
        "labels exist",
        labels,
        "email-town-labeler setup --yes (creates them), or just run: they are created on first use",
        needs=bool(gm_ok and cfg_ok),
    )
    check(
        "state db",
        lambda: f"{p.state_db} (schema v{make_store(p).schema_version})",
        f"move {p.state_db} aside; it is rebuilt (decision history is lost)",
    )
    for name, ok, detail in results:
        ctx.out(f"{OK if ok else SKIP if ok is None else BAD} {name:<16} {detail}")
    try:
        from . import service

        ctx.out(f"  {'service':<16} {service.status(service_target(p), run=service_runner())['state']}")
    except Exception as e:  # noqa: BLE001 - informational only
        log.debug("service status failed: %s", type(e).__name__)
    bad = [n for n, ok, _ in results if ok is False]
    ctx.out(f"\n{len(bad)} problem(s)" if bad else "\nall good")
    return 1 if bad else 0


def cmd_model(ctx: Ctx) -> int:
    a, p = ctx.args, ctx.paths
    from .models import catalog, registry

    ref = ctx.cfg.model_ref
    if a.model_cmd == "list":
        ctx.out("catalog (published models; `email-town-labeler model pull NAME`):")
        pkgs: dict[str, str] = {}
        for e in catalog.CATALOG.values():
            pkgs[e.package] = e.key
            state = "installed" if catalog.installed(e, p.models_dir) else "not installed"
            extra = f"[{e.extra}]" + ("" if catalog.extra_installed(e.extra) else " missing")
            mark = "*" if e.key == ref else " "
            ctx.out(
                f"{mark} {e.key:<24}{state:<15}{catalog.human_size(e.size):>8}  {extra:<18}{e.description}"
            )
        ctx.out("\ninstalled:")
        for m in registry.list_models(p.models_dir):
            cur = m.name == ref or (m.path and str(m.path) == ref) or pkgs.get(m.name) == ref
            ctx.out(f"{'*' if cur else ' '} {m.id:<40} {m.backend:<26} {m.description}")
        ctx.out("\n* = configured (model.ref). Also: model pull hf:<org>/<repo>[@rev] | https URL | archive")
        return 0
    if a.model_cmd == "pull":
        from .models import hub

        p.ensure()
        entry = catalog.get(a.ref)
        if entry is not None:
            if not catalog.extra_installed(entry.extra):
                ctx.out(f"note: {entry.key} needs the [{entry.extra}] extra to run (not installed)")
            d = catalog.installed(entry, p.models_dir)
            if d is not None and not a.force:
                ctx.out(f"{OK} {entry.key} already installed at {d} (--force downloads it again)")
            else:
                ctx.out(f"downloading {entry.key} ({catalog.human_size(entry.size)}) from {entry.url}")
                d = catalog.fetch(entry, p.models_dir)
                ctx.out(f"{OK} archive sha256 verified ({entry.sha256})")
        else:
            d = hub.fetch(a.ref, p.models_dir)
        man = registry.resolve(str(d), models_dir=p.models_dir, download=False)
        ctx.out(f"{OK} installed {man.id} ({man.backend}) at {d}")
        if a.use:
            ctx.cfg.model_ref = a.ref
            save_config(ctx.cfg, p.config_file)
            ctx.out(f"{OK} model.ref = {a.ref} (restart the service to pick it up)")
        return 0
    man = registry.resolve(a.ref or ref, models_dir=p.models_dir, download=False)
    pol = ctx.cfg.policy_sources(man, _learned_policy(p, man))
    info = {
        "id": man.id,
        "name": man.name,
        "version": man.version,
        "backend": man.backend,
        "classes": man.classes,
        "description": man.description,
        "license": man.license,
        "metrics": man.metrics,
        "policy": {k: {"value": v, "source": src} for k, (v, src) in pol.items()},
        "path": str(man.path) if man.path else None,
    }
    if a.json:
        ctx.out(json.dumps(info, sort_keys=True, default=str))
    else:
        for k, v in info.items():
            ctx.out(f"{k:<12}{json.dumps(v) if isinstance(v, (dict, list)) else v}")
    return 0


def _learn_model_id(ctx: Ctx, store: Any, man: Any) -> str | None:
    """The model learning is for: the configured one if it resolves, else the one the last poll used."""
    return man.id if man is not None else store.get("model_id")


def learning_info(cfg: Config, p: Paths, store: Any, man: Any) -> dict:
    """Signal counts, the personal head and learned thresholds, for `status`. `_policy` is popped by callers."""
    from . import learn

    mid = man.id if man is not None else store.get("model_id")
    art = learn.load(p.learned, mid) if mid else None
    counts = learn.collect(store, mid, cfg).counts() if mid else {k: 0 for k in learn.KINDS}
    head = art.active_head if art else None
    m = (art.metrics if art else {}) or {}
    hb, hh = m.get("heldout_base") or {}, m.get("heldout_head") or {}
    last = store.get(learn.LAST_TS)
    return {
        "signals": counts,
        "teacher": cfg.learn_teacher,
        "teacher_labels_seen": store.stats().get("teacher", 0),
        "auto": cfg.learn_auto,
        "interval_hours": cfg.learn_interval_hours,
        "last_run": float(last) if last else None,
        "head": head.id if head else None,
        "head_active": head is not None,
        "reason": art.reason if art else None,
        "heldout": {
            "n": hh.get("n"),
            "head_acc": hh.get("acc_strong"),
            "base_acc": hb.get("acc_strong"),
            "head_log_loss": hh.get("log_loss"),
            "base_log_loss": hb.get("log_loss"),
        }
        if hh
        else None,
        "learned_policy": dict(art.policy) if art else {},
        "_policy": dict(art.policy) if art else None,
    }


def _fmt_pct(v: Any) -> str:
    return "?" if v is None else f"{100 * v:.1f}%"


def _learning_rows(li: dict) -> list[tuple[str, str]]:
    s = li["signals"]
    sig = (
        f"{s['correction']} corrections, {s['removal']} removals, {s['teacher']} teacher labels, "
        f"{s['teacher_none']} teacher-unlabeled, {s['confirm']} kept labels"
    )
    rows = [("signals", sig)]
    if li["head_active"]:
        h = li["heldout"] or {}
        head = f"{li['head']} active; held-out accuracy {_fmt_pct(h.get('head_acc'))} vs model {_fmt_pct(h.get('base_acc'))}"
        head += f" on {h.get('n')} signals"
    else:
        head = (
            f"none ({li['reason']})" if li["reason"] else "none (`email-town-labeler learn` has not run yet)"
        )
    rows.append(("personal head", head))
    auto = f"every {li['interval_hours']} h with new signals (last run {_fmt_ts(li['last_run'])})"
    rows.append(("learning", auto if li["auto"] else "manual (`[learn] auto = false`)"))
    return rows


def cmd_learn(ctx: Ctx) -> int:
    """Fit the personal head on the stored signals and re-tune thresholds. Local, no Gmail calls."""
    from . import learn

    p, cfg, a = ctx.paths, ctx.cfg, ctx.args
    store = make_store(p)
    if a.reset:
        existed = learn.reset(store, p.learned)
        ctx.out(
            f"{OK} {'removed the personal head and learned thresholds' if existed else 'nothing learned yet'}; "
            "the model is used as shipped. Signals from before now are no longer used."
        )
        if cfg.learn_auto:
            ctx.out(
                "  learning restarts from new signals; to stop it, set `[learn] auto = false` in config.toml"
            )
        ctx.out(
            "  restart the service only if it is older than this version (it reloads personal.json itself)"
        )
        return 0
    if a.export:
        return _learn_export(ctx, store)
    man = _manifest_or_none(cfg, p)
    mid = _learn_model_id(ctx, store, man)
    if not mid:
        raise CLIError("no decisions yet: run `email-town-labeler run` (or `once`) first")
    res = learn.learn_once(store, p.learned, mid, cfg, man, save_it=not a.dry_run)
    art = res.artifact
    if a.json:
        ctx.out(
            json.dumps(
                {**art.to_json(), "changed_policy": res.changed_policy, "dry_run": a.dry_run},
                sort_keys=True,
                default=str,
            )
        )
        return 0
    s = res.examples
    ctx.out(f"signals for {mid}: " + ", ".join(f"{k} {v}" for k, v in s.items()))
    if art.active_head:
        hh, hb = art.metrics["heldout_head"], art.metrics["heldout_base"]
        ctx.out(
            f"{OK} personal head {art.active_head.id}: held-out accuracy {_fmt_pct(hh['acc_strong'])} "
            f"(model {_fmt_pct(hb['acc_strong'])}), log-loss {hh['log_loss']:.3f} (model {hb['log_loss']:.3f})"
        )
    else:
        ctx.out(f"{SKIP} no personal head: {art.reason}")
    for k, (before, after) in res.changed_policy.items():
        ctx.out(f"{OK} {k}: {before:g} -> {after:g}")
    if not res.changed_policy:
        ctx.out(f"{SKIP} thresholds unchanged")
    ctx.out(
        "(dry run: nothing saved)"
        if a.dry_run
        else f"saved {p.learned.name}; the running service picks it up on its next poll"
    )
    return 0


def _learn_export(ctx: Ctx, store: Any) -> int:
    """JSON lines of (message id, class or not-class, kind, weight, scores, features): no mail content."""
    from . import learn
    from .taxonomy import CLASSES

    p, cfg = ctx.paths, ctx.cfg
    mid = _learn_model_id(ctx, store, _manifest_or_none(cfg, p))
    if not mid:
        raise CLIError("no decisions yet: nothing to export")
    ex = learn.collect(store, mid, cfg)
    out = Path(ctx.args.export)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for i in range(len(ex)):
            row = {
                "msg_id": ex.ids[i],
                "label": CLASSES[ex.y[i]] if ex.y[i] >= 0 else None,
                "not_label": CLASSES[ex.neg[i]] if ex.neg[i] >= 0 else None,
                "kind": ex.kind[i],
                "weight": float(ex.w[i]),
                "scores": [round(float(v), 5) for v in ex.X[i, : learn.K]],
                "features": dict(zip(learn.FEATURES, (int(v) for v in ex.X[i, learn.K :]))),
                "model": mid,
            }
            f.write(json.dumps(row, sort_keys=True) + "\n")
    try:
        os.chmod(out, 0o600)
    except OSError:  # pragma: no cover
        pass
    ctx.out(f"{OK} wrote {len(ex)} signals to {out} (message ids and numbers only; keep it private)")
    return 0


def cmd_personalize(ctx: Ctx) -> int:
    from . import personalize as pz
    from .models import registry

    p, cfg = ctx.paths, ctx.cfg
    if ctx.args.reset:
        return cmd_learn(ctx)
    man = registry.resolve(cfg.model_ref, models_dir=p.models_dir, download=False)
    if man.backend not in pz.HEAD_BACKENDS or man.path is None:
        # no separate head to refit (e.g. town-modernbert): learn a personal head on its scores instead
        ctx.out(f"{man.id} ({man.backend}) has no trainable head: learning on its stored scores instead")
        return cmd_learn(ctx)
    fb = [f for f in make_store(p).feedback() if f["corrected"]]
    if len(fb) < ctx.args.min_examples:
        raise CLIError(
            f"only {len(fb)} corrections so far (need {ctx.args.min_examples}); relabel more mail in "
            f"Gmail by moving it to the right {ctx.cfg.label_prefix}/* label"
        )
    client = make_client(p)
    by_id = {r["id"]: r for r in client.get_records([f["msg_id"] for f in fb])}  # in memory only
    # new_sender is a rule on top of `unknown`, not a model class: for the model it means unknown
    examples = [
        (by_id[f["msg_id"]], UNKNOWN if f["corrected"] == NEW_SENDER else f["corrected"])
        for f in fb
        if f["msg_id"] in by_id
    ]
    ctx.out(f"refitting {man.id} on {len(examples)} corrections ({len(fb) - len(examples)} messages gone)...")
    new = pz.personalize(man.path, examples, p.models_dir, strength=ctx.args.strength, device=cfg.device)
    cfg.model_ref = new.name
    save_config(cfg, p.config_file)
    m = new.metrics.get("personalized", {})
    ctx.out(
        f"{OK} {new.id}: train acc {m.get('train_acc_before', 0):.2f} -> {m.get('train_acc_after', 0):.2f}; "
        f"model.ref = {new.name} (restart the service to use it)"
    )
    return 0


def cmd_contacts(ctx: Ctx) -> int:
    """Counts only, except `list --yes-show-addresses`, the one command that prints addresses."""
    a, p, cfg = ctx.args, ctx.paths, ctx.cfg
    if a.contacts_cmd == "rebuild":
        eng = ctx.engine(with_model=False)
        r = eng.rebuild_contacts()
        ctx.out(
            f"{OK} {r['contacts']} contacts from {r['sent_scanned']} sent messages "
            f"(last {cfg.contacts_scan_days} days, at most {cfg.contacts_scan_limit})"
        )
        return 0
    store = make_store(p)
    if a.contacts_cmd == "list":
        if not a.yes_show_addresses:
            raise CLIError(
                "`contacts list` prints every address you have sent mail to; "
                "pass --yes-show-addresses if that is what you want"
            )
        for addr in store.contact_addresses():
            ctx.print_fn(addr)
        return 0
    ts = store.get("contacts_ts")
    info = {"contacts": store.contacts_count(), "rebuilt": float(ts) if ts else None}
    if a.json:
        ctx.out(json.dumps(info, sort_keys=True))
    else:
        ctx.out(f"{info['contacts']} contacts (last rebuilt: {_fmt_ts(info['rebuilt'])})")
    return 0


def cmd_service(ctx: Ctx) -> int:
    from . import service

    t = service_target(ctx.paths)
    run, echo = service_runner(), ctx.out
    if ctx.args.service_cmd == "install":
        ctx.paths.ensure()
        ok = service.install(t, run=run, echo=echo)
        ctx.out(
            f"{OK} service installed ({t.platform}): {' '.join(t.argv)}"
            if ok
            else f"{BAD} service not installed"
        )
        return 0 if ok else 1
    if ctx.args.service_cmd == "uninstall":
        ok = service.uninstall(t, run=run, echo=echo)
        ctx.out(f"{OK} service removed" if ok else f"{BAD} could not remove the service")
        return 0 if ok else 1
    st = service.status(t, run=run)
    ctx.out(json.dumps(st, sort_keys=True) if ctx.args.json else f"{st['platform']}: {st['state']}")
    return 0


GCP_CONSOLE = "https://console.cloud.google.com"


def gcp_setup_commands(project: str) -> str:
    q = f"?project={project}"
    return f"""\
# Prepare a Google Cloud project for email-town-labeler. email-town-labeler runs none of this; paste it yourself.
# Needs the Google Cloud CLI: https://cloud.google.com/sdk/docs/install   Guide: {DOCS_URL}
gcloud auth login
gcloud projects create {project} --name="email-town-labeler"
gcloud services enable gmail.googleapis.com --project {project}

# Then in the browser (Google offers no CLI for these):
# 3. Consent screen (App name: email-town-labeler; Audience: Internal for your own Workspace org, else External):
#      {GCP_CONSOLE}/auth/overview{q}
# 4. External only: add yourself as a test user, or "Publish app" (Testing = re-login every 7 days):
#      {GCP_CONSOLE}/auth/audience{q}
# 5. Data Access -> Add scope https://www.googleapis.com/auth/gmail.modify
#      {GCP_CONSOLE}/auth/scopes{q}
# 6. Clients -> Create client -> "Desktop app" -> Download JSON:
#      {GCP_CONSOLE}/auth/clients/create{q}
# 7. email-town-labeler setup --client-secret ~/Downloads/client_secret_....json
"""


def cmd_gcp_setup(ctx: Ctx) -> int:
    a = ctx.args
    if not a.print:
        raise CLIError(
            "email-town-labeler does not run gcloud for you: `email-town-labeler gcp-setup --print` prints the commands "
            "to paste (a clone also has scripts/gcp_setup.sh, which runs them)"
        )
    if not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", a.project) and a.project != "PROJECT_ID":
        raise CLIError(
            f"{a.project!r} is not a valid project id (6-30 chars: a-z, 0-9, '-', start with a letter)"
        )
    ctx.out(gcp_setup_commands(a.project).rstrip("\n"))
    return 0


# -- parser -------------------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="email-town-labeler", description="Private, self-hosted Gmail auto-labeler."
    )
    ap.add_argument("--version", action="version", version=f"email-town-labeler {__version__}")
    ap.add_argument(
        "--home", metavar="DIR", help="keep config and data under DIR (same as EMAIL_TOWN_LABELER_HOME)"
    )
    ap.add_argument("-v", "--verbose", action="count", default=0, help="more logging (-vv: debug)")
    # the same two options after the subcommand too (`email-town-labeler run -v`); SUPPRESS keeps the global value
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--home", metavar="DIR", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    common.add_argument("-v", "--verbose", action="count", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    _sub = ap.add_subparsers(dest="cmd", metavar="COMMAND", required=True)

    class _Sub:  # every (sub-)subcommand accepts the common options
        def __init__(self, action: Any):
            self.action = action

        def add_parser(self, *a: Any, **kw: Any) -> argparse.ArgumentParser:
            return self.action.add_parser(*a, parents=[common], **kw)

    sub = _Sub(_sub)

    s = sub.add_parser("setup", help="guided first-time setup")
    s.add_argument("--client-secret", metavar="PATH", help="OAuth client JSON downloaded from Google Cloud")
    s.add_argument("--headless", action="store_true", help="sign in without a local browser (paste a URL)")
    s.add_argument(
        "--model",
        metavar="REF",
        help="model ref, e.g. town-modernbert (default: town-modernbert if the [onnx] extra is installed, else rules)",
    )
    s.add_argument("--yes", "-y", action="store_true", help="accept defaults, ask nothing")
    s.add_argument("--install-service", action="store_true", help="also install the background service")
    s.set_defaults(fn=cmd_setup)

    s = sub.add_parser("login", help="sign in to Gmail (stores a token)")
    s.add_argument("--headless", action="store_true", help="print a URL, paste the redirect back")
    s.set_defaults(fn=cmd_login)

    s = sub.add_parser("run", help="run in the foreground, polling every poll_seconds")
    s.add_argument("--dry-run", action="store_true", help="classify and record, never change Gmail")
    s.set_defaults(fn=cmd_run, single_instance=True)

    s = sub.add_parser("once", help="poll once and exit")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_once, single_instance=True)

    s = sub.add_parser("backfill", help="label recent inbox mail (default: last 7 days)")
    s.add_argument("--days", type=int, metavar="N")
    s.add_argument("--limit", type=int, metavar="N", help="at most N newest messages")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_backfill, single_instance=True)

    s = sub.add_parser("status", help="account, model, last poll, counts, service state")
    s.add_argument("--json", action="store_true", help="machine-readable; exit 1 when unhealthy")
    s.add_argument(
        "--redact", action="store_true", help="mask your email address and home paths (for issues)"
    )
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("undo", help="remove every label under label_prefix (default e/) from every message")
    s.add_argument("--yes", "-y", action="store_true")
    s.add_argument("--dry-run", action="store_true", help="only count")
    s.set_defaults(fn=cmd_undo, single_instance=True)

    s = sub.add_parser("labels", help="the Gmail labels email-town-labeler creates")
    ls = _Sub(s.add_subparsers(dest="labels_cmd", metavar="ACTION", required=True))
    r = ls.add_parser("rename-prefix", help="rename every label under label_prefix to a new prefix, in place")
    r.add_argument("prefix", help="the new prefix, for example e")
    r.add_argument("--dry-run", action="store_true", help="only count")
    s.set_defaults(fn=cmd_labels, single_instance=True)

    s = sub.add_parser("model", help="list, pull or inspect models")
    ms = _Sub(s.add_subparsers(dest="model_cmd", metavar="ACTION", required=True))
    ms.add_parser("list", help="published (catalog) and installed models")
    m = ms.add_parser("pull", help="download a model package")
    m.add_argument("ref", help="a catalog name (town-modernbert), hf:<org>/<repo>[@rev], or an https URL")
    m.add_argument("--use", action="store_true", help="also set it as model.ref")
    m.add_argument("--force", action="store_true", help="download a catalog model again even if installed")
    m = ms.add_parser("info", help="show a model's manifest")
    m.add_argument("ref", nargs="?", help="default: the configured model")
    m.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_model)

    s = sub.add_parser(
        "personalize",
        help="adapt the model to you: refit its head on your corrections, or `learn` if it has none",
    )
    s.add_argument("--strength", type=float, default=1.0, help="how far corrections move the model")
    s.add_argument("--min-examples", type=int, default=5)
    s.add_argument(
        "--reset", action="store_true", help="forget the learned head and thresholds (= learn --reset)"
    )
    s.add_argument("--dry-run", action="store_true", help=argparse.SUPPRESS)
    s.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    s.set_defaults(fn=cmd_personalize, export=None)

    s = sub.add_parser(
        "learn", help="learn from your corrections and Town's labels: personal head + thresholds (local)"
    )
    s.add_argument("--dry-run", action="store_true", help="fit and report, save nothing")
    s.add_argument("--reset", action="store_true", help="forget the learned head and thresholds; start over")
    s.add_argument("--export", metavar="FILE", help="write the signals as JSON lines (ids and numbers only)")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_learn)

    s = sub.add_parser("contacts", help="the addresses you have sent mail to (used by the policy)")
    cs = _Sub(s.add_subparsers(dest="contacts_cmd", metavar="ACTION", required=True))
    cs.add_parser("rebuild", help="rescan sent mail (headers only) and replace the contacts")
    cs.add_parser("count", help="how many contacts are stored").add_argument("--json", action="store_true")
    cs.add_parser("list", help="print the stored addresses").add_argument(
        "--yes-show-addresses", action="store_true", help="required: confirms printing addresses"
    )
    s.set_defaults(fn=cmd_contacts)

    s = sub.add_parser("service", help="background service at login (launchd/systemd/Task Scheduler)")
    ss = _Sub(s.add_subparsers(dest="service_cmd", metavar="ACTION", required=True))
    ss.add_parser("install")
    ss.add_parser("uninstall")
    ss.add_parser("status").add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_service)

    s = sub.add_parser("doctor", help="check config, token, Gmail, model and labels")
    s.add_argument(
        "--redact", action="store_true", help="mask your email address and home paths (for issues)"
    )
    s.set_defaults(fn=cmd_doctor)

    s = sub.add_parser("gcp-setup", help="print the gcloud commands that prepare a Google Cloud project")
    s.add_argument(
        "--print", action="store_true", help="print the commands (email-town-labeler never runs gcloud)"
    )
    s.add_argument("--project", metavar="ID", default="PROJECT_ID", help="project id to put in the commands")
    s.set_defaults(fn=cmd_gcp_setup)
    return ap


def _safe_streams() -> None:
    """Never crash printing ✓/✗ to a console that cannot encode them (Windows cp1252 when redirected)."""
    global OK, BAD, SKIP
    try:
        (OK + BAD + SKIP).encode(getattr(sys.stdout, "encoding", None) or "utf-8")
    except (UnicodeEncodeError, LookupError):
        OK, BAD, SKIP = "+", "x", "-"
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):  # None under pythonw, or not a TextIOWrapper
            pass


def main(
    argv: list[str] | None = None,
    *,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[..., None] = print,
) -> int:
    _safe_streams()
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return int(e.code or 0) if not isinstance(e.code, str) else 2
    ctx = Ctx(args, Paths.default(args.home), input_fn=input_fn, print_fn=print_fn)
    if args.fn is not cmd_run:
        setup_logging(args.verbose)
    try:
        if getattr(args, "single_instance", False):  # run/once/backfill/undo/labels: one per data dir
            from .lock import InstanceLock

            with InstanceLock(ctx.paths.data_dir):
                return int(args.fn(ctx) or 0)
        return int(args.fn(ctx) or 0)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 1
    except (CLIError, ConfigError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        hint = _known_error(e)
        known = type(e).__name__ in (
            "AuthError",
            "UnknownModelError",
            "MissingExtraError",
            "HubError",
            "RemoteEndpointError",
            "AlreadyRunning",
        )
        msg = f"{hint[0]}; {hint[1]}" if hint else str(e) if known else f"{type(e).__name__}: {e}"
        print(f"error: {msg}", file=sys.stderr)
        log.debug("traceback", exc_info=True)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
