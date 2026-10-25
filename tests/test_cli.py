from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from email_town_labeler import __version__, cli
from email_town_labeler.config import Paths, load_config
from email_town_labeler.models import catalog
from email_town_labeler.models.backends._common import sha256_file
from email_town_labeler.service import Target
from email_town_labeler.state import Store
from tests._model_fixtures import head_package
from tests.fakes.fake_client import FakeClassifier, FakeGmailClient, FakeLabelMap
from tests.fakes.fake_service import http_error

SECRET = {
    "installed": {"client_id": "x.apps.example", "client_secret": "s", "redirect_uris": ["http://localhost"]}
}


class FakeAuth:
    SCOPES = ("https://www.googleapis.com/auth/gmail.modify",)

    def __init__(self):
        self.calls: list[str] = []

    def load_credentials(self, token_path: Path):
        return SimpleNamespace(scopes=self.SCOPES, valid=True) if Path(token_path).exists() else None

    def _save(self, token_path):
        Path(token_path).write_text("{}")
        return SimpleNamespace(scopes=self.SCOPES)

    def login_local(self, client_secret, token_path, *, open_browser=True):
        self.calls.append("local")
        return self._save(token_path)

    def headless_auth_url(self, client_secret, pending_path):
        self.calls.append("url")
        return "https://accounts.example/o/oauth2/auth?state=abc"

    def headless_finish(self, client_secret, pending_path, token_path, redirect_url):
        assert redirect_url.startswith("http://localhost")
        self.calls.append("finish")
        return self._save(token_path)


class Run:
    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, args, **kw):
        self.calls.append(list(args))
        if "is-active" in args:
            return SimpleNamespace(returncode=3, stdout="inactive", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")


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


@pytest.fixture(autouse=True)
def _offline_catalog(tmp_path, monkeypatch):
    """Results must not depend on the venv's extras, and no test may ever download from GitHub."""
    monkeypatch.setattr(catalog, "extra_installed", lambda extra: False)
    monkeypatch.setenv(catalog.BASE_URL_ENV, (tmp_path / "no-release").as_uri())


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("EMAIL_TOWN_LABELER_HOME", str(tmp_path / "home"))
    client, auth, run = FakeGmailClient(), FakeAuth(), Run()
    for i in range(7):
        client.add_message(cls=["orders", "travel", "promotional"][i % 3], prob=0.4 + 0.1 * i)
    monkeypatch.setattr(cli, "_auth", lambda: auth)
    monkeypatch.setattr(cli, "make_client", lambda paths: (_need_token(paths), client)[1])
    monkeypatch.setattr(
        cli,
        "make_labels",
        lambda c, cfg: FakeLabelMap(c, cfg.label_prefix, cfg.display_names(), colors=cfg.colors()),
    )
    monkeypatch.setattr(cli, "make_model", lambda cfg, paths: FakeClassifier())
    monkeypatch.setattr(cli, "service_runner", lambda: run)
    monkeypatch.setattr(
        cli,
        "service_target",
        lambda paths: Target(
            "linux", ["/venv/bin/email-town-labeler"], tmp_path / "userhome", paths.logs_dir
        ),
    )
    secret = tmp_path / "dl" / "client_secret_123.json"
    secret.parent.mkdir()
    secret.write_text(json.dumps(SECRET))
    out: list[str] = []
    inputs: list[str] = []

    def call(*argv: str):
        out.clear()
        rc = cli.main(
            list(argv),
            input_fn=lambda prompt: inputs.pop(0),
            print_fn=lambda *a: out.append(" ".join(map(str, a))),
        )
        return rc, "\n".join(out)

    return SimpleNamespace(
        call=call, client=client, auth=auth, run=run, secret=secret, inputs=inputs, paths=Paths.default()
    )


def _need_token(paths):
    if not paths.token.exists():
        raise cli.CLIError("not logged in: run `email-town-labeler login`")


def test_version_and_usage(capsys):
    assert cli.main(["--version"]) == 0
    assert __version__ in capsys.readouterr().out
    assert cli.main(["--help"]) == 0
    helptext = capsys.readouterr().out
    for cmd in (
        "setup",
        "login",
        "run",
        "once",
        "backfill",
        "status",
        "undo",
        "model",
        "personalize",
        "service",
        "doctor",
    ):
        assert f"    {cmd} " in helptext
    for bad in ([], ["bogus"], ["backfill", "--days", "x"], ["model"], ["service", "restart"]):
        assert cli.main(bad) == 2
    p = cli.build_parser()
    a = p.parse_args(["backfill", "--days", "3", "--limit", "10", "--dry-run"])
    assert (a.days, a.limit, a.dry_run) == (3, 10, True)
    a = p.parse_args(
        ["--home", "/x", "setup", "--client-secret", "c.json", "--headless", "--model", "rules", "--yes"]
    )
    assert (a.home, a.client_secret, a.headless, a.model, a.yes) == ("/x", "c.json", True, "rules", True)
    assert p.parse_args(["model", "pull", "hf:o/r@v1", "--use"]).ref == "hf:o/r@v1"
    assert p.parse_args(["service", "status", "--json"]).json


def test_setup_needs_client_secret_noninteractive(app, capsys):
    rc, _ = app.call("setup", "--yes")
    assert rc == 1 and "--client-secret" in capsys.readouterr().err


def test_setup_noninteractive_headless(app):
    app.inputs.append("http://localhost:8765/?state=abc&code=xyz")
    rc, out = app.call("setup", "--client-secret", str(app.secret), "--headless", "--model", "rules", "--yes")
    assert rc == 0, out
    p = app.paths
    assert p.client_secret.exists() and p.token.exists() and app.auth.calls == ["url", "finish"]
    assert "https://accounts.example/o/oauth2/auth" in out and "1/7" in out and "7/7" in out
    assert load_config(p.config_file).model_ref == "rules"
    names = {lb["name"] for lb in app.client.labels.values()}
    assert {
        "email-town-labeler/orders",
        "email-town-labeler/action needed",
    } <= names and "email-town-labeler/unknown" not in names
    by_name = {lb["name"]: lb for lb in app.client.labels.values()}
    assert by_name["email-town-labeler/orders"]["color"]["backgroundColor"] == "#b99aff"
    assert "color" not in by_name["email-town-labeler"]  # the parent stays uncolored
    preview = [ln for ln in out.splitlines() if ln.strip().startswith("m0")]
    assert len(preview) == 5 and all(len(ln.split()) >= 3 for ln in preview)
    assert not app.client.modifies  # dry run on 5 newest changed nothing
    assert Store(p.state_db).get("history_id") == str(app.client.history_id)
    assert "made-up" not in out and "sender@" not in out  # ids, classes and probs only
    assert app.run.calls == []  # --yes without --install-service does not install
    # re-running is idempotent and reuses the token
    rc, out = app.call("setup", "--yes")
    assert rc == 0 and "already signed in" in out and app.auth.calls == ["url", "finish"]


def test_setup_interactive(app):
    app.inputs += [str(app.secret), "", "y"]  # secret path, model (default), install service
    rc, out = app.call("setup")
    assert rc == 0, out
    assert app.auth.calls == ["local"]
    assert ["systemctl", "--user", "enable", "--now", "email-town-labeler.service"] in app.run.calls


def test_once_status_and_json(app):
    app.inputs.append("http://localhost:8765/?state=abc&code=xyz")
    app.call("setup", "--client-secret", str(app.secret), "--headless", "--yes")
    app.client.add_message(cls="orders", prob=0.95)
    app.client.add_message(cls="travel", prob=0.9)
    rc, out = app.call("once")
    assert rc == 0 and "labeled=2" in out
    rc, out = app.call("status")
    assert rc == 0, out
    assert (
        "me@example.test" in out
        and "fake@1" in out
        and "orders" in out
        and "installed (not running)" not in out
    )
    assert "not installed" in out and "feedback" in out and "last poll" in out
    rc, out = app.call("status", "--json")
    info = json.loads(out)
    assert rc == 0 and info["healthy"] and info["by_class"]["orders"] == 1 and info["labeled"] == 2
    assert info["account"] == "me@example.test" and info["service"]["state"] == "not installed"


def test_status_unhealthy_before_login(app):
    rc, out = app.call("status", "--json")
    assert rc == 1 and json.loads(out)["logged_in"] is False


def test_backfill_undo(app):
    app.paths.ensure()
    app.paths.token.write_text("{}")
    rc, out = app.call("backfill", "--days", "2", "--dry-run")
    assert rc == 0 and "[dry-run]" in out and not app.client.modifies
    rc, out = app.call("backfill", "--days", "2")
    assert rc == 0 and "labeled=" in out and app.client.modifies
    app.inputs.append("n")
    assert app.call("undo")[0] == 1
    rc, out = app.call("undo", "--yes")
    assert rc == 0 and "removed email-town-labeler/* labels from" in out


def test_doctor(app):
    rc, out = app.call("doctor")
    assert rc == 1 and "✗ token" in out and "email-town-labeler login" in out and "– gmail reachable" in out
    app.inputs.append("http://localhost:8765/?state=abc&code=xyz")
    app.call("setup", "--client-secret", str(app.secret), "--headless", "--yes")
    rc, out = app.call("doctor")
    assert rc == 0, out
    for name in (
        "config",
        "oauth client",
        "token",
        "scopes",
        "gmail reachable",
        "model loads",
        "labels exist",
        "state db",
    ):
        assert f"✓ {name}" in out
    assert "all good" in out


def test_doctor_bad_config(app):
    app.paths.ensure()
    app.paths.config_file.write_text("[model]\nunknown_action = 'delete'\n")
    rc, out = app.call("doctor")
    assert rc == 1 and "✗ config" in out and "unknown_action" in out


def test_run_wires_signals_and_logs(app, monkeypatch):
    app.paths.ensure()
    app.paths.token.write_text("{}")
    seen = {}

    def fake_run_forever(self, stop=None):
        seen["stop"] = stop
        self.sync_once()

    monkeypatch.setattr("email_town_labeler.engine.Engine.run_forever", fake_run_forever)
    rc, _ = app.call("run", "--dry-run")
    assert rc == 0 and seen["stop"] is not None
    log = (app.paths.logs_dir / "email-town-labeler.log").read_text()
    assert "poll ok [dry-run]" in log and "made-up" not in log


def test_service_commands(app):
    rc, out = app.call("service", "status")
    assert rc == 0 and "linux: not installed" in out
    rc, out = app.call("service", "install")
    assert rc == 0 and ["systemctl", "--user", "daemon-reload"] in app.run.calls
    rc, out = app.call("service", "uninstall")
    assert rc == 0


def test_errors_exit_1(app, capsys):
    rc, _ = app.call("once")
    assert rc == 1 and "not logged in" in capsys.readouterr().err


def _api_disabled(paths):
    raise http_error(
        403,
        "Gmail API has not been used in project 123456789012 before or it is disabled.",
        reason="accessNotConfigured",
        path="profile",
    )


def test_gmail_api_not_enabled_is_one_line_with_the_fix(app, monkeypatch, capsys):
    monkeypatch.setattr(cli, "make_client", _api_disabled)
    app.paths.ensure()
    app.paths.token.write_text("{}")
    rc, _ = app.call("once")
    err = capsys.readouterr().err
    assert rc == 1 and err.count("\n") == 1 and "HttpError" not in err
    assert "the Gmail API is not enabled in Google Cloud project 123456789012" in err
    assert "gmail.googleapis.com/overview?project=123456789012" in err
    rc, out = app.call("doctor")
    assert rc == 1 and "✗ gmail reachable" in out and "fix: click Enable at" in out
    assert "check your network" not in out and "HttpError" not in out


def test_cli_against_real_gmail_layer_and_rules(tmp_path, monkeypatch):
    """Real GmailClient + LabelMap + rules model over the googleapiclient-shaped fake (when those exist)."""
    gclient = pytest.importorskip("email_town_labeler.gmail.client")
    pytest.importorskip("email_town_labeler.models.registry")
    fake_service = pytest.importorskip("tests.fakes.fake_service")
    monkeypatch.setenv("EMAIL_TOWN_LABELER_HOME", str(tmp_path / "home"))
    import time

    svc = fake_service.FakeGmail(now_ms=int(time.time() * 1000))  # the CLI's engine uses the real clock
    before = svc.deliver(
        subject="Your order has shipped",
        from_="Shop <orders@shop.example>",
        internal_date=svc.now_ms - 86_400_000,
    )  # before setup: untouched, even by recovery
    client = gclient.GmailClient(svc, sleep=lambda s: None)
    auth = FakeAuth()
    monkeypatch.setattr(cli, "_auth", lambda: auth)
    monkeypatch.setattr(cli, "make_client", lambda paths: (_need_token(paths), client)[1])
    monkeypatch.setattr(cli, "service_runner", lambda: Run())
    monkeypatch.setattr(
        cli,
        "service_target",
        lambda paths: Target(
            "linux", ["/venv/bin/email-town-labeler"], tmp_path / "userhome", paths.logs_dir
        ),
    )
    secret = tmp_path / "cs.json"
    secret.write_text(json.dumps(SECRET))
    out: list[str] = []

    def call(*argv):
        out.clear()
        return cli.main(
            list(argv), input_fn=lambda p: "", print_fn=lambda *a: out.append(" ".join(map(str, a)))
        )

    assert call("setup", "--client-secret", str(secret), "--yes") == 0, out
    assert not any(lid.startswith("Label_") for m in svc.message_ids() for lid in svc.labels_of(m))
    receipt = svc.deliver(subject="Receipt for your order #123", from_="Store <no-reply@store.example>")
    flight = svc.deliver(
        subject="Your flight itinerary and boarding pass", from_="Air <no-reply@air.example>"
    )
    assert call("once") == 0 and "labeled=" in out[0], out
    orders = svc.label_id("email-town-labeler/orders")
    assert orders in svc.labels_of(receipt)
    assert svc.label_id("email-town-labeler/travel") in svc.labels_of(flight)
    svc.user_relabel(receipt, add=["email-town-labeler/updates"], remove=[orders])
    assert call("once", "--json") == 0 and json.loads(out[0])["feedback"] == 1
    hotel = svc.deliver(subject="Your hotel reservation", from_="Hotel <no-reply@hotel.example>")
    svc.expire_history()
    assert call("once") == 0 and "(recovered)" in out[0], out
    assert svc.label_id("email-town-labeler/travel") in svc.labels_of(hotel)
    assert not any(lid.startswith("Label_") for lid in svc.labels_of(before))
    assert call("doctor") == 0, out
    assert call("status") == 0 and any("rules@" in ln for ln in out)
    assert call("undo", "--yes") == 0
    ours = {lb["id"] for lb in client.list_labels() if lb["name"].startswith("email-town-labeler")}
    assert not any(set(svc.labels_of(m)) & ours for m in svc.message_ids())
    assert call("once", "--json") == 0 and json.loads(out[0])["feedback"] == 0


def test_output_survives_a_console_that_cannot_encode_checkmarks(tmp_path, monkeypatch):
    import io
    import sys

    raw = io.BytesIO()
    out = io.TextIOWrapper(raw, encoding="cp1252")  # Windows console code page when output is redirected
    monkeypatch.setattr(sys, "stdout", out)
    for name in ("OK", "BAD", "SKIP"):  # main() swaps the glyphs module-wide; restore them afterwards
        monkeypatch.setattr(cli, name, getattr(cli, name))
    assert cli.main(["--home", str(tmp_path), "doctor"]) == 1  # problems found, but no UnicodeEncodeError
    out.flush()
    assert b"+ config" in raw.getvalue() and b"x oauth client" in raw.getvalue()


def test_undo_works_when_the_model_cannot_load(app, monkeypatch):
    app.paths.ensure()
    app.paths.token.write_text("{}")
    assert app.call("backfill", "--days", "2")[0] == 0 and app.client.modifies

    def broken(cfg, paths):
        raise RuntimeError("model unavailable (extra uninstalled, or would need a download)")

    monkeypatch.setattr(cli, "make_model", broken)
    rc, out = app.call("undo", "--yes")
    assert rc == 0 and "removed email-town-labeler/* labels from" in out


def _logged_in(app, **kv):
    app.paths.ensure()
    app.paths.token.write_text("{}")
    st = Store(app.paths.state_db)
    for k, v in kv.items():
        st.set(k, v)
    st.close()


def test_health_needs_a_successful_poll_after_setup(app):
    import os
    import time

    now = time.time()
    _logged_in(app, bootstrap_ts=repr(now - 60))
    rc, out = app.call("status", "--json")
    assert rc == 0 and json.loads(out)["healthy"]  # just set up: the first poll is still coming
    _logged_in(app, bootstrap_ts=repr(now - 16 * 60))
    rc, out = app.call("status", "--json")
    info = json.loads(out)
    assert rc == 1 and not info["healthy"] and "no successful poll yet" in info["problem"]
    rc, out = app.call("status")
    assert rc == 1 and "unhealthy: no successful poll yet" in out
    _logged_in(app, last_poll=repr(now - 30))
    assert app.call("status", "--json")[0] == 0
    _logged_in(app, last_poll=repr(now - 3600))  # stale
    rc, out = app.call("status", "--json")
    assert rc == 1 and "no successful poll in the last" in json.loads(out)["problem"]
    # logged in but never bootstrapped (container never started `run`): the login time counts as setup
    st = Store(app.paths.state_db)
    for k in ("bootstrap_ts", "last_poll"):
        st.delete(k)
    st.close()
    assert app.call("status", "--json")[0] == 0
    os.utime(app.paths.token, (now - 3600, now - 3600))
    assert app.call("status", "--json")[0] == 1


@pytest.mark.parametrize(
    "argv", [["once"], ["backfill", "--days", "1"], ["undo", "--yes"], ["run", "--dry-run"]]
)
def test_second_instance_exits_1(app, capsys, argv):
    from email_town_labeler.lock import InstanceLock

    _logged_in(app)
    with InstanceLock(app.paths.data_dir) as held:
        rc, _ = app.call(*argv)
        assert rc == 1 and "already running" in capsys.readouterr().err
        assert not app.client.modifies and held.path.parent == app.paths.data_dir
        assert app.call("status", "--json")[0] in (0, 1)  # read-only commands do not take the lock
    if argv[0] != "run":
        assert app.call(*argv)[0] == 0  # released: the next one runs
    with (
        pytest.raises(Exception, match="already running"),
        InstanceLock(app.paths.data_dir),
        InstanceLock(app.paths.data_dir),
    ):
        pass


def test_client_secret_copied_owner_only(app):
    import os
    import stat

    app.secret.chmod(0o644)
    app.paths.ensure()
    app.paths.client_secret.write_text("{}")
    app.paths.client_secret.chmod(0o644)  # an old copy with wide permissions is tightened too
    app.inputs.append("http://localhost:8765/?state=abc&code=xyz")
    assert app.call("setup", "--client-secret", str(app.secret), "--headless", "--yes")[0] == 0
    assert json.loads(app.paths.client_secret.read_text()) == SECRET
    if os.name == "posix":
        assert stat.S_IMODE(app.paths.client_secret.stat().st_mode) == 0o600


def test_redact_masks_address_and_home(app, monkeypatch, tmp_path):
    monkeypatch.setattr(cli.Path, "home", classmethod(lambda cls: tmp_path))
    app.inputs.append("http://localhost:8765/?state=abc&code=xyz")
    app.call("setup", "--client-secret", str(app.secret), "--headless", "--yes")
    for argv, has_paths in ((["status"], False), (["status", "--json"], True), (["doctor"], True)):
        out = app.call(*argv)[1]
        shown = str(tmp_path) in out or json.dumps(str(tmp_path))[1:-1] in out  # --json escapes backslashes
        assert "me@example.test" in out and shown is has_paths, argv
        out = app.call(*argv, "--redact")[1]
        assert "me@example.test" not in out and str(tmp_path) not in out and "<email>" in out, argv
        assert ("~" in out) is has_paths
    assert cli.redact("C:\\Users\\pat\\x a@b.example", home="C:\\Users\\pat") == "~\\x <email>"
    assert cli.redact('{"p": "C:\\\\Users\\\\pat\\\\x"}', home="C:\\Users\\pat") == '{"p": "~\\\\x"}'


def test_global_options_after_the_subcommand(tmp_path, capsys):
    p = cli.build_parser()
    a = p.parse_args(["status", "--home", "/h", "-vv", "--json"])
    assert (a.home, a.verbose, a.json) == ("/h", 2, True)
    a = p.parse_args(["--home", "/g", "-v", "once"])
    assert (a.home, a.verbose) == ("/g", 1)  # a subcommand without them keeps the global values
    assert p.parse_args(["model", "list", "--home", "/m"]).home == "/m"
    assert p.parse_args(["service", "status", "-v"]).verbose == 1
    assert cli.main(["status", "--json", "--home", str(tmp_path / "h")]) == 1  # not logged in there
    assert json.loads(capsys.readouterr().out)["config_dir"] == str(tmp_path / "h" / "config")


def test_gcp_setup_print(app, capsys):
    rc, out = app.call("gcp-setup", "--print", "--project", "my-inbox-123")
    assert rc == 0 and "gcloud services enable gmail.googleapis.com --project my-inbox-123" in out
    assert "gcloud projects create my-inbox-123" in out and "auth/clients/create?project=my-inbox-123" in out
    assert app.call("gcp-setup", "--print")[1].count("PROJECT_ID") >= 3
    assert app.call("gcp-setup")[0] == 1 and "--print" in capsys.readouterr().err
    assert app.call("gcp-setup", "--print", "--project", "Bad_Id")[0] == 1


def test_contacts_commands_never_print_addresses_unasked(app, capsys):
    app.client.add_message(labels=["SENT"], to="Pal <Pal@x.test>", age_days=3)
    app.inputs.append("http://localhost:8765/?state=abc&code=xyz")
    rc, out = app.call("setup", "--client-secret", str(app.secret), "--headless", "--yes")
    assert rc == 0 and "1 contacts" in out and "pal@x.test" not in out.lower()
    assert "email-town-labeler/new sender" in {lb["name"] for lb in app.client.labels.values()}
    rc, out = app.call("contacts", "count", "--json")
    assert rc == 0 and json.loads(out)["contacts"] == 1 and json.loads(out)["rebuilt"]
    assert "1 contacts (last rebuilt: " in app.call("contacts", "count")[1]
    app.client.add_message(labels=["SENT"], to="two@x.test", age_days=1)
    rc, out = app.call("contacts", "rebuild")
    assert rc == 0 and "2 contacts from 2 sent messages" in out and "@" not in out
    capsys.readouterr()
    rc, out = app.call("contacts", "list")
    assert rc == 1 and not out and "--yes-show-addresses" in capsys.readouterr().err
    rc, out = app.call("contacts", "list", "--yes-show-addresses")
    assert rc == 0 and out.splitlines() == ["pal@x.test", "two@x.test"]
    app.client.add_message(labels=["INBOX", "CATEGORY_PERSONAL"], **{"from": "New <new@y.test>"})
    assert app.call("once")[0] == 0
    rc, out = app.call("status", "--json")
    info = json.loads(out)
    assert info["contacts"] == 2 and info["by_class"]["new_sender"] == 1
    rc, out = app.call("status")
    assert "new_sender" in out and "2 addresses you have sent mail to" in out and "y.test" not in out


# -- the published default model ------------------------------------------------------------------
REC = {"action_threshold": 0.07, "promotional_threshold": 0.8}


@pytest.fixture
def release(tmp_path, monkeypatch):
    """Catalog entry `tiny-cat` served from a local dir via EMAIL_TOWN_LABELER_CATALOG_BASE_URL."""
    import tarfile

    pkg = head_package(tmp_path / "src" / "tiny-pkg", name="tiny-pkg", config={"recommended_policy": REC})
    rel = tmp_path / "release"
    rel.mkdir()
    arc = rel / "tiny-pkg-v1.tar.gz"
    with tarfile.open(arc, "w:gz") as t:
        t.add(pkg, arcname=pkg.name)
    entry = catalog.CatalogEntry(
        "tiny-cat", "tiny-pkg", arc.name, sha256_file(arc), arc.stat().st_size, "onnx", "test entry"
    )
    monkeypatch.setitem(catalog.CATALOG, entry.key, entry)
    monkeypatch.setenv(catalog.BASE_URL_ENV, rel.as_uri())
    return entry


def _setup(app, *extra):
    app.inputs.append("http://localhost:8765/?state=abc&code=xyz")
    return app.call("setup", "--client-secret", str(app.secret), "--headless", *extra)


def test_setup_yes_falls_back_to_rules_without_the_extra(app):
    rc, out = _setup(app, "--yes")
    assert rc == 0, out
    assert load_config(app.paths.config_file).model_ref == "rules"
    assert (
        'pipx install --force "email-town-labeler[onnx] @ git+https://github.com/scamai/email-town-labeler"'
        in out
    )


def test_setup_yes_picks_the_default_model_when_the_extra_is_there(app, monkeypatch):
    monkeypatch.setattr(catalog, "extra_installed", lambda extra: True)
    rc, out = _setup(app, "--yes")
    assert rc == 0, out
    assert load_config(app.paths.config_file).model_ref == "town-modernbert"
    assert "downloading town-modernbert (824 MB" in out and "policy: action_threshold" in out
    # a re-run keeps the model already chosen
    rc, out = app.call("setup", "--yes", "--model", "rules")
    assert rc == 0 and load_config(app.paths.config_file).model_ref == "rules"
    rc, out = app.call("setup", "--yes")
    assert rc == 0 and load_config(app.paths.config_file).model_ref == "rules"


def test_setup_model_flag_and_interactive_offer(app):
    rc, out = _setup(app, "--yes", "--model", "town-modernbert")
    assert rc == 0 and load_config(app.paths.config_file).model_ref == "town-modernbert"
    app.inputs += ["", "n"]  # model (keep), no service
    rc, out = app.call("setup")
    assert rc == 0, out
    assert (
        "recommended: town-modernbert (824 MB download, once; needs the [onnx] extra: not installed)" in out
    )
    assert load_config(app.paths.config_file).model_ref == "town-modernbert"


def test_model_list_shows_the_catalog(app):
    rc, out = app.call("model", "list")
    assert rc == 0
    line = next(ln for ln in out.splitlines() if "town-modernbert " in ln)
    assert "not installed" in line and "824 MB" in line and "[onnx] missing" in line
    assert "town-modernbert-torch" in out and "635 MB" in out and "rules@" in out


def test_model_pull_catalog_use_info_status(app, release):
    rc, out = app.call("model", "pull", "tiny-cat", "--use")
    assert rc == 0, out
    assert f"sha256 verified ({release.sha256})" in out and "installed tiny-pkg@1" in out
    assert "needs the [onnx] extra" in out
    assert load_config(app.paths.config_file).model_ref == "tiny-cat"
    rc, out = app.call("model", "pull", "tiny-cat")
    assert rc == 0 and "already installed" in out
    rc, out = app.call("model", "list")
    line = next(ln for ln in out.splitlines() if "tiny-cat" in ln)
    assert line.startswith("*") and "installed" in line and "not installed" not in line
    rc, out = app.call("model", "info", "--json")
    info = json.loads(out)
    assert info["name"] == "tiny-pkg" and info["policy"]["action_threshold"] == {
        "value": 0.07,
        "source": "model",
    }
    rc, out = app.call("status", "--json")
    assert json.loads(out)["policy"]["promotional_threshold"] == {"value": 0.8, "source": "model"}
    rc, out = app.call("status")
    assert "action_threshold 0.07 (model), promotional_threshold 0.8 (model)" in out
    cfg_file = app.paths.config_file
    cfg_file.write_text(re.sub(r"# action_threshold = .*", "action_threshold = 0.3", cfg_file.read_text()))
    rc, out = app.call("status")
    assert "action_threshold 0.3 (config), promotional_threshold 0.8 (model)" in out


def test_model_pull_refuses_a_wrong_archive_hash(app, release, monkeypatch, capsys):
    monkeypatch.setitem(
        catalog.CATALOG, "tiny-cat", catalog.CatalogEntry(**{**release.__dict__, "sha256": "0" * 64})
    )
    rc, _ = app.call("model", "pull", "tiny-cat")
    assert rc == 1 and "sha256 mismatch" in capsys.readouterr().err
    assert not (app.paths.models_dir / "tiny-pkg").exists()


def test_doctor_shows_the_effective_policy(app):
    _setup(app, "--yes", "--model", "rules")
    _, out = app.call("doctor")
    assert "policy" in out and "action_threshold 0.2 (default), promotional_threshold 0.6 (default)" in out


def _town_day(app, monkeypatch, n=40):
    """Setup, then n new messages that Town (the teacher) labels; the CLI resolves the fake model."""
    from email_town_labeler.models.base import ModelManifest

    monkeypatch.setattr(cli, "_manifest_or_none", lambda cfg, paths: ModelManifest("fake", "1", "fake"))
    app.inputs.append("http://localhost:8765/?state=abc&code=xyz")
    app.call("setup", "--client-secret", str(app.secret), "--headless", "--yes")
    for name in ("action needed", "newsletters", "promotional", "orders"):
        app.client.create_label(name)
    ids = []
    for i in range(n):
        bulk = i % 2 == 0
        h = {"list-id": "<list.example.test>"} if bulk else {}
        ids.append(app.client.add_message(cls="promotional", prob=0.7, **h))
    assert app.call("once")[0] == 0
    for i, m in enumerate(ids):
        app.client.user_add_label(m, app.client.label_id("newsletters" if i % 2 == 0 else "promotional"))
    assert app.call("once")[0] == 0
    return ids


def test_learn_status_reset_and_export(app, monkeypatch, tmp_path):
    rc, out = app.call("learn", "--dry-run")
    assert rc == 0 and "collecting signals: 0 of 30" in out and not app.paths.learned.exists()
    _town_day(app, monkeypatch)
    rc, out = app.call("status", "--json")
    li = json.loads(out)["learning"]
    assert li["signals"]["teacher"] == 40 and not li["head_active"] and li["teacher"] == "auto"
    rc, out = app.call("learn", "--dry-run")
    assert rc == 0 and "teacher 40" in out and "dry run" in out
    assert not app.paths.learned.exists()
    rc, out = app.call("learn")
    assert rc == 0 and "saved personal.json" in out, out
    assert "personal head personal-" in out and "held-out accuracy" in out
    rc, out = app.call("status")
    assert "personal head" in out and "active" in out and "40 teacher labels" in out
    assert "@example.test" not in out.replace("me@example.test", "")  # no sender addresses
    rc, out = app.call("status", "--json")
    assert json.loads(out)["learning"]["head_active"] is True
    # export: ids and numbers only
    f = tmp_path / "signals.jsonl"
    rc, out = app.call("learn", "--export", str(f))
    rows = [json.loads(x) for x in f.read_text().splitlines()]
    assert rc == 0 and len(rows) == 40 and {r["kind"] for r in rows} == {"teacher"}
    assert set(rows[0]) == {"msg_id", "label", "not_label", "kind", "weight", "scores", "features", "model"}
    assert "made-up" not in f.read_text()
    # the escape hatch
    rc, out = app.call("personalize", "--reset")
    assert rc == 0 and "removed the personal head" in out and not app.paths.learned.exists()
    rc, out = app.call("status", "--json")
    li = json.loads(out)["learning"]
    assert not li["head_active"] and li["signals"]["teacher"] == 0  # signals before the reset are not reused


def test_personalize_without_a_head_runs_learn(app, monkeypatch):
    _town_day(app, monkeypatch)
    rc, out = app.call("personalize")  # model.ref = rules: no trainable head
    assert rc == 0 and "learning on its stored scores instead" in out and "saved personal.json" in out
    assert load_config(app.paths.config_file).model_ref == "rules"  # unchanged


def test_status_policy_shows_learned_thresholds(app, monkeypatch):
    from email_town_labeler import learn

    _town_day(app, monkeypatch)
    learn.save(
        app.paths.learned,
        learn.Artifact(base_model="fake@1", created=1.0, policy={"promotional_threshold": 0.9}),
    )
    _rc, out = app.call("status", "--json")
    assert json.loads(out)["policy"]["promotional_threshold"] == {"value": 0.9, "source": "learned"}
    _rc, out = app.call("status")
    assert "promotional_threshold 0.9 (learned)" in out
