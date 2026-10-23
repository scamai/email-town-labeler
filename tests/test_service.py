from __future__ import annotations

import os
import plistlib
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from email_town_labeler import service
from email_town_labeler.service import Target


def target(platform: str, tmp_path: Path, **kw) -> Target:
    prog = (
        ["C:\\Users\\me\\pipx\\venvs\\email-town-labeler\\Scripts\\pythonw.exe", "-m", "email_town_labeler"]
        if platform == "windows"
        else ["/home/me/.local/pipx/venvs/email-town-labeler/bin/email-town-labeler"]
    )
    return Target(
        platform=platform,
        program=prog,
        user_home=tmp_path / "home",
        logs_dir=tmp_path / "logs",
        uid=501,
        **kw,
    )


class FakeRun:
    def __init__(self, rc: int = 0, stdout: str = "", fail_on: str | None = None):
        self.calls: list[list[str]] = []
        self.rc, self.stdout, self.fail_on = rc, stdout, fail_on

    def __call__(self, args, **kw):
        self.calls.append(list(args))
        rc = 1 if self.fail_on and self.fail_on in args else self.rc
        return SimpleNamespace(returncode=rc, stdout=self.stdout, stderr="boom" if rc else "")


@pytest.mark.parametrize(
    "sys_platform,want",
    [
        ("darwin", "macos"),
        ("linux", "linux"),
        ("win32", "windows"),
        ("cygwin", "windows"),
        ("freebsd14", "linux"),
    ],
)
def test_detect_platform(sys_platform, want):
    assert service.detect_platform(sys_platform) == want


def test_resolve_program_prefers_venv_script_and_pythonw():
    yes, no = (lambda p: True), (lambda p: False)
    assert service.resolve_program("linux", "/v/bin/python", yes) == [
        os.path.abspath("/v/bin/email-town-labeler")
    ]
    assert service.resolve_program("macos", "/v/bin/python", no)[1:] == ["-m", "email_town_labeler"]
    win = service.resolve_program("windows", "C:/v/Scripts/python.exe", yes)
    assert win[0].endswith("pythonw.exe") and win[1:] == ["-m", "email_town_labeler"]
    only_script = service.resolve_program(
        "windows", "C:/v/Scripts/python.exe", lambda p: p.name == "email-town-labeler.exe"
    )
    assert only_script[0].endswith("email-town-labeler.exe") and len(only_script) == 1


def test_macos_plan(tmp_path):
    t = target("macos", tmp_path, inbox_home="/srv/jev data")
    plan = service.plan_install(t)
    ((path, text),) = plan.files.items()
    assert path == tmp_path / "home/Library/LaunchAgents/io.github.email-town-labeler.plist"
    d = plistlib.loads(text.encode())
    assert d["ProgramArguments"] == [t.program[0], "--home", "/srv/jev data", "run"]
    assert d["RunAtLoad"] is True and d["KeepAlive"] == {"SuccessfulExit": False}
    assert d["StandardErrorPath"].startswith(str(tmp_path / "logs"))
    assert [c.args for c in plan.commands] == [
        ["launchctl", "bootout", "gui/501/io.github.email-town-labeler"],
        ["launchctl", "bootstrap", "gui/501", str(path)],
    ]
    assert plan.commands[0].may_fail and not plan.commands[1].may_fail
    un = service.plan_uninstall(t)
    assert un.remove == [path] and un.commands[0].args[:2] == ["launchctl", "bootout"]
    assert service.status_command(t) == ["launchctl", "print", "gui/501/io.github.email-town-labeler"]
    assert service.parse_status(t, 0, "state = running\n", True)["state"] == "running"
    assert service.parse_status(t, 113, "", False)["state"] == "not installed"


def test_linux_plan(tmp_path):
    t = target("linux", tmp_path, xdg_config_home=tmp_path / "xdg")
    plan = service.plan_install(t)
    ((path, text),) = plan.files.items()
    assert path == tmp_path / "xdg/systemd/user/email-town-labeler.service"
    assert "Restart=on-failure" in text and "WantedBy=default.target" in text
    assert f"ExecStart={t.program[0]} run" in text
    assert [c.args for c in plan.commands][-1] == [
        "systemctl",
        "--user",
        "enable",
        "--now",
        "email-town-labeler.service",
    ]
    assert any("enable-linger" in n for n in plan.notes)
    default_loc = service.service_file(target("linux", tmp_path))
    assert default_loc == tmp_path / "home/.config/systemd/user/email-town-labeler.service"
    assert service.parse_status(t, 0, "active\n", True)["running"] is True
    assert service.parse_status(t, 3, "inactive\n", True)["state"] == "installed (not running)"


def test_systemd_quoting(tmp_path):
    t = Target("linux", ["/opt/my apps/bin/email-town-labeler"], tmp_path, tmp_path, inbox_home='/d/100%"x"')
    line = next(ln for ln in service.systemd_unit(t).splitlines() if ln.startswith("ExecStart="))
    assert line == 'ExecStart="/opt/my apps/bin/email-town-labeler" --home "/d/100%%\\"x\\"" run'


def test_windows_plan(tmp_path):
    t = target("windows", tmp_path, inbox_home="C:\\Users\\me\\email town labeler")
    plan = service.plan_install(t)
    assert plan.files == {} and service.service_file(t) is None
    create = plan.commands[0].args
    assert create[:6] == ["schtasks", "/Create", "/F", "/SC", "ONLOGON", "/TN"]
    tr = create[create.index("/TR") + 1]
    assert tr == subprocess.list2cmdline(t.argv)
    assert "pythonw.exe" in tr and tr.endswith(
        '-m email_town_labeler --home "C:\\Users\\me\\email town labeler" run'
    )
    assert plan.commands[1].args == ["schtasks", "/Run", "/TN", "email-town-labeler"]
    assert service.plan_uninstall(t).commands[-1].args == [
        "schtasks",
        "/Delete",
        "/TN",
        "email-town-labeler",
        "/F",
    ]
    out = "TaskName: \\email-town-labeler\nStatus:   Running\n"
    assert service.parse_status(t, 0, out, False)["state"] == "running"
    assert service.parse_status(t, 0, "Status: Ready\n", False)["state"] == "installed (not running)"
    assert service.parse_status(t, 1, "", False)["installed"] is False


def test_apply_writes_and_runs_with_fake_runner(tmp_path):
    t = target("linux", tmp_path)
    run, lines = FakeRun(), []
    assert service.install(t, run=run, echo=lines.append)
    f = service.service_file(t)
    assert f.exists() and run.calls[0] == ["systemctl", "--user", "show-environment"]
    assert ["systemctl", "--user", "enable", "--now", "email-town-labeler.service"] in run.calls
    assert service.status(t, run=FakeRun(stdout="active"))["state"] == "running"
    assert service.uninstall(t, run=FakeRun(), echo=lines.append) and not f.exists()


def test_linux_without_systemd_prints_instructions(tmp_path):
    t = target("linux", tmp_path)
    lines: list[str] = []
    assert not service.install(t, run=FakeRun(rc=1), echo=lines.append)
    text = "\n".join(lines)
    assert "not available" in text and "Restart=on-failure" in text
    assert not service.service_file(t).exists()


def test_apply_stops_on_required_failure(tmp_path):
    t = target("macos", tmp_path)
    lines: list[str] = []
    run = FakeRun(fail_on="bootstrap")
    assert not service.apply(service.plan_install(t), run=run, echo=lines.append)
    assert any("failed" in ln for ln in lines)
    # bootout may fail (not loaded yet) without aborting
    assert service.apply(service.plan_install(t), run=FakeRun(fail_on="bootout"), echo=lines.append)


def test_missing_binary_is_reported_not_raised(tmp_path):
    def boom(*a, **k):
        raise FileNotFoundError("schtasks")

    t = target("windows", tmp_path)
    assert not service.apply(service.plan_install(t), run=boom, echo=lambda s: None)
    assert service.status(t, run=boom)["state"] == "not installed"


def test_uninstall_stops_the_unit_before_deleting_its_file(tmp_path):
    t = target("linux", tmp_path)
    assert service.install(t, run=FakeRun(), echo=lambda s: None)
    f = service.service_file(t)
    seen: list[bool] = []

    def run(args, **kw):
        if "disable" in args:
            seen.append(f.exists())  # real systemctl refuses to disable a unit whose file is gone
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    assert service.uninstall(t, run=run, echo=lambda s: None)
    assert seen == [True] and not f.exists()
