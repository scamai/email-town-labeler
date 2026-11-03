"""The update check (update_check.py): what it requests, when it runs, what it prints, and that it
never breaks a command. No test reaches GitHub: requests go to fakes or to a server on 127.0.0.1."""

from __future__ import annotations

import http.server
import io
import json
import logging
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from email_town_labeler import cli, update_check
from email_town_labeler.config import Paths
from email_town_labeler.models import catalog, hub

REAL_MAKE_OPENER = update_check.make_opener  # captured before tests/conftest.py replaces it
T0 = 1_800_000_000.0
DAY = 24 * 3600


# -- fakes ---------------------------------------------------------------------------------------
class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class Opener:
    """Records every request, and what had been said (`watch`) when it was made; answers with
    `body`, or raises `exc`."""

    def __init__(self, body: bytes = b'{"version": "9.9.9"}', exc: BaseException | None = None):
        self.body, self.exc = body, exc
        self.requests: list[tuple[urllib.request.Request, float | None]] = []
        self.watch: list[str] = []
        self.said_at_open: list[list[str]] = []

    def open(self, req, timeout=None):
        self.requests.append((req, timeout))
        self.said_at_open.append(list(self.watch))
        if self.exc is not None:
            raise self.exc
        return Resp(self.body)


class TTY(io.StringIO):
    def isatty(self):
        return True


class NotTTY(io.StringIO):
    def isatty(self):
        return False


@pytest.fixture(autouse=True)
def _restore_logging():
    lg = logging.getLogger("email_town_labeler")
    level, handlers = lg.level, list(lg.handlers)
    yield
    for h in lg.handlers:
        if h not in handlers:
            h.close()
    lg.handlers[:] = handlers
    lg.setLevel(level)


@pytest.fixture
def on(monkeypatch, tmp_path):
    """The check as a person meets it: a terminal, a release version, no opt-out set, outside a
    container, no extras. `on.opener` stands in for the network."""
    for name in update_check.OFF_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("EMAIL_TOWN_LABELER_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(update_check, "__version__", "1.0.0")
    monkeypatch.setattr(update_check, "_in_container", lambda: False)
    monkeypatch.setattr(catalog, "extra_installed", lambda extra: False)
    monkeypatch.setattr(update_check, "_stdout", TTY)  # pytest's capture owns sys.stdout itself
    said: list[str] = []
    opener = Opener()
    opener.watch = said
    monkeypatch.setattr(update_check, "make_opener", lambda: opener)
    # commands that would download a model fail offline instead
    monkeypatch.setattr(catalog, "fetch", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline")))
    monkeypatch.setattr(hub, "fetch", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline")))
    monkeypatch.setattr(cli, "service_runner", lambda: _no_service)
    paths = Paths.default()
    return SimpleNamespace(
        opener=opener,
        paths=paths,
        said=said,
        say=said.append,
        cache=paths.data_dir / "update-check.json",
        monkeypatch=monkeypatch,
    )


def _no_service(args, **kw):
    return SimpleNamespace(returncode=3, stdout="inactive", stderr="")


def _args(cmd="status", **kw):
    return SimpleNamespace(cmd=cmd, **kw)


def _cfg(check=True):
    return SimpleNamespace(update_check=check)


def _after(on, args=None, *, now=T0, cfg=None, timeout=update_check.TIMEOUT, opener=None):
    update_check.after_command(
        args or _args(),
        on.paths,
        lambda: cfg or _cfg(),
        now=now,
        opener=opener,
        timeout=timeout,
        say=on.say,
    )


FIRST = update_check.FIRST_RUN
NOTICE = (
    "email-town-labeler 9.9.9 is available (you have 1.0.0; release notes: "
    "https://github.com/scamai/email-town-labeler/releases/tag/v9.9.9). Upgrade: "
    'pipx install --force "email-town-labeler @ git+https://github.com/scamai/email-town-labeler@v9.9.9"'
)


# -- the suite-wide switch (tests/conftest.py) ---------------------------------------------------
def test_every_test_runs_with_the_check_off_even_in_a_terminal(monkeypatch, tmp_path):
    """Without the `on` fixture, as every other test in the suite: off by the environment, and the
    real opener is out of reach. (pytest's own stdout is never a terminal, so this test makes one.)"""
    assert update_check.make_opener is not REAL_MAKE_OPENER  # calling it fails the test at teardown
    monkeypatch.setattr(update_check, "make_opener", lambda: pytest.fail("fetched"))
    monkeypatch.setattr(update_check, "_stdout", TTY)
    monkeypatch.setattr(update_check, "__version__", "1.0.0")
    for name in ("DO_NOT_TRACK", "CI"):
        monkeypatch.delenv(name, raising=False)
    said: list[str] = []
    update_check.after_command(_args(), SimpleNamespace(data_dir=tmp_path), _cfg, now=T0, say=said.append)
    assert said == [] and not (tmp_path / "update-check.json").exists()


# -- the request ---------------------------------------------------------------------------------
def test_the_request_is_one_plain_get_with_only_the_fixed_user_agent(on):
    _after(on)
    assert len(on.opener.requests) == 1
    req, timeout = on.opener.requests[0]
    assert req.full_url == "https://github.com/scamai/email-town-labeler/releases/latest/download/latest.json"
    parts = urlsplit(req.full_url)
    assert parts.scheme == "https" and not parts.query and not parts.fragment and not parts.username
    assert req.get_method() == "GET" and req.data is None
    assert req.header_items() == [("User-agent", "email-town-labeler")]  # no version, token or cookie
    assert not {k.lower() for k, _ in req.header_items()} & {"authorization", "cookie"}
    assert timeout == 2.0


@pytest.fixture
def loopback(monkeypatch):
    """A server on 127.0.0.1 that records what urllib really sends, sets a cookie, and redirects
    /redirect-http to plain http."""
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    seen: list[dict] = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append({"method": "GET", "path": self.path, "headers": dict(self.headers.items())})
            if self.path == "/redirect-http":
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{self.server.server_address[1]}/elsewhere")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = b'{"version": "2.3.4"}'
            self.send_response(200)
            self.send_header("Set-Cookie", "session=abc; Path=/")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", seen
    srv.shutdown()
    srv.server_close()


def test_on_the_wire_only_the_user_agent_and_urllib_basics_and_never_a_cookie(loopback):
    base, seen = loopback
    opener = REAL_MAKE_OPENER()
    for _ in range(2):  # the first answer sets a cookie; the second request must not send it back
        assert update_check.fetch_latest(opener, url=f"{base}/latest.json", timeout=2) == "2.3.4"
    assert [s["path"] for s in seen] == ["/latest.json", "/latest.json"]
    for s in seen:
        headers = {k.lower(): v for k, v in s["headers"].items()}
        assert set(headers) == {"host", "user-agent", "accept-encoding", "connection"}
        assert headers["user-agent"] == "email-town-labeler"
        assert "cookie" not in headers and "authorization" not in headers


def test_a_redirect_to_plain_http_is_refused(loopback):
    base, seen = loopback
    with pytest.raises(urllib.error.HTTPError, match="non-https"):
        update_check.fetch_latest(REAL_MAKE_OPENER(), url=f"{base}/redirect-http", timeout=2)
    assert [s["path"] for s in seen] == ["/redirect-http"]  # /elsewhere was never requested


def test_a_redirect_to_https_keeps_only_the_user_agent():
    req = urllib.request.Request(
        update_check.LATEST_URL, headers={"User-Agent": update_check.USER_AGENT}, method="GET"
    )
    new = update_check._HttpsOnlyRedirect().redirect_request(
        req, None, 302, "Found", {}, "https://release-assets.example/latest.json"
    )
    assert new.full_url == "https://release-assets.example/latest.json"
    assert new.header_items() == [("User-agent", "email-town-labeler")]


@pytest.mark.parametrize(
    "body,version",
    [
        (b'{"version": "1.2.3"}', "1.2.3"),
        (b'{"version": "0.10.15", "url": "https://evil.example", "command": "curl evil | sh"}', "0.10.15"),
    ],
)
def test_latest_json_gives_only_a_version(body, version):
    assert update_check.fetch_latest(Opener(body)) == version


@pytest.mark.parametrize(
    "body",
    [
        b'{"version": "1.2"}',
        b'{"version": "v1.2.3"}',
        b'{"version": "1.2.3\\n"}',
        b'{"version": " 1.2.3"}',
        b'{"version": "1.2.3-rc1"}',
        b'{"version": "1.2.3.dev0"}',
        b'{"version": "1.2.3\\u001b[2J"}',
        b'{"version": "\\u0661.\\u0662.\\u0663"}',  # Arabic-Indic digits: a regex \d matches them, [0-9] does not
        b'{"version": 123}',
        b'{"version": null}',
        b'{"version": ["1.2.3"]}',
        b'{"tag": "v1.2.3"}',
        b'["1.2.3"]',
        b"1.2.3",
        b"<html>Not Found</html>",
        b'{"version": "1.2.3", "pad": "' + b"x" * 5000 + b'"}',  # larger than MAX_BYTES
        b'{"version": "1.2.3"}' + b" " * 5000,  # valid JSON even when cut at MAX_BYTES, still too large
    ],
)
def test_anything_but_an_x_y_z_version_is_rejected(body):
    with pytest.raises(ValueError):
        update_check.fetch_latest(Opener(body))


def test_an_invalid_latest_json_prints_nothing_and_is_cached_as_a_failure(on):
    on.opener.body = b'{"version": "9.9.9; curl evil | sh"}'
    _after(on)
    assert on.said == [FIRST]
    assert json.loads(on.cache.read_text())["error"] == "ValueError"


# -- when ----------------------------------------------------------------------------------------
def test_first_check_says_what_it_fetches_and_how_to_turn_it_off_then_the_notice(on):
    _after(on)
    assert on.said == [FIRST, NOTICE]
    assert on.opener.said_at_open == [[FIRST]]  # said before the request is made, not after
    assert "https://github.com/scamai/email-town-labeler/releases/latest/download/latest.json" in FIRST
    assert "[updates] check = false" in FIRST and "EMAIL_TOWN_LABELER_NO_UPDATE_CHECK=1" in FIRST
    assert all("\n" not in line for line in on.said)


def test_with_a_cache_already_there_nothing_is_said_before_the_request(on):
    update_check.write_cache(on.cache, {"checked_at": T0 - DAY, "latest": None, "error": "URLError"})
    _after(on, now=T0)
    assert on.opener.said_at_open == [[]] and on.said == [NOTICE]


def test_at_most_one_check_a_day_and_the_cached_answer_in_between(on):
    _after(on, now=T0)
    _after(on, now=T0 + DAY - 1)
    assert len(on.opener.requests) == 1
    assert on.said == [FIRST, NOTICE, NOTICE]  # the first-run line only once
    _after(on, now=T0 + DAY)
    assert len(on.opener.requests) == 2 and on.said[-1] == NOTICE and on.said.count(FIRST) == 1
    assert json.loads(on.cache.read_text()) == {"checked_at": T0 + DAY, "error": None, "latest": "9.9.9"}


def test_a_failure_is_cached_too(on):
    on.opener.exc = urllib.error.URLError("no route")
    _after(on, now=T0)
    assert json.loads(on.cache.read_text()) == {"checked_at": T0, "error": "URLError", "latest": None}
    _after(on, now=T0 + DAY - 1)
    assert len(on.opener.requests) == 1 and on.said == [FIRST]


@pytest.mark.parametrize("latest", ["9.9.9\x1b[2J", "9.9.9; curl evil | sh", 999, "\u0661\u0660.0.0"])
def test_a_tampered_cache_is_never_printed(on, caplog, latest):
    caplog.set_level(logging.INFO, logger="email_town_labeler")
    update_check.write_cache(on.cache, {"checked_at": T0, "latest": latest, "error": None})
    _after(on, now=T0 + 60)
    update_check.log_known_update(on.paths, _cfg)
    assert on.said == [] and on.opener.requests == []
    assert not [r for r in caplog.records if r.levelno >= logging.INFO]


# Each layer of the test above on its own, so removing any one of them fails a test.
def test_reading_a_tampered_cache_drops_the_version(on):
    update_check.write_cache(on.cache, {"checked_at": T0, "latest": "9.9.9\x1b[2J", "error": None})
    assert update_check.read_cache(on.cache) == {"checked_at": T0, "latest": None, "error": None}


@pytest.mark.parametrize("latest", ["9.9.9; curl x", "\u0661\u0660.0.0", " 9.9.9", 999])
def test_only_a_release_version_can_be_newer(latest):
    assert update_check.newer(latest, "1.0.0") is False


def test_no_upgrade_command_for_anything_but_a_release_version(on):
    with pytest.raises(ValueError, match="not a release version"):
        update_check.upgrade_command("1.2.3; curl x")


def test_the_notice_never_carries_control_or_format_characters(on):
    line = update_check.notice("9.9.9", "1.0.0\x1b[2J\u202e")
    assert line.startswith("email-town-labeler 9.9.9 is available (you have 1.0.0[2J;")
    assert not [c for c in line if unicodedata.category(c) in ("Cc", "Cf")]


def test_a_clock_set_back_does_not_stop_checks(on):
    update_check.write_cache(on.cache, {"checked_at": T0 + 30 * DAY, "latest": None, "error": None})
    _after(on, now=T0)
    assert len(on.opener.requests) == 1 and FIRST not in on.said


def test_an_unwritable_cache_means_no_check_at_all(on):
    on.cache.mkdir(parents=True)  # a directory where the file should be: every write fails
    _after(on)
    _after(on)
    assert on.opener.requests == [] and on.said == []


def test_a_newer_release_only(on):
    for latest, shown in (("1.0.0", False), ("0.9.9", False), ("1.0.1", True), ("1.10.0", True)):
        on.cache.unlink(missing_ok=True)
        on.said.clear()
        on.opener.body = json.dumps({"version": latest}).encode()
        _after(on)
        assert any("is available" in s for s in on.said) is shown, latest
    on.monkeypatch.setattr(update_check, "__version__", "0.9.9")
    on.cache.unlink()
    on.opener.body = b'{"version": "0.10.0"}'  # numeric, not string, comparison
    _after(on)
    assert "email-town-labeler 0.10.0 is available" in on.said[-1]


@pytest.mark.parametrize("version", ["1.0.1.dev0", "1.1.0rc1", "1.0.0+local", "1.0"])
def test_a_development_version_never_checks(on, version):
    on.monkeypatch.setattr(update_check, "__version__", version)
    _after(on)
    assert on.opener.requests == [] and on.said == [] and not on.cache.exists()


@pytest.mark.parametrize(
    "env",
    [
        {"EMAIL_TOWN_LABELER_NO_UPDATE_CHECK": "1"},
        {"DO_NOT_TRACK": "1"},
        {"CI": "true"},
        {"CI": "1"},
    ],
)
def test_opt_outs_in_the_environment(on, env):
    for k, v in env.items():
        on.monkeypatch.setenv(k, v)
    _after(on)
    assert on.opener.requests == [] and on.said == [] and not on.cache.exists()


@pytest.mark.parametrize("env", [{"CI": ""}, {"CI": "false"}, {"DO_NOT_TRACK": "0"}])
def test_an_opt_out_set_to_false_does_not_count(on, env):
    for k, v in env.items():
        on.monkeypatch.setenv(k, v)
    _after(on)
    assert len(on.opener.requests) == 1


def test_config_check_false_or_an_unreadable_config_never_checks(on):
    _after(on, cfg=_cfg(check=False))

    def broken():
        raise ValueError("bad config")

    update_check.after_command(_args(), on.paths, broken, now=T0, say=on.say)
    assert on.opener.requests == [] and on.said == []


def test_not_a_terminal_never_checks(on):
    on.monkeypatch.setattr(update_check, "_stdout", NotTTY)
    _after(on)
    on.monkeypatch.setattr(update_check, "_stdout", lambda: None)  # pythonw
    _after(on)
    assert on.opener.requests == [] and on.said == []


def test_json_never_checks(on):
    _after(on, _args("status", json=True))
    assert on.opener.requests == [] and on.said == []


# -- what it prints ------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "extras,container,command",
    [
        (
            set(),
            False,
            'pipx install --force "email-town-labeler @ git+https://github.com/scamai/email-town-labeler@v9.9.9"',
        ),
        (
            {"onnx"},
            False,
            (
                'pipx install --force "email-town-labeler[onnx] @ '
                'git+https://github.com/scamai/email-town-labeler@v9.9.9"'
            ),
        ),
        (
            {"embed", "onnx"},
            False,
            (
                'pipx install --force "email-town-labeler[embed,onnx] @ '
                'git+https://github.com/scamai/email-town-labeler@v9.9.9"'
            ),
        ),
        (
            {"onnx"},
            True,
            (
                "in your clone, git fetch --tags && git checkout v9.9.9 && docker compose -f docker/compose.yaml "
                "build && docker compose -f docker/compose.yaml up -d"
            ),
        ),
    ],
)
def test_the_upgrade_command_installs_this_repository_s_tag(on, extras, container, command):
    on.monkeypatch.setattr(catalog, "extra_installed", lambda extra: extra in extras)
    on.monkeypatch.setattr(update_check, "_in_container", lambda: container)
    _after(on)
    line = on.said[-1]
    assert line.endswith(f"Upgrade: {command}")
    # the PyPI name belongs to an unrelated project: never a bare `pip install email-town-labeler`
    assert not BARE_INSTALL.search(line)


BARE_INSTALL = re.compile(r'pipx? install(?: -\S+)* "?email-town-labeler(?:\[[a-z,]+\])?"?(?!\s*@)(?=\s|$)')


@pytest.mark.parametrize(
    "text,bare",
    [
        ("pip install email-town-labeler", True),
        ('pipx install --force "email-town-labeler[onnx]"', True),
        ("run pip install email-town-labeler now", True),
        (
            'pipx install --force "email-town-labeler[onnx] @ git+https://github.com/scamai/email-town-labeler"',
            False,
        ),
        ('pip install "email-town-labeler @ git+https://github.com/scamai/email-town-labeler@v1.2.3"', False),
    ],
)
def test_the_bare_install_detector_itself(text, bare):
    assert bool(BARE_INSTALL.search(text)) is bare


def test_control_characters_are_stripped():
    assert update_check.clean("a\x1b[2Jb\x07c\u202ed\x00e\x7f\x9bf\u200bg") == "a[2Jbcdefg"


def test_server_text_in_an_error_reaches_the_debug_log_without_control_characters(on, caplog):
    caplog.set_level(logging.DEBUG, logger="email_town_labeler.update_check")
    on.opener.exc = urllib.error.HTTPError(
        update_check.LATEST_URL, 404, "Not\x1b]0;owned\x07 Found\u202e", {}, None
    )
    _after(on)
    msgs = [r.getMessage() for r in caplog.records if r.name == "email_town_labeler.update_check"]
    assert any("HTTPError" in m and "owned" in m for m in msgs)
    assert not [m for m in msgs if any(unicodedata.category(c) in ("Cc", "Cf") for c in m)]
    assert json.loads(on.cache.read_text())["error"] == "HTTPError 404"  # type and status, no server text
    assert on.said == [FIRST]  # errors never reach the terminal


class _BrokenPaths:
    @property
    def data_dir(self):
        raise RuntimeError("boom")


def _block_the_second_cache_write(on):
    """The request itself puts a directory where write_cache's temporary file goes."""
    tmp = on.cache.with_name(on.cache.name + ".tmp")
    answer = on.opener.open

    def open_then_block(req, timeout=None):
        tmp.mkdir()
        return answer(req, timeout)

    on.opener.open = open_then_block


def _unreadable_config():
    raise ValueError("bad config")


# Each way the check gives up, and the debug line it leaves (cli.setup_logging(0) prints WARNING and
# up on stderr, and `run` logs INFO and up to its log file: anything above DEBUG would show).
GIVE_UPS = {
    "the request failed": (
        lambda on: setattr(on.opener, "exc", urllib.error.URLError("no route")),
        lambda on: _after(on),
        "update check failed: URLError",
    ),
    "the cache cannot be written before the request": (
        lambda on: on.cache.mkdir(parents=True),
        lambda on: _after(on),
        "update check skipped: cannot write update-check.json",
    ),
    "the cache cannot be written after the request": (
        _block_the_second_cache_write,
        lambda on: _after(on),
        "update check: cannot write update-check.json",
    ),
    "a failure anywhere else": (
        lambda on: None,
        lambda on: update_check.after_command(_args(), _BrokenPaths(), _cfg, now=T0, say=on.say),
        "update check failed: RuntimeError: boom",
    ),
    "an opt-out is set": (
        lambda on: on.monkeypatch.setenv("DO_NOT_TRACK", "1"),
        lambda on: _after(on),
        "update check off: DO_NOT_TRACK is set",
    ),
    "the config cannot be read": (
        lambda on: None,
        lambda on: update_check.after_command(_args(), on.paths, _unreadable_config, now=T0, say=on.say),
        "update check: config unreadable (ValueError)",
    ),
    "run cannot read the cache": (
        lambda on: None,
        lambda on: update_check.log_known_update(_BrokenPaths(), _cfg),
        "update check: RuntimeError: boom",
    ),
}


@pytest.mark.parametrize("case", list(GIVE_UPS))
def test_errors_and_skips_are_logged_at_debug_only(on, caplog, case):
    arrange, act, expected = GIVE_UPS[case]
    caplog.set_level(logging.DEBUG, logger="email_town_labeler")
    arrange(on)
    act(on)
    records = [r for r in caplog.records if r.name == "email_town_labeler.update_check"]
    assert [r.getMessage() for r in records if r.getMessage().startswith(expected)], records
    assert [(r.levelname, r.getMessage()) for r in records if r.levelno != logging.DEBUG] == []
    assert set(on.said) <= {FIRST, NOTICE}  # nothing about the failure on the terminal


# -- never breaks a command ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "exc",
    [
        urllib.error.URLError("no route"),
        urllib.error.HTTPError(update_check.LATEST_URL, 500, "oops", {}, None),
        TimeoutError("timed out"),
        ConnectionResetError(54, "reset"),
        OSError("ssl"),
        RuntimeError("anything else"),
    ],
)
def test_network_errors_are_swallowed_and_cached(on, exc):
    on.opener.exc = exc
    _after(on)
    assert on.said == [FIRST]
    assert json.loads(on.cache.read_text())["error"] == update_check._error_name(exc) != "interrupted"


def test_a_failure_anywhere_else_is_swallowed_too(on):
    class Broken:
        @property
        def data_dir(self):
            raise RuntimeError("boom")

    update_check.after_command(_args(), Broken(), _cfg, now=T0, say=on.say)
    update_check.log_known_update(Broken(), _cfg)
    assert on.said == []


def test_a_slow_answer_is_given_up_on_in_time(on):
    release = threading.Event()

    class Slow(Opener):
        def open(self, req, timeout=None):
            release.wait(5)  # like a name lookup that hangs: urllib's timeout does not cover it
            return Resp(b'{"version": "9.9.9"}')

    try:
        start = time.monotonic()
        _after(on, opener=Slow(), timeout=0.2)
        assert time.monotonic() - start < 1.5
    finally:
        release.set()
    assert json.loads(on.cache.read_text())["error"] == "TimeoutError"


# -- through the CLI -----------------------------------------------------------------------------
def _main(*argv: str) -> tuple[int, list[str]]:
    out: list[str] = []
    rc = cli.main(
        list(argv), input_fn=lambda prompt: "", print_fn=lambda *a: out.append(" ".join(map(str, a)))
    )
    return rc, out


@pytest.mark.parametrize(
    "argv,checks",
    [
        (["status"], True),
        (["doctor"], True),
        (["model", "list"], True),
        (["model", "pull", "town-modernbert"], True),
        (["setup", "--yes"], True),
        (["status", "--json"], False),
        (["run"], False),
        (["once"], False),
        (["backfill"], False),
        (["--version"], False),
        (["model", "info"], False),
        (["service", "status"], False),
        (["contacts", "count"], False),
        (["learn", "--dry-run"], False),
        (["gcp-setup", "--print"], False),
    ],
)
def test_which_commands_check_and_that_the_exit_code_never_changes(on, capsys, argv, checks):
    on.monkeypatch.setenv(update_check.DISABLE_ENV, "1")
    rc_off, _ = _main(*argv)
    off_err = capsys.readouterr().err
    on.monkeypatch.delenv(update_check.DISABLE_ENV)
    rc_on, _ = _main(*argv)
    err = capsys.readouterr().err
    assert rc_on == rc_off
    assert len(on.opener.requests) == (1 if checks else 0)
    assert (NOTICE in err) is checks and (FIRST in err) is checks
    assert FIRST not in off_err and NOTICE not in off_err


def test_the_notice_goes_to_stderr_never_stdout(on, capsys):
    rc, out = _main("model", "list")
    err = capsys.readouterr().err.splitlines()
    assert rc == 0 and err[-2:] == [FIRST, NOTICE]
    assert not [line for line in out if "is available" in line or "latest.json" in line]


def test_through_the_cli_a_failed_check_leaves_only_the_first_run_line_on_stderr(on, capsys):
    on.monkeypatch.setenv(update_check.DISABLE_ENV, "1")
    rc_off, _ = _main("model", "list")
    err_off = capsys.readouterr().err.splitlines()
    on.monkeypatch.delenv(update_check.DISABLE_ENV)
    on.opener.exc = urllib.error.HTTPError(update_check.LATEST_URL, 503, "Service Unavailable", {}, None)
    rc, _ = _main("model", "list")
    assert len(on.opener.requests) == 1
    assert rc == rc_off and capsys.readouterr().err.splitlines() == [*err_off, FIRST]


def test_cli_skips_when_stdout_is_not_a_terminal_or_config_says_no(on, capsys):
    on.monkeypatch.setattr(update_check, "_stdout", NotTTY)
    _main("status")
    on.monkeypatch.setattr(update_check, "_stdout", TTY)
    on.paths.config_dir.mkdir(parents=True, exist_ok=True)
    on.paths.config_file.write_text("[updates]\ncheck = false\n")
    _main("status")
    assert on.opener.requests == [] and "is available" not in capsys.readouterr().err


@pytest.mark.parametrize("exc", [RuntimeError("boom"), KeyboardInterrupt()])
def test_a_check_that_blows_up_leaves_the_exit_code_alone(on, capsys, exc):
    def explode(*a, **k):
        raise exc

    rc_before, _ = _main("model", "list")
    on.monkeypatch.setattr(update_check, "_after_command", explode)
    rc, _ = _main("model", "list")
    assert rc == rc_before == 0 and "interrupted" not in capsys.readouterr().err


def test_run_logs_a_known_newer_release_from_the_cache_and_never_fetches(on, caplog):
    caplog.set_level(logging.INFO, logger="email_town_labeler")
    update_check.write_cache(on.cache, {"checked_at": T0, "latest": "9.9.9", "error": None})  # stale
    rc, _ = _main("run")  # stops at "not logged in", after the startup log line
    assert rc == 1 and on.opener.requests == []
    lines = [
        (r.levelno, r.getMessage()) for r in caplog.records if r.name == "email_town_labeler.update_check"
    ]
    assert lines == [(logging.INFO, NOTICE)]
    caplog.clear()
    on.monkeypatch.setenv("DO_NOT_TRACK", "1")
    _main("run")
    assert not [r for r in caplog.records if r.name == "email_town_labeler.update_check"]


def test_run_logs_nothing_without_a_cache(on, caplog):
    caplog.set_level(logging.INFO, logger="email_town_labeler")
    _main("run")
    assert on.opener.requests == [] and not Path(on.cache).exists()
    assert not [r for r in caplog.records if r.name == "email_town_labeler.update_check"]


def test_container_detection_picks_the_docker_upgrade_command(monkeypatch):
    """The real _in_container: a container marker file switches the hint to the Docker command."""
    real_exists = update_check.Path.exists
    markers = (update_check.Path("/.dockerenv"), update_check.Path("/run/.containerenv"))
    for present, expected in ((markers[0], True), (markers[1], True), (None, False)):

        def fake_exists(self, present=present):
            # Path equality, not str(): on Windows str(Path("/.dockerenv")) is "\\.dockerenv".
            if self in markers:
                return self == present
            return real_exists(self)

        monkeypatch.setattr(update_check.Path, "exists", fake_exists)
        assert update_check._in_container() is expected
        command = update_check.upgrade_command("9.9.9")
        docker = update_check.DOCKER_UPGRADE.format(version="9.9.9")
        if expected:
            assert command == docker
        else:
            assert command != docker and "pipx" in command
