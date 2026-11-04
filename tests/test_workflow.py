"""The far side of the workflow boundary: real GmailClient against an in-memory Gmail service."""

import copy
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest
from googleapiclient.errors import HttpError

from email_town_labeler import cli
from email_town_labeler.config import Config, ConfigError
from email_town_labeler.gmail.client import GmailClient
from email_town_labeler.gmail.labels import LabelMap
from email_town_labeler.workflow import audit_labels, daily_plan, expected_classes, inspect_inbox, show_labels
from tests.fakes.fake_service import FakeGmail


def mailbox(cfg=None):
    cfg = cfg or Config()
    fake = FakeGmail()
    client = GmailClient(fake, sleep=lambda _: None)
    labels = LabelMap(client, cfg.label_prefix, cfg.display_names(), colors=cfg.colors())
    for cls in expected_classes(cfg):
        labels.ensure(cls)
    return cfg, fake, client, labels


def test_visibility_repair_leaves_mail_colors_foreign_and_history_untouched():
    cfg, fake, client, labels = mailbox()
    action = labels.ensure("action_needed")
    foreign = client.create_label("outside")
    child = client.create_label("e/custom")
    for lid in (action, foreign["id"], child["id"]):
        fake.users().labels().patch(userId="me", id=lid, body={"labelListVisibility": "labelHide"}).execute()
    mid = fake.deliver(label_ids=["INBOX", "UNREAD", action, foreign["id"], child["id"]])
    mail_before, history = fake.labels_of(mid), fake.history_id
    color = client.get_label(action)["color"]
    before = copy.deepcopy(fake._labels)
    assert not audit_labels(client, cfg)["ok"]
    dry = show_labels(client, cfg, dry_run=True)
    assert dry["planned"] == 1 and dry["changed"] == 0 and not dry["ok"]
    assert fake._labels == before
    actual = show_labels(client, cfg, dry_run=False)
    assert actual["ok"] and actual["changed"] == 1
    assert client.get_label(action)["labelListVisibility"] == "labelShow"
    assert client.get_label(action)["color"] == color
    assert client.get_label(foreign["id"])["labelListVisibility"] == "labelHide"
    assert client.get_label(child["id"])["labelListVisibility"] == "labelHide"
    assert fake.labels_of(mid) == mail_before and fake.history_id == history
    assert show_labels(client, cfg, dry_run=False)["changed"] == 0


def test_empty_mailbox_missing_parent_and_custom_names():
    cfg = Config(label_prefix="Project", label_names={"action_needed": "要处理"}, new_sender=False)
    fake = FakeGmail()
    client = GmailClient(fake)
    assert not audit_labels(client, cfg)["ok"]
    # Gmail permits children without a parent; an existing mixed-case child must be reused.
    existing = client.create_label("project/要处理")
    report = show_labels(client, cfg, dry_run=False)
    assert report["ok"] and report["changed"] == 6
    assert fake.label_id("Project/要处理") == existing["id"]
    assert fake.label_id("Project") is not None
    assert fake.label_id("Project/new sender") is None
    assert fake.label_id("Project/unknown") is None


def test_unknown_visibility_and_read_errors_are_not_a_pass():
    cfg, fake, client, labels = mailbox()
    lid = labels.ensure("action_needed")
    del fake._labels[lid]["messageListVisibility"]
    row = next(r for r in audit_labels(client, cfg)["labels"] if r.get("id") == lid)
    assert row["state"] == "unknown"
    fake.fail_next(1, 403, method="labels.get", reason="insufficientPermissions")
    with pytest.raises(HttpError):
        audit_labels(client, cfg)


def test_label_deleted_during_audit_is_missing():
    cfg, fake, client, _labels = mailbox()
    fake.fail_next(1, 404, method="labels.get")
    report = audit_labels(client, cfg)
    assert not report["ok"] and report["labels"][0]["state"] == "missing"


@pytest.mark.parametrize("day,hours", [("2026-03-08", 23), ("2026-11-01", 25)])
def test_calendar_day_uses_both_local_midnights_across_dst(day, hours):
    try:
        ZoneInfo("America/Los_Angeles")
    except ZoneInfoNotFoundError:
        pytest.skip("OS has no IANA timezone database; named zones require tzdata")
    plan = daily_plan(Config(), day=day, timezone="America/Los_Angeles")
    assert plan["end_epoch"] - plan["start_epoch"] == hours * 3600
    assert datetime.fromtimestamp(plan["start_epoch"], ZoneInfo("America/Los_Angeles")).hour == 0


def test_daily_queries_match_current_mail_with_old_unread_read_and_archived():
    cfg, fake, client, labels = mailbox()
    plan = daily_plan(cfg, day="2026-10-30")
    start, end = plan["start_epoch"] * 1000, plan["end_epoch"] * 1000
    action = labels.ensure("action_needed")
    old = fake.deliver(label_ids=["INBOX", action], internal_date=start - 1000)
    today = fake.deliver(label_ids=["INBOX", "UNREAD", action], internal_date=start + 1000)
    fake.deliver(label_ids=["INBOX", action], internal_date=end + 1000)
    fake.deliver(label_ids=[action], internal_date=start + 1000)  # archived, not in these inbox views
    fake.deliver(label_ids=["INBOX"], internal_date=start - 5000)  # unclassified, even if read and old
    fake.deliver(label_ids=["INBOX", labels.ensure("new_sender")], internal_date=start)
    fake.deliver(label_ids=["INBOX", labels.ensure("orders")], internal_date=start)
    report = inspect_inbox(client, plan, view=None, limit=10)
    counts = {r["key"]: r["count"] for r in report["views"]}
    assert counts == {"action": 3, "today": 1, "backlog": 1, "new-sender": 1, "unclassified": 1}
    assert all(not r["truncated"] for r in report["views"])
    assert fake.labels_of(old) == ["INBOX", action]
    assert "UNREAD" in fake.labels_of(today)
    # Querying never opens message content or creates/changes a label/message.
    assert "messages.get" not in fake.calls


def test_cap_is_not_reported_as_total_and_pagination_is_followed():
    cfg, fake, client, labels = mailbox()
    lid = labels.ensure("action_needed")
    for _ in range(502):
        fake.deliver(label_ids=["INBOX", lid])
    plan = daily_plan(cfg)
    report = inspect_inbox(client, plan, view="action", limit=501)
    assert report["views"][0]["count"] == 501 and report["views"][0]["truncated"]
    exact = inspect_inbox(client, plan, view="action", limit=502)["views"][0]
    assert exact["count"] == 502 and not exact["truncated"]


@pytest.mark.parametrize("kwargs", [{"day": "nonsense"}, {"timezone": "not/a-zone"}, {"day": "9999-12-31"}])
def test_invalid_day_or_zone_fails_clearly(kwargs):
    with pytest.raises(ConfigError):
        daily_plan(Config(), **kwargs)


@pytest.mark.parametrize("name", ['bad" OR in:anywhere', "bad\\x", "bad\nname"])
def test_search_does_not_accept_ambiguous_label_names(name):
    with pytest.raises(ConfigError):
        daily_plan(Config(label_names={"orders": name}))


def test_cli_plan_is_offline_and_visibility_wiring_changes_only_definitions(tmp_path, monkeypatch, capsys):
    _cfg, fake, client, labels = mailbox()
    home = tmp_path / "account"
    out = []

    def fail(*_):
        pytest.fail("offline plan tried to construct a client/model/store")

    monkeypatch.setattr(cli, "make_client", fail)
    monkeypatch.setattr(cli, "make_model", fail)
    monkeypatch.setattr(cli, "make_store", fail)
    assert cli.main(["--home", str(home), "triage", "--plan", "--json"], print_fn=out.append) == 0
    assert not home.exists()
    monkeypatch.setattr(cli, "make_client", lambda _: client)
    lid = labels.ensure("orders")
    fake._labels[lid]["messageListVisibility"] = "hide"
    assert cli.main(["--home", str(home), "labels", "check", "--json"], print_fn=out.append) == 1
    assert cli.main(["--home", str(home), "labels", "show", "--dry-run"], print_fn=out.append) == 0
    assert client.get_label(lid)["messageListVisibility"] == "hide"
    assert cli.main(["--home", str(home), "labels", "show"], print_fn=out.append) == 0
    assert client.get_label(lid)["messageListVisibility"] == "show"
    assert cli.main(["--home", str(home), "triage", "--limit", "0"], print_fn=out.append) == 1
    assert "between 1 and 1000" in capsys.readouterr().err


def test_visibility_dry_run_works_while_engine_holds_lock(tmp_path, monkeypatch, capsys):
    from email_town_labeler.config import Paths
    from email_town_labeler.lock import InstanceLock

    _cfg, _fake, client, _labels = mailbox()
    home = tmp_path / "account"
    paths = Paths.default(home).ensure()
    monkeypatch.setattr(cli, "make_client", lambda _: client)
    with InstanceLock(paths.data_dir):
        for args in (["labels", "check"], ["labels", "show", "--dry-run"], ["triage"]):
            assert cli.main(["--home", str(home), *args], print_fn=lambda *_: None) == 0
        assert cli.main(["--home", str(home), "labels", "show"], print_fn=lambda *_: None) == 1
    assert "already" in capsys.readouterr().err.lower()


def test_visibility_repair_failure_is_reported_and_safe_to_resume():
    cfg, fake, client, labels = mailbox()
    lid = labels.ensure("orders")
    fake._labels[lid]["labelListVisibility"] = "labelHide"
    fake.fail_next(1, 403, method="labels.patch", reason="insufficientPermissions")
    with pytest.raises(HttpError):
        show_labels(client, cfg, dry_run=False)
    assert client.get_label(lid)["labelListVisibility"] == "labelHide"
    assert show_labels(client, cfg, dry_run=False)["ok"]


def test_concurrent_label_rename_is_not_mistaken_for_our_label(monkeypatch):
    cfg, _fake, client, labels = mailbox()
    original = client.get_label
    action = labels.ensure("action_needed")

    def renamed(lid):
        row = original(lid)
        return {**row, "name": "outside/renamed"} if lid == action else row

    monkeypatch.setattr(client, "get_label", renamed)
    report = audit_labels(client, cfg)
    assert not report["ok"]
    assert any(r["state"] == "changed-during-check" and "id" not in r for r in report["labels"])
    with pytest.raises(ConfigError, match="changed during repair"):
        show_labels(client, cfg, dry_run=False)


def test_config_dry_run_prevents_visibility_writes():
    cfg, fake, client, labels = mailbox()
    cfg.dry_run = True
    # The library requires explicit dry_run; CLI carries the config into that argument.
    lid = labels.ensure("orders")
    fake._labels[lid]["messageListVisibility"] = "hide"
    assert show_labels(client, cfg, dry_run=cfg.dry_run)["changed"] == 0
    assert client.get_label(lid)["messageListVisibility"] == "hide"
