"""`email-town`: the local workbench in its own native window, and the desktop app's entry point.

The window (pywebview: WKWebView on macOS, WebView2 on Windows, WebKitGTK on Linux) shows the same
loopback UI as `email-town-labeler desktop`. This process serves it the way `desktop` does (server.py):
Workbench + LocalServer under the workbench lock, with the private launch link in launch.json, so a second
start finds and reuses it. If another process already serves this data root (a running app, or
`desktop` in a terminal), the window opens on that instance and leaves it running when it closes.
Closing the window that owns the server stops everything: HTTP server, mail threads, model worker.

target=_blank links (the original message in Gmail/Outlook, the Microsoft sign-in page) open in the
system browser, as does Google sign-in (webbrowser, unchanged). Attachments download through a save panel.

The frozen app (scripts/build_app.py) is a single binary, so `main()` also dispatches
  --email-town-model-worker   the model worker (models/worker.py), which the app spawns from itself
  --self-check                report what the bundle can load and exit (desktop/selfcheck.py)
before importing anything heavy.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from html import escape
from importlib.resources import files
from pathlib import Path
from typing import Any

from ..config import Paths
from ..models import worker

TITLE = "Email Town"
SIZE = (1280, 840)
MIN_SIZE = (800, 560)
BACKGROUND = "#f6f8fc"  # --bg in web/style.css: what the window shows before the page paints
# Seconds to wait for another start, or for a server that holds the lock but does not answer yet.
START_WAIT = 15.0
LOG_LIMIT = 1_000_000  # desktop.log is rotated to desktop.log.1 above this size, at startup

log = logging.getLogger("email_town_labeler.desktop.window")


class StartError(RuntimeError):
    """The workbench could not start; the message is shown to the user as is."""


# ─── The workbench this process serves ───


class Host:
    """Workbench + LocalServer on a thread, under the root's lock, with launch.json written (as server.run)."""

    def __init__(self, root: Path, paths: Paths):
        # Imported here, not at module level: the model worker and --self-check start through this module
        # and must not load the mail providers.
        from ..gmail.auth import _write_private
        from ..lock import InstanceLock
        from .manager import Workbench
        from .server import LocalServer

        self.lock = InstanceLock(root).acquire()  # raises AlreadyRunning: another process serves this root
        try:
            self.manager = Workbench(root, paths)
            self.server = LocalServer(self.manager, 0)
            _write_private(
                root / "launch.json", json.dumps({"url": self.server.launch_url, "pid": os.getpid()})
            )
        except BaseException:
            self.lock.release()
            raise
        self.url = self.server.launch_url
        self.on_exit = None  # called if the server stops by itself (`desktop --stop`, POST /api/shutdown)
        self._stopped = threading.Event()
        self._stop_lock = threading.Lock()
        self._thread = threading.Thread(target=self._serve, name="workbench-http", daemon=True)

    def start(self) -> None:
        self._thread.start()
        self.manager.start()
        log.info("serving the workbench at %s", self.server.origin)  # the origin, never the launch token

    def _serve(self) -> None:
        try:
            self.server.serve_forever(poll_interval=0.2)
        finally:
            if not self._stopped.is_set() and self.on_exit is not None:
                try:
                    self.on_exit()
                except Exception:  # a window that is already gone needs no closing
                    log.exception("could not close the window after the server stopped")

    def stop(self) -> None:
        """Stop serving, end the mail threads and the model worker, release the lock. Safe to call twice."""
        with self._stop_lock:
            if self._stopped.is_set():
                return
            self._stopped.set()
            try:
                if self._thread.ident is not None:  # shutdown() waits forever for a loop that never ran
                    self.server.shutdown()
                self.manager.close()
            finally:
                self.server.server_close()
                self.lock.release()
                log.info("the workbench stopped")


def claim(root: Path, paths: Paths, *, wait: float | None = None) -> tuple[Host | None, dict | None]:
    """Serve this root (a started Host), or find the process that already serves it (launcher.running)."""
    from ..lock import AlreadyRunning, InstanceLock
    from .launcher import running

    deadline = time.monotonic() + (START_WAIT if wait is None else wait)
    while True:
        try:
            # The launch lock `desktop --background` takes too: simultaneous double-clicks start one server.
            with InstanceLock(root / "launcher"):
                active = running(root)
                if active:
                    return None, active
                try:
                    host = Host(root, paths)
                except AlreadyRunning:
                    pass  # a server holds the workbench lock but does not answer yet: still starting
                else:
                    try:
                        host.start()  # serving before the launch lock is released, so the next start finds it
                    except BaseException:
                        host.stop()
                        raise
                    return host, None
        except AlreadyRunning:
            pass  # another start holds the launch lock
        if time.monotonic() >= deadline:
            raise StartError(
                "Another Email Town is using this data folder but does not respond. Quit it, or end "
                "`email-town-labeler desktop` in its terminal, then open Email Town again."
            )
        time.sleep(0.2)


# ─── The window ───


def run(root: Path, paths: Paths, webview: Any) -> int:
    """Open the window on this root's workbench (starting it if needed); return when the window closes."""
    configure(webview)
    try:
        host, active = claim(root, paths)
    except Exception as e:  # a double-clicked app has no terminal: show why it stopped
        log.exception("the workbench did not start")
        message = (
            str(e) if isinstance(e, StartError) else f"The workbench did not start ({type(e).__name__})."
        )
        return show_error(webview, message, root / "desktop.log")
    window = webview.create_window(
        TITLE,
        host.url if host else active["url"],
        width=SIZE[0],
        height=SIZE[1],
        min_size=MIN_SIZE,
        background_color=BACKGROUND,
        text_select=True,  # pywebview disables selection by default; mail must be selectable and copyable
        zoomable=True,
    )
    if host is None:
        log.info("opening a window on the workbench already running at %s", active["origin"])
    else:
        # Quit from the app menu (Cmd+Q) ends the process inside the GUI loop, after `closing` fires:
        # stop there. A plain window close fires it too; the finally below is then a no-op.
        window.events.closing += host.stop
        host.on_exit = window.destroy  # `desktop --stop` ended the server: close the window it served
    try:
        webview.start(private_mode=True, icon=_icon())
    finally:
        if host is not None:
            host.stop()
    return 0


def configure(webview: Any) -> None:
    settings = getattr(webview, "settings", {})
    for key, value in {
        "ALLOW_DOWNLOADS": True,  # attachment downloads (<a download> on a blob URL) open a save panel
        "OPEN_EXTERNAL_LINKS_IN_BROWSER": True,  # target=_blank goes to the system browser, not a new webview
    }.items():
        if key in settings:
            settings[key] = value


def show_error(webview: Any, message: str, log_path: Path) -> int:
    page = (
        "<!doctype html><meta charset='utf-8'><title>Email Town</title>"
        "<body style='font:15px/1.5 -apple-system,system-ui,sans-serif;margin:32px;color:#1f2328'>"
        f"<h1 style='font-size:18px;margin:0 0 12px'>Email Town could not start</h1><p>{escape(message)}</p>"
        f"<p style='color:#59636e'>Details: {escape(str(log_path))}</p></body>"
    )
    webview.create_window(TITLE, html=page, width=560, height=280, background_color="#ffffff")
    webview.start(private_mode=True)
    return 1


def _icon() -> str | None:
    """The window icon where pywebview uses one (GTK/Qt); macOS and Windows take it from the app bundle."""
    try:
        path = Path(str(files("email_town_labeler.desktop").joinpath("web", "app-icon.png")))
    except (ModuleNotFoundError, TypeError):
        return None
    return str(path) if path.is_file() else None


# ─── Process setup ───


def _log_to(path: Path) -> None:
    """A double-clicked app has no terminal (Windows: no stdout at all). Keep its output in desktop.log."""
    if not (getattr(sys, "frozen", False) or sys.stdout is None or sys.stderr is None):
        return
    from ..gmail.auth import _write_private

    try:
        if path.is_file() and path.stat().st_size > LOG_LIMIT:
            os.replace(path, path.with_name(path.name + ".1"))
        if not path.exists():
            _write_private(path, "")
        stream = path.open("a", encoding="utf-8", errors="replace", buffering=1)
    except OSError:
        return
    if os.name != "nt":
        for fd in (1, 2):  # native libraries and the model worker (it inherits stderr) write here too
            try:
                os.dup2(stream.fileno(), fd)
            except OSError:
                pass
    sys.stdout = sys.stderr = stream
    logging.basicConfig(
        level=logging.INFO, stream=stream, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )


def trust_store() -> None:
    """A frozen app carries the OpenSSL its Python was built with, and that build's default CA file may not
    exist here (a bundle built on Ubuntu, run on Fedora). Use the bundled certifi CAs only then."""
    if not getattr(sys, "frozen", False) or sys.platform == "win32":  # Windows: Python reads the OS store
        return
    if os.environ.get("SSL_CERT_FILE") or os.environ.get("SSL_CERT_DIR"):
        return
    import ssl

    defaults = ssl.get_default_verify_paths()  # cafile / capath are None when they do not exist
    if defaults.cafile:
        return
    # A CA directory only works with OpenSSL's hash links (<hash>.0); Fedora's /etc/ssl/certs has none.
    if defaults.capath and any(Path(defaults.capath).glob("*.0")):
        return
    try:
        import certifi
    except ImportError:
        return
    os.environ["SSL_CERT_FILE"] = certifi.where()


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == [worker.FROZEN_FLAG]:
        return worker.main()
    if args[:1] == ["--self-check"]:
        from .selfcheck import main as self_check

        return self_check(args[1:])
    paths = Paths.default()  # EMAIL_TOWN_LABELER_HOME, else the per-user data directory
    root = paths.data_dir.absolute() / "workbench"
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)
    _log_to(root / "desktop.log")
    trust_store()
    try:
        import webview
    except ImportError:
        print(
            "The Email Town window needs pywebview: pip install 'email-town-labeler[app]'. "
            "Or use `email-town-labeler desktop` in a browser.",
            file=sys.stderr,
        )
        return 1
    return run(root, paths, webview)


if __name__ == "__main__":
    sys.exit(main())
