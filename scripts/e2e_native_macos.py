"""End-to-end check of the INSTALLED macOS app: real keys into its native window, read back through
the Accessibility API, cross-checked against the app's own state API. Uses the real data folder and
accounts, so it avoids mailbox side effects: it opens only Sent mail (already read, so open-to-read
writes nothing) and its one write is a star toggled on and back off. The Sent steps run only after the
app's own state says Sent is open, so a lost keystroke can never act on the Inbox; the search text is
digits, which are no shortcut, for the same reason.

Needs: the app running (open -a "/Applications/Email Town.app"), Accessibility permission for this
terminal, and `pip install pyobjc-framework-ApplicationServices`. Screen Recording is not needed.

    python scripts/e2e_native_macos.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import ApplicationServices as AX

ROOT = Path.home() / "Library/Application Support/email-town-labeler/workbench"
results: list[tuple[bool, str]] = []


def step(ok: bool, what: str) -> bool:
    results.append((bool(ok), what))
    print(("ok    " if ok else "FAIL  ") + what, flush=True)
    return bool(ok)


# ─── The app's state API (the same authenticated loopback API the window uses) ───


class Api:
    def __init__(self):
        data = json.loads((ROOT / "launch.json").read_text())
        url = urlsplit(data["url"])
        self.pid, self.origin = data["pid"], f"http://127.0.0.1:{url.port}"
        token = parse_qs(url.fragment)["token"][0]
        self.session = self.call("/api/session", {"token": token})["session"]

    def call(self, path, body=None):
        headers = {"X-Email-Town": "local", "Origin": self.origin}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if getattr(self, "session", None):
            headers["Authorization"] = "Bearer " + self.session
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.origin + path, data=data, headers=headers)
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read())

    def state(self):
        return self.call("/api/state")


# ─── The native window, through Accessibility ───


def attr(element, name):
    err, value = AX.AXUIElementCopyAttributeValue(element, name, None)
    return value if err == 0 else None


def window_of(pid):
    """The app's main window. Accessibility lists only windows on the current Space, so a window left on
    another desktop reads as missing until the app is activated (activate() below)."""
    app = AX.AXUIElementCreateApplication(pid)
    # When the app is not frontmost, AXMainWindow can hand back the application element itself, whose
    # children include itself: only a real window counts. A minimized one reports the subrole AXDialog.
    for window in [attr(app, "AXMainWindow"), *(attr(app, "AXWindows") or [])]:
        if window is not None and attr(window, "AXRole") == "AXWindow":
            return window
    return None


def activate(pid, timeout=10.0):
    """Bring Email Town to the front (switching to its Space) and wait for its window."""
    subprocess.run(["osascript", "-e", 'tell application "Email Town" to activate'], check=True)
    return until(lambda: window_of(pid) is not None, timeout)


def texts(element, limit=20000):  # the sidebar comes after 100 message rows in the walk
    """Visible strings in the window: static texts, headings, buttons, field values (breadth first)."""
    found, queue, seen = [], [element], 0
    while queue and seen < limit:
        node = queue.pop(0)
        seen += 1
        for name in ("AXValue", "AXTitle", "AXDescription"):
            value = attr(node, name)
            if isinstance(value, str) and value.strip():
                found.append(value.strip())
        queue.extend(attr(node, "AXChildren") or [])
    return found


def size_of(element):
    ok, size = AX.AXValueGetValue(attr(element, "AXSize"), AX.kAXValueCGSizeType, None)
    return (size.width, size.height) if ok else None


def page_overflow(pid):
    """How much taller the web document is than the window's web view (0 when only the panes scroll)."""
    queue, seen = [window_of(pid)], 0
    while queue and seen < 200:  # the web view sits a few levels down; never walk into the page
        node = queue.pop(0)
        seen += 1
        if node is None:
            continue
        if attr(node, "AXRole") == "AXScrollArea":
            for child in attr(node, "AXChildren") or []:
                if attr(child, "AXRole") == "AXWebArea":
                    return size_of(child)[1] - size_of(node)[1]
        queue.extend(attr(node, "AXChildren") or [])
    return None


def shows(pid, needle, timeout=8.0, present=True):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        window = window_of(pid)
        hit = window is not None and any(needle in t for t in texts(window))
        if hit == present:
            return True
        time.sleep(0.3)
    return False


def keys(script):
    """Send keystrokes to Email Town through System Events (the app is brought to the front first)."""
    subprocess.run(
        [
            "osascript",
            "-e",
            'tell application "Email Town" to activate',
            "-e",
            "delay 0.3",
            "-e",
            f'tell application "System Events" to {script}',
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    time.sleep(0.4)


def until(check, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.3)
    return False


def main() -> int:
    if not AX.AXIsProcessTrusted():
        print("This terminal needs Accessibility permission (System Settings > Privacy & Security).")
        return 2
    api = Api()
    pid = api.pid
    plist = Path("/Applications/Email Town.app/Contents/Info.plist").read_bytes()
    import plistlib

    version = plistlib.loads(plist)["CFBundleShortVersionString"]
    step(True, f"installed app version {version}, serving pid {pid}")
    activate(pid)
    window = window_of(pid)
    title = attr(window, "AXTitle") if window is not None else None
    step(title == "Email Town", f"native window 'Email Town' is open (title: {title!r})")
    state = api.state()
    account = state["accounts"][0]
    step(
        account["status"] == "ready" and state["messages"],
        f"account ready with {len(state['messages'])} messages",
    )

    step(shows(pid, "All categories: "), "the sidebar shows per-category loaded / done / wrong counts")
    overflow = page_overflow(pid)
    step(overflow is not None and overflow <= 1, f"the page fits the window; only panes scroll ({overflow})")

    # Keyboard shortcuts inside the native window. The help dialog is harmless, so it doubles as the
    # readiness gate: nothing else is pressed until the window demonstrably takes keys.
    keys("key code 53")  # Esc: start from a clean state
    ready = False
    for _attempt in range(5):
        keys('keystroke "?"')
        if shows(pid, "Next (older) message", timeout=2):
            ready = True
            break
    if not step(ready, "? opens the shortcuts help in the window"):
        print("The window does not take keys; stopping before any shortcut that could act on mail.")
        return 1
    keys("key code 53")
    step(shows(pid, "Show these shortcuts", present=False), "Esc closes it")

    keys('keystroke "/"')
    keys('keystroke "73195-00042"')  # digits: no shortcut, should "/" ever be lost
    if step(shows(pid, "No messages match these filters"), "/ focuses search; typing filters the list"):
        # Delete is also the Trash shortcut: pressed only while the search field demonstrably has the keys.
        keys('keystroke "a" using command down')
        keys("key code 51")  # Delete: clears the selected search text (⌘A from the native Edit menu)
        cleared = shows(pid, "No messages match these filters", present=False)
        step(cleared, "⌘A + Delete clears the search field")
        keys("key code 48")  # Tab out of the field, so single keys are shortcuts again
        keys("key code 48")

    keys('keystroke "g"')
    keys('keystroke "s"')
    in_sent = step(until(lambda: api.state()["folder"] == "sent", 20), "g then s opens Sent")
    sent = sorted(api.state()["messages"], key=lambda m: m["date"], reverse=True) if in_sent else []
    # Only in Sent: opening Inbox mail would mark it read.
    if step(in_sent and bool(sent), f"Sent has {len(sent)} loaded messages"):
        first = sent[0]
        keys('keystroke "j"')
        step(shows(pid, first["subject"][:40]), "j opens the newest sent message in the reading pane")
        step(shows(pid, f"1 of {len(sent)}"), "the reading pane shows its place in the list (1 of N)")
        before = first["starred"]
        keys('keystroke "s"')
        flipped = until(
            lambda: (
                next(m for m in api.state()["messages"] if m["id"] == first["id"])["starred"] is not before
            )
        )
        step(flipped, "s toggles its star in the mailbox")
        keys('keystroke "s"')
        restored = until(
            lambda: next(m for m in api.state()["messages"] if m["id"] == first["id"])["starred"] is before
        )
        step(restored, "s again restores it (net change: none)")
        keys('keystroke "J"')
        keys('keystroke "J"')
        step(shows(pid, "3 selected"), "Shift+J twice selects three messages")
        keys('keystroke "*"')
        keys('keystroke "n"')
        step(shows(pid, "3 selected", present=False), "* then n clears the selection")

    keys('keystroke "g"')
    keys('keystroke "i"')
    step(until(lambda: api.state()["folder"] == "inbox"), "g then i returns to Inbox")

    keys('keystroke "c"')
    step(shows(pid, "Compose message"), "c opens a new message")
    keys("key code 53")
    step(shows(pid, "Compose message", present=False), "Esc closes it without sending")

    keys('keystroke "m" using command down')
    step(
        until(lambda: attr(window_of(pid), "AXMinimized") is True, 5), "⌘M minimizes the window (Window menu)"
    )
    AX.AXUIElementSetAttributeValue(window_of(pid), "AXMinimized", False)
    step(until(lambda: attr(window_of(pid), "AXMinimized") is False, 5), "the window comes back")

    keys('keystroke "q" using command down')
    gone = until(
        lambda: subprocess.run(["kill", "-0", str(pid)], check=False, capture_output=True).returncode != 0, 15
    )
    step(gone, "⌘Q quits: the app process ends")
    subprocess.run(["open", "-a", "/Applications/Email Town.app"], check=True)
    time.sleep(8)
    again = Api()
    step(activate(again.pid), "the reopened app shows its window")
    step(
        again.pid != pid
        and again.state()["accounts"][0]["status"] in ("ready", "syncing", "classifying", "waiting"),
        "reopening starts a new instance that serves the same account",
    )

    failed = [what for ok, what in results if not ok]
    print(
        f"\n{len(results) - len(failed)}/{len(results)} checks passed"
        + (f"; failed: {failed}" if failed else "")
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
