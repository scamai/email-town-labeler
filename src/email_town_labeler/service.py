"""Run `email-town-labeler run` in the background at login: launchd (macOS), systemd --user (Linux), Task Scheduler
(Windows). Plan generation is pure (text + commands, platform injected); only `apply` touches the system."""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

LAUNCHD_LABEL = "io.github.email-town-labeler"
SYSTEMD_UNIT = "email-town-labeler.service"
WINDOWS_TASK = "email-town-labeler"
PLATFORMS = ("macos", "linux", "windows")

Runner = Callable[..., Any]  # subprocess.run-compatible


@dataclass(frozen=True)
class Cmd:
    args: list[str]
    may_fail: bool = False  # e.g. "stop it if it was running"


@dataclass
class Plan:
    platform: str
    action: str
    files: dict[Path, str] = field(default_factory=dict)  # write these first
    remove: list[Path] = field(default_factory=list)
    commands: list[Cmd] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Target:
    """Everything the generators need, so they stay pure."""

    platform: str
    program: list[str]  # absolute argv prefix up to (not including) the subcommand
    user_home: Path
    logs_dir: Path
    uid: int = 0
    xdg_config_home: Path | None = None
    inbox_home: str | None = None  # EMAIL_TOWN_LABELER_HOME to bake into the service

    @property
    def argv(self) -> list[str]:
        return self.program + (["--home", self.inbox_home] if self.inbox_home else []) + ["run"]


def detect_platform(sys_platform: str | None = None) -> str:
    p = sys_platform or sys.platform
    if p == "darwin":
        return "macos"
    if p.startswith(("win", "cygwin")):
        return "windows"
    return "linux"


def resolve_program(
    platform: str, executable: str | None = None, exists: Callable[[Path], bool] = Path.exists
) -> list[str]:
    """Absolute argv for the CLI. Uses the interpreter's own venv (pipx-safe); never resolves symlinks,
    which would escape the venv."""
    exe = Path(os.path.abspath(executable or sys.executable))
    if platform == "windows":
        pyw = exe.with_name("pythonw.exe")
        if exists(pyw):
            return [str(pyw), "-m", "email_town_labeler"]  # no console window
        script = exe.with_name("email-town-labeler.exe")
        return [str(script)] if exists(script) else [str(exe), "-m", "email_town_labeler"]
    script = exe.with_name("email-town-labeler")
    return [str(script)] if exists(script) else [str(exe), "-m", "email_town_labeler"]


def default_target(logs_dir: Path, *, platform: str | None = None, inbox_home: str | None = None) -> Target:
    plat = platform or detect_platform()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return Target(
        platform=plat,
        program=resolve_program(plat),
        user_home=Path.home(),
        logs_dir=Path(logs_dir),
        uid=os.getuid() if hasattr(os, "getuid") else 0,
        xdg_config_home=Path(xdg) if xdg else None,
        inbox_home=inbox_home,
    )


# -- file locations -------------------------------------------------------------------------------
def service_file(t: Target) -> Path | None:
    if t.platform == "macos":
        return t.user_home / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
    if t.platform == "linux":
        return (t.xdg_config_home or t.user_home / ".config") / "systemd" / "user" / SYSTEMD_UNIT
    return None  # Task Scheduler keeps its own store


# -- generators (pure) ----------------------------------------------------------------------------
def launchd_plist(t: Target) -> str:
    d = {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": t.argv,
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},  # restart on crash, not after a clean stop
        "ThrottleInterval": 30,
        "ProcessType": "Background",
        "StandardOutPath": str(t.logs_dir / "service.out.log"),
        "StandardErrorPath": str(t.logs_dir / "service.err.log"),
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1"},
    }
    return plistlib.dumps(d, sort_keys=False).decode("utf-8")


def _systemd_quote(arg: str) -> str:
    arg = arg.replace("%", "%%")
    if arg and not any(c in arg for c in " \t\"'\\;$"):
        return arg
    return '"' + arg.replace("\\", "\\\\").replace('"', '\\"') + '"'


def systemd_unit(t: Target) -> str:
    return "\n".join(
        [
            "[Unit]",
            "Description=email-town-labeler Gmail auto-labeler",
            "Wants=network-online.target",
            "After=network-online.target",
            "",
            "[Service]",
            "Type=simple",
            f"ExecStart={' '.join(_systemd_quote(a) for a in t.argv)}",
            "Restart=on-failure",
            "RestartSec=30",
            "Environment=PYTHONUNBUFFERED=1",
            "",
            "[Install]",
            "WantedBy=default.target",
            "",
        ]
    )


def windows_command_line(t: Target) -> str:
    return subprocess.list2cmdline(t.argv)


def plan_install(t: Target) -> Plan:
    f = service_file(t)
    if t.platform == "macos":
        assert f is not None
        dom = f"gui/{t.uid}"
        return Plan(
            "macos",
            "install",
            files={f: launchd_plist(t)},
            commands=[
                Cmd(["launchctl", "bootout", f"{dom}/{LAUNCHD_LABEL}"], may_fail=True),
                Cmd(["launchctl", "bootstrap", dom, str(f)]),
            ],
            notes=[f"LaunchAgent {LAUNCHD_LABEL} starts at login; logs in {t.logs_dir}"],
        )
    if t.platform == "linux":
        assert f is not None
        return Plan(
            "linux",
            "install",
            files={f: systemd_unit(t)},
            commands=[
                Cmd(["systemctl", "--user", "daemon-reload"]),
                Cmd(["systemctl", "--user", "enable", "--now", SYSTEMD_UNIT]),
            ],
            notes=[
                "to keep it running while logged out: loginctl enable-linger $USER",
                f"logs: journalctl --user -u {SYSTEMD_UNIT} (and {t.logs_dir})",
            ],
        )
    if t.platform == "windows":
        return Plan(
            "windows",
            "install",
            commands=[
                Cmd(
                    [
                        "schtasks",
                        "/Create",
                        "/F",
                        "/SC",
                        "ONLOGON",
                        "/TN",
                        WINDOWS_TASK,
                        "/TR",
                        windows_command_line(t),
                        "/RL",
                        "LIMITED",
                    ]
                ),
                Cmd(["schtasks", "/Run", "/TN", WINDOWS_TASK], may_fail=True),
            ],
            notes=[
                (
                    "the task runs at logon with no console window (pythonw); "
                    "if /Create is denied, run it once from an elevated prompt"
                ),
                f"logs in {t.logs_dir}",
            ],
        )
    raise ValueError(f"unknown platform {t.platform!r}")


def plan_uninstall(t: Target) -> Plan:
    f = service_file(t)
    if t.platform == "macos":
        assert f is not None
        return Plan(
            "macos",
            "uninstall",
            remove=[f],
            commands=[Cmd(["launchctl", "bootout", f"gui/{t.uid}/{LAUNCHD_LABEL}"], may_fail=True)],
        )
    if t.platform == "linux":
        assert f is not None
        return Plan(
            "linux",
            "uninstall",
            remove=[f],
            commands=[
                Cmd(["systemctl", "--user", "disable", "--now", SYSTEMD_UNIT], may_fail=True),
                Cmd(["systemctl", "--user", "daemon-reload"], may_fail=True),
            ],
        )
    if t.platform == "windows":
        return Plan(
            "windows",
            "uninstall",
            commands=[
                Cmd(["schtasks", "/End", "/TN", WINDOWS_TASK], may_fail=True),
                Cmd(["schtasks", "/Delete", "/TN", WINDOWS_TASK, "/F"]),
            ],
        )
    raise ValueError(f"unknown platform {t.platform!r}")


def status_command(t: Target) -> list[str]:
    return {
        "macos": ["launchctl", "print", f"gui/{t.uid}/{LAUNCHD_LABEL}"],
        "linux": ["systemctl", "--user", "is-active", SYSTEMD_UNIT],
        "windows": ["schtasks", "/Query", "/TN", WINDOWS_TASK, "/FO", "LIST", "/V"],
    }[t.platform]


def parse_status(t: Target, returncode: int, stdout: str, file_exists: bool) -> dict:
    out = stdout or ""
    if t.platform == "macos":
        installed = file_exists
        running = returncode == 0 and "state = running" in out
    elif t.platform == "linux":
        installed = file_exists
        running = out.strip() == "active"
    else:
        installed = returncode == 0
        running = installed and any(
            ln.split(":", 1)[-1].strip() == "Running"
            for ln in out.splitlines()
            if ln.strip().startswith("Status")
        )
    state = "running" if running else "installed (not running)" if installed else "not installed"
    return {"platform": t.platform, "installed": installed, "running": running, "state": state}


# -- side effects ---------------------------------------------------------------------------------
def apply(plan: Plan, *, run: Runner = subprocess.run, echo: Callable[[str], None] = print) -> bool:
    for path, text in plan.files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        echo(f"wrote {path}")
    for c in plan.commands:
        try:
            r = run(c.args, capture_output=True, text=True)
            rc, err = r.returncode, (r.stderr or "").strip()
        except OSError as e:
            rc, err = 127, str(e)
        if rc != 0 and not c.may_fail:
            echo(f"✗ {' '.join(c.args)} failed ({rc}): {err[:300]}")
            return False
    # Remove files only after the commands: `systemctl --user disable --now` fails (and leaves the
    # service running) once the unit file is gone.
    for path in plan.remove:
        if path.exists():
            path.unlink()
            echo(f"removed {path}")
    for n in plan.notes:
        echo(f"  {n}")
    return True


def systemd_user_available(run: Runner = subprocess.run) -> bool:
    try:
        return (
            run(["systemctl", "--user", "show-environment"], capture_output=True, text=True).returncode == 0
        )
    except OSError:
        return False


def install(t: Target, *, run: Runner = subprocess.run, echo: Callable[[str], None] = print) -> bool:
    plan = plan_install(t)
    if t.platform == "linux" and not systemd_user_available(run):
        f = service_file(t)
        assert f is not None
        echo("systemctl --user is not available here (container, WSL1 or no user session).")
        echo(f"Unit file for when it is ({f}):\n\n{systemd_unit(t)}")
        echo("Otherwise run in the foreground under your own supervisor:  " + " ".join(t.argv))
        return False
    return apply(plan, run=run, echo=echo)


def uninstall(t: Target, *, run: Runner = subprocess.run, echo: Callable[[str], None] = print) -> bool:
    return apply(plan_uninstall(t), run=run, echo=echo)


def status(t: Target, *, run: Runner = subprocess.run) -> dict:
    f = service_file(t)
    exists = bool(f and f.exists())
    try:
        r = run(status_command(t), capture_output=True, text=True, timeout=10)
        rc, out = r.returncode, r.stdout or ""
    except (OSError, subprocess.TimeoutExpired):
        rc, out = 127, ""
    return parse_status(t, rc, out, exists)
