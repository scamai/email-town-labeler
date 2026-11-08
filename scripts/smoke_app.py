"""Launch the Email Town window (built app or source) against a fresh temporary home and prove it works.

Run:  python scripts/smoke_app.py -- "dist/Email Town.app/Contents/MacOS/Email Town"
      python scripts/smoke_app.py -- "dist/Email Town/Email Town.exe"
      python scripts/smoke_app.py -- python -m email_town_labeler.desktop.window   (from source)

It always uses a new empty EMAIL_TOWN_LABELER_HOME (never your mailbox data), and checks, in order:
  1. the server in launch.json answers /api/proof with the HMAC of its launch token, and the UI loads;
  2. a second start reuses that server (same pid in launch.json) and opens its own window;
  3. --screenshot PATH (macOS): captures the app's window (needs Screen Recording permission);
  4. an authenticated shutdown (`desktop --stop`'s request) closes the window and ends the app (exit 0);
  5. no process of the app is left, model worker included.
With a built app it first runs `--self-check` (imports, web assets, CA store, frozen model worker).
Exit 0 only if every step passed. No mail, account or credential is involved.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import http.client
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from email_town_labeler.config import Paths
from email_town_labeler.desktop.launcher import running


def step(ok: bool, what: str) -> bool:
    print(f"{'ok  ' if ok else 'FAIL'}  {what}", flush=True)
    return ok


def wait_for(fn, timeout: float, interval: float = 0.2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(interval)
    return None


def launch(app: list[str], env: dict, log: Path) -> subprocess.Popen:
    with log.open("ab") as out:
        return subprocess.Popen(app, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=out)


def proof(root: Path) -> str | None:
    """GET /api/proof on the port in launch.json; the reply must be HMAC(token, challenge)."""
    try:
        url = urlsplit(json.loads((root / "launch.json").read_text())["url"])
    except (OSError, ValueError, KeyError):
        return None
    token = parse_qs(url.fragment).get("token", [""])[0]
    challenge = secrets.token_hex(32)
    conn = http.client.HTTPConnection("127.0.0.1", url.port, timeout=3)
    try:
        conn.request("GET", "/api/proof?challenge=" + challenge)
        response = conn.getresponse()
        got = json.loads(response.read()).get("proof", "")
        want = hmac.digest(token.encode(), ("EmailTown:" + challenge).encode(), hashlib.sha256).hex()
        if response.status == 200 and hmac.compare_digest(got, want):
            return f"port {url.port}"
    except (OSError, ValueError, http.client.HTTPException):
        return None
    finally:
        conn.close()
    return None


def page(port: int) -> int:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        conn.request("GET", "/")
        response = conn.getresponse()
        body = response.read()
        return response.status if b"Email Town" in body else -1
    finally:
        conn.close()


def shutdown(active: dict) -> int:
    conn = http.client.HTTPConnection("127.0.0.1", active["port"], timeout=3)
    try:
        conn.request(
            "POST",
            "/api/shutdown",
            "{}",
            {
                "Content-Type": "application/json",
                "Origin": active["origin"],
                "X-Email-Town": "local",
                "Authorization": "Bearer " + active["session"],
            },
        )
        response = conn.getresponse()
        response.read()
        return response.status
    finally:
        conn.close()


def processes(marker: str) -> list[str]:
    """Running processes whose command line (POSIX) or image name (Windows) contains marker."""
    if os.name == "nt":
        out = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, check=False
        ).stdout
        return [line for line in out.splitlines() if marker.lower() in line.lower()]
    out = subprocess.run(
        ["ps", "-A", "-o", "pid=,command="], capture_output=True, text=True, check=False
    ).stdout
    return [line.strip() for line in out.splitlines() if marker in line and "smoke_app.py" not in line]


def window_of(pid: int) -> tuple[int, int, int, str] | None:
    """(window id, width, height, owner) of pid's on-screen window: macOS, Quartz (a pywebview dependency).
    Window ids and bounds need no Screen Recording permission; capturing the pixels does."""
    try:
        import Quartz
    except ImportError:
        return None
    windows = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
        Quartz.kCGNullWindowID,
    )
    for w in windows or []:
        bounds = w.get("kCGWindowBounds", {})
        if (
            w.get("kCGWindowOwnerPID") == pid
            and w.get("kCGWindowLayer") == 0
            and bounds.get("Width", 0) > 200
        ):
            return (
                int(w["kCGWindowNumber"]),
                int(bounds["Width"]),
                int(bounds["Height"]),
                w.get("kCGWindowOwnerName"),
            )
    return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--screenshot", type=Path, help="macOS: save a capture of the window here")
    parser.add_argument("--network", action="store_true", help="self-check: also test HTTPS to the model")
    parser.add_argument("--timeout", type=float, default=60.0, help="seconds to wait for the server")
    parser.add_argument("app", nargs=argparse.REMAINDER, help="-- then the command that opens the window")
    args = parser.parse_args()
    args.app = args.app[1:] if args.app[:1] == ["--"] else args.app
    if not args.app:
        parser.error("give the app command after --")

    home = Path(tempfile.mkdtemp(prefix="email-town-smoke-"))
    root = Paths.default(home).data_dir / "workbench"
    env = {**os.environ, "EMAIL_TOWN_LABELER_HOME": str(home), "EMAIL_TOWN_LABELER_NO_UPDATE_CHECK": "1"}
    log = home / "smoke.log"
    binary = args.app[0]
    frozen = Path(binary).is_file() and "python" not in Path(binary).name.lower()
    # What identifies the app's processes: the bundle binary, or the module when run from source.
    marker = (Path(binary).name if os.name == "nt" else binary) if frozen else args.app[-1]
    print(f"home: {home}\napp:  {' '.join(args.app)}", flush=True)
    passed = True

    if frozen:
        report = home / "self-check.txt"
        cmd = [binary, "--self-check", "--output", str(report)] + (["--network"] if args.network else [])
        code = subprocess.run(cmd, env=env, capture_output=True, timeout=180, check=False).returncode
        print(report.read_text() if report.exists() else "(no self-check report)", end="", flush=True)
        passed &= step(code == 0, f"self-check exit code {code}")

    started = time.monotonic()
    first = launch(args.app, env, log)
    served = wait_for(lambda: proof(root), args.timeout)
    passed &= step(bool(served), f"server answers /api/proof ({served}, {time.monotonic() - started:.1f}s)")
    active = running(root) if served else None
    if active:
        passed &= step(page(active["port"]) == 200, "GET / returns the workbench page")
        owner = json.loads((root / "launch.json").read_text())["pid"]
        passed &= step(owner == first.pid, f"launch.json names the app process ({owner})")

        second = launch(args.app, env, log)
        time.sleep(4)
        same = json.loads((root / "launch.json").read_text())["pid"] == owner
        passed &= step(second.poll() is None and same, "a second start reuses the running server")
        second.terminate()
        second.wait(timeout=10)

        if sys.platform == "darwin":
            window = wait_for(lambda: window_of(first.pid), 10)
            passed &= step(
                window is not None, f"the app has an on-screen window (id, w, h, owner = {window})"
            )
            if window and args.screenshot:  # reported, not gated: it depends on a privacy permission
                time.sleep(2)  # let the page paint
                args.screenshot.parent.mkdir(parents=True, exist_ok=True)
                shot = subprocess.run(
                    ["screencapture", "-x", "-o", f"-l{window[0]}", str(args.screenshot)], check=False
                )
                ok = shot.returncode == 0 and args.screenshot.is_file()
                print(
                    f"{'ok  ' if ok else 'skip'}  window capture to {args.screenshot}"
                    + ("" if ok else " (refused: grant Screen Recording to this terminal to capture)")
                )

        children = (
            subprocess.run(
                ["pgrep", "-P", str(first.pid)], capture_output=True, text=True, check=False
            ).stdout.split()
            if os.name != "nt"
            else []
        )
        status = shutdown(active)
        passed &= step(status == 200, f"authenticated shutdown accepted ({status})")
    try:
        code = first.wait(timeout=30)
    except subprocess.TimeoutExpired:
        first.kill()
        code = first.wait()
        passed &= step(False, "the app did not exit within 30 s of shutdown (killed)")
    else:
        passed &= step(code == 0, f"the app exited with code {code}")
    if active:
        gone = wait_for(lambda: not any(running_pid(p) for p in children), 10)
        passed &= step(
            gone is not None or not children, f"its child processes are gone ({children or 'none'})"
        )
    left = wait_for(lambda: not processes(marker), 10) is not None
    passed &= step(left, "no process of the app is left" if left else f"left running: {processes(marker)}")
    if not passed and log.exists():
        print("--- app output ---\n" + log.read_text(errors="replace")[-4000:])
    wb_log = root / "desktop.log"
    if not passed and wb_log.exists():
        print("--- desktop.log ---\n" + wb_log.read_text(errors="replace")[-4000:])
    print("PASS" if passed else "FAIL", flush=True)
    return 0 if passed else 1


def running_pid(pid: str) -> bool:
    if os.name == "nt":  # os.kill(pid, 0) would terminate it on Windows; no child list is taken there
        return False
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError):
        return False
    return True


if __name__ == "__main__":
    sys.exit(main())
