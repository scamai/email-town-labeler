"""desktop/window.py: start or reuse the workbench, open the window, stop on close. pywebview is faked;
the workbench, server, lock and launch.json are real, in an empty temporary home."""

from __future__ import annotations

import http.client
import io
import json
import os
import pickle
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from email_town_labeler.config import Paths
from email_town_labeler.desktop import selfcheck, window
from email_town_labeler.desktop.launcher import running
from email_town_labeler.desktop.manager import Workbench
from email_town_labeler.lock import AlreadyRunning, InstanceLock
from email_town_labeler.models import registry, worker
from email_town_labeler.models.backends.rules import MANIFEST as RULES
from tests._model_fixtures import rec

ENTRY = Path(__file__).resolve().parents[1] / "scripts" / "email_town_app.py"


class FakeEvent(list):
    def __iadd__(self, fn):
        self.append(fn)
        return self

    def set(self):
        for fn in self:
            fn()


class FakeWindow:
    def __init__(self, title, url=None, html=None, **options):
        self.title, self.url, self.html, self.options = title, url, html, options
        self.events = SimpleNamespace(closing=FakeEvent())
        self.destroyed = threading.Event()

    def destroy(self):
        self.destroyed.set()


class FakeWebview:
    """create_window/start/settings as pywebview has them. `during(window)` plays the GUI loop."""

    def __init__(self, during=None):
        self.settings = {"ALLOW_DOWNLOADS": False, "OPEN_EXTERNAL_LINKS_IN_BROWSER": False}
        self.windows, self.during, self.started = [], during, None

    def create_window(self, title, url=None, html=None, **options):
        self.windows.append(FakeWindow(title, url, html, **options))
        return self.windows[-1]

    def start(self, **options):
        self.started = options
        if self.during:
            self.during(self.windows[-1])


@pytest.fixture
def home(tmp_path):
    paths = Paths.default(tmp_path)
    root = paths.data_dir / "workbench"
    root.mkdir(parents=True)
    return paths, root


@pytest.fixture
def calls(monkeypatch):
    seen = []
    for name in ("start", "close"):
        original = getattr(Workbench, name)

        def spy(self, _original=original, _name=name):
            seen.append(_name)
            return _original(self)

        monkeypatch.setattr(Workbench, name, spy)
    return seen


def lock_free(root):
    try:
        InstanceLock(root).acquire().release()
    except AlreadyRunning:
        return False
    return True


def shutdown(active):
    conn = http.client.HTTPConnection("127.0.0.1", active["port"], timeout=3)
    headers = {
        "Content-Type": "application/json",
        "Origin": active["origin"],
        "X-Email-Town": "local",
        "Authorization": "Bearer " + active["session"],
    }
    try:
        conn.request("POST", "/api/shutdown", "{}", headers)
        return conn.getresponse().status
    finally:
        conn.close()


# ─── start, serve, stop ───


def test_starts_the_workbench_opens_its_window_and_stops_when_it_closes(home, calls):
    paths, root = home
    seen = {}

    def gui(win):
        seen["active"] = running(root)  # the server answers the launch handshake, as a second start sees it
        seen["lock_free"] = lock_free(root)
        win.events.closing.set()  # the user closes the window

    webview = FakeWebview(gui)
    assert window.run(root, paths, webview) == 0
    win = webview.windows[0]
    assert seen["active"] and win.url == seen["active"]["url"] and "#token=" in win.url
    assert win.url == json.loads((root / "launch.json").read_text())["url"]
    assert seen["lock_free"] is False  # the workbench lock was held while serving
    assert win.title == "Email Town" and win.options["min_size"] == window.MIN_SIZE
    assert win.options["text_select"] is True
    assert webview.settings == {"ALLOW_DOWNLOADS": True, "OPEN_EXTERNAL_LINKS_IN_BROWSER": True}
    assert calls == ["start", "close"]
    assert running(root) is None and lock_free(root)


def test_quit_stops_everything_before_the_gui_loop_ends(home, calls):
    # Cmd+Q / the app menu's Quit ends the process inside the GUI loop, right after `closing` fires.
    paths, root = home
    seen = {}

    def gui(win):
        win.events.closing.set()
        seen["after_closing"] = (running(root), lock_free(root), list(calls))

    assert window.run(root, paths, FakeWebview(gui)) == 0
    assert seen["after_closing"] == (None, True, ["start", "close"])
    assert calls == ["start", "close"]  # stopping twice does nothing the second time


def test_a_server_stopped_from_outside_closes_its_window(home, calls):
    paths, root = home
    seen = {}

    def gui(win):
        seen["status"] = shutdown(running(root))  # `email-town-labeler desktop --stop`
        seen["destroyed"] = win.destroyed.wait(5)

    assert window.run(root, paths, FakeWebview(gui)) == 0
    assert seen == {"status": 200, "destroyed": True}
    assert calls == ["start", "close"] and lock_free(root)


# ─── reuse ───


def test_reuses_a_running_instance_and_leaves_it_running(home, calls):
    paths, root = home
    first, active = window.claim(root, paths)
    assert active is None
    try:
        webview = FakeWebview()
        assert window.run(root, paths, webview) == 0
        win = webview.windows[0]
        assert win.url == first.url  # a window on the instance that already serves this root
        assert list(win.events.closing) == []  # closing it does not stop the other instance
        assert running(root) is not None and calls == ["start"]
    finally:
        first.stop()
    assert running(root) is None and lock_free(root)


def test_a_lock_holder_that_never_answers_is_reported_in_a_window(home, monkeypatch):
    paths, root = home
    monkeypatch.setattr(window, "START_WAIT", 0.3)
    held = InstanceLock(root).acquire()  # a server that took the lock but never wrote launch.json
    try:
        webview = FakeWebview()
        assert window.run(root, paths, webview) == 1
    finally:
        held.release()
    win = webview.windows[0]
    assert win.url is None and "does not respond" in win.html and webview.started is not None
    assert not (root / "launch.json").exists()


def test_main_serves_the_home_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("EMAIL_TOWN_LABELER_HOME", str(tmp_path))
    fake = FakeWebview()
    monkeypatch.setitem(sys.modules, "webview", fake)
    seen = {}
    monkeypatch.setattr(
        window, "run", lambda root, paths, webview: seen.update(root=root, webview=webview) or 0
    )
    assert window.main([]) == 0
    assert seen == {"root": tmp_path.absolute() / "data" / "workbench", "webview": fake}


# ─── the frozen entry point ───

RECS = [rec(0, subject="Your order has shipped", sender="orders@shop.example"), rec(1, subject="Lunch?")]


def test_frozen_worker_command_reenters_the_app_binary(monkeypatch):
    assert worker.command() == [sys.executable, "-m", "email_town_labeler.models.worker"]
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert worker.command() == [sys.executable, worker.FROZEN_FLAG]


def test_the_app_entry_runs_the_model_worker(tmp_path):
    # What the frozen app does: its own binary (here: python + the PyInstaller entry script) + the flag.
    proc = subprocess.run(
        [sys.executable, str(ENTRY), worker.FROZEN_FLAG],
        input=pickle.dumps(("load", RULES, "cpu", False)) + pickle.dumps(("predict", RECS)),
        capture_output=True,
        timeout=60,
        env={**os.environ, "EMAIL_TOWN_LABELER_HOME": str(tmp_path)},
        check=False,
    )
    assert proc.returncode == 0, proc.stderr.decode(errors="replace")
    out = io.BytesIO(proc.stdout)
    assert pickle.load(out) == ("ok", None)
    assert pickle.load(out) == ("ok", registry.load_manifest(RULES).predict(RECS))


def test_worker_model_spawns_through_command(monkeypatch):
    monkeypatch.setattr(worker, "command", lambda: [sys.executable, str(ENTRY), worker.FROZEN_FLAG])
    model = worker.WorkerModel(RULES, idle_seconds=60)
    try:
        assert model.predict(RECS) == registry.load_manifest(RULES).predict(RECS)
        assert model._proc.args == [sys.executable, str(ENTRY), worker.FROZEN_FLAG]
    finally:
        model.release()


@pytest.mark.parametrize(
    ("cafile", "links", "frozen", "want_certifi"),
    [
        (True, False, True, False),  # the build's CA file exists here: keep it
        (False, True, True, False),  # a hashed CA directory (Debian/Ubuntu): keep it
        (False, False, True, True),  # neither (a Fedora CA directory without hash links): certifi
        (False, False, False, False),  # from source: never touched
    ],
)
def test_frozen_app_falls_back_to_certifi_only_without_usable_system_cas(
    tmp_path, monkeypatch, cafile, links, frozen, want_certifi
):
    import ssl

    certifi = pytest.importorskip("certifi")
    capath = tmp_path / "certs"
    capath.mkdir()
    (capath / "ca-bundle.crt").write_text("")
    if links:
        (capath / "abcd1234.0").write_text("")
    defaults = ssl.DefaultVerifyPaths(
        str(tmp_path / "cert.pem") if cafile else None, str(capath), "SSL_CERT_FILE", "", "SSL_CERT_DIR", ""
    )
    monkeypatch.setattr(ssl, "get_default_verify_paths", lambda: defaults)
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    # a private copy: what trust_store() sets must not leak into the rest of the test session
    monkeypatch.setattr(os, "environ", {k: v for k, v in os.environ.items() if not k.startswith("SSL_CERT_")})
    window.trust_store()
    assert os.environ.get("SSL_CERT_FILE") == (certifi.where() if want_certifi else None)


# ─── self-check ───


def test_self_check_reports_every_failure_and_exits_1(tmp_path, monkeypatch, capsys):
    def broken():
        raise ModuleNotFoundError("No module named 'onnxruntime'")

    monkeypatch.setattr(selfcheck, "CHECKS", [("fine", lambda: "loaded"), ("onnx backend", broken)])
    report = tmp_path / "report.txt"
    assert selfcheck.main(["--output", str(report)]) == 1
    text = report.read_text()
    assert text == capsys.readouterr().out
    assert "ok    fine: loaded" in text and "FAIL  onnx backend: ModuleNotFoundError" in text
    assert "1/2 checks passed" in text
    monkeypatch.setattr(selfcheck, "CHECKS", [("fine", lambda: "loaded")])
    assert selfcheck.main([]) == 0


def test_self_check_runs_the_model_worker():
    assert selfcheck._model_worker().endswith(": 1 prediction")


@pytest.mark.parametrize(("platform", "has_icon"), [("win32", False), ("darwin", False), ("linux", True)])
def test_window_icon_is_passed_only_where_pywebview_takes_a_png(monkeypatch, platform, has_icon):
    # Windows WinForms accepts only .ico: a PNG crashed the app as its window opened (CI, 2026-11-08).
    monkeypatch.setattr(window.sys, "platform", platform)
    icon = window._icon()
    assert (icon is not None) == has_icon
    if icon:
        assert icon.endswith(".png") and Path(icon).is_file()
