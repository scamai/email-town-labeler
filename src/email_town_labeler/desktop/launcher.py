"""Start/reopen a private local workbench without requiring an open terminal."""

from __future__ import annotations

import hmac
import http.client
import json
import os
import secrets
import subprocess
import sys
import time
import webbrowser
from urllib.parse import parse_qs, urlsplit

from ..gmail.auth import _write_private
from ..lock import InstanceLock


def running(root):
    """Authenticate the saved launch capability; never follow a URL to another host."""
    try:
        data = json.loads((root / "launch.json").read_text())
        url = urlsplit(data["url"])
        if url.scheme != "http" or url.hostname != "127.0.0.1" or url.username or not url.port:
            return None
        token = parse_qs(url.fragment).get("token", [""])[0]
        if len(token) != 43 or url.path != "/":
            return None
        origin = f"http://127.0.0.1:{url.port}"
        conn = http.client.HTTPConnection("127.0.0.1", url.port, timeout=1)
        try:
            challenge = secrets.token_hex(32)
            conn.request("GET", "/api/proof?challenge=" + challenge)
            response = conn.getresponse()
            payload = json.loads(response.read(4096))
            expected = hmac.digest(token.encode(), ("EmailTown:" + challenge).encode(), "sha256").hex()
            proof = payload.get("proof", "")
            if (
                response.status != 200
                or not isinstance(proof, str)
                or not hmac.compare_digest(proof.encode(errors="replace"), expected.encode())
            ):
                return None
            conn.request(
                "POST",
                "/api/session",
                json.dumps({"token": token}),
                {"Content-Type": "application/json", "Origin": origin, "X-Email-Town": "local"},
            )
            response = conn.getresponse()
            payload = json.loads(response.read(4096))
            if response.status == 200 and isinstance(payload.get("session"), str):
                return {"url": data["url"], "origin": origin, "port": url.port, "session": payload["session"]}
        finally:
            conn.close()
    except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException):
        return None
    return None


def launch(ctx):
    root = ctx.paths.data_dir.absolute() / "workbench"
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)
    # Separate launch lock serializes simultaneous double-clicks; server owns its own lifetime lock.
    with InstanceLock(root / "launcher"):
        active = running(root)
        if ctx.args.stop:
            if active:
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
                    if response.status != 200:
                        raise ValueError("The workbench could not stop. Close the original terminal.")
                finally:
                    conn.close()
            ctx.out("The local workbench has stopped.")
            return 0
        if active is None:
            log = root / "desktop.log"
            if not log.exists():
                _write_private(log, "")
            args = [sys.executable, "-m", "email_town_labeler"]
            if ctx.args.home:
                args += ["--home", ctx.args.home]
            args += ["desktop", "--no-browser", "--port", str(ctx.args.port)]
            options = (
                {"start_new_session": True}
                if os.name != "nt"
                else {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
            )
            with log.open("ab") as output:
                child = subprocess.Popen(
                    args, stdin=subprocess.DEVNULL, stdout=output, stderr=output, **options
                )
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                active = running(root)
                if active:
                    break
                if child.poll() is not None:
                    raise ValueError(f"The workbench failed to start. Check the local log at {log}")
                time.sleep(0.1)
            if not active:
                raise ValueError(f"The workbench is not ready. Check the local log at {log}")
        ctx.out(f"Email Town local workbench: {active['origin']}")
        if not ctx.args.no_browser:
            webbrowser.open(active["url"])
    return 0
