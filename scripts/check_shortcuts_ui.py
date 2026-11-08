"""Keyboard shortcuts against the real local server and a synthetic mailbox (no real mail).

Run: uv run --with playwright python scripts/check_shortcuts_ui.py

Each shortcut is checked where its effect lands: the fixture mailbox (archive, trash, star, read,
sent mail) or the workbench state, not only the page. Also checked: single keys do nothing while
typing in a field, while an input method is composing, or under an open dialog.
"""

from __future__ import annotations

import re
import tempfile
import threading
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from email_town_labeler.config import Paths
from email_town_labeler.desktop.manager import Workbench
from email_town_labeler.desktop.server import LocalServer

NOW = int(time.time() * 1000)


class FixtureReader:
    def __init__(self, account):
        self.address = "fixture@example.test"
        self.records = [
            {
                "id": f"m{i}",
                "subject": f"Fixture message {i}",
                "folder": "inbox",
                "labels": ["UNREAD"],
                "at": NOW - i * 60_000,
            }
            for i in range(1, 5)
        ]
        self.sent = []

    def capabilities(self):
        return {
            "send": True,
            "write": True,
            "attachments": True,
            "max_attachment_bytes": 1 << 20,
            "reason": "",
        }

    def list(self, limit, folder="inbox"):
        rows = [r for r in self.records if r["folder"] == folder]
        return [
            {
                "provider_id": r["id"],
                "headers": {"subject": r["subject"], "from": "Sender <sender@example.test>"},
                "internal_date": r["at"],
                "category": "updates",
                "label_ids": list(r["labels"]),
            }
            for r in rows[:limit]
        ], False

    def detail(self, provider_id):
        return {
            "body_text": "Synthetic body.",
            "headers": {
                "from": "Sender <sender@example.test>",
                "to": "fixture@example.test",
                "message-id": "<f@x>",
            },
            "attachments": [],
            "thread_id": "t",
        }

    def record(self, provider_id):
        return next(r for r in self.records if r["id"] == provider_id)

    def mutate(self, provider_id, action):
        r = self.record(provider_id)
        if action in ("archive", "trash"):
            r["folder"] = action
        elif action == "restore":
            r["folder"] = "inbox"
        elif action in ("read", "unread"):
            r["labels"] = [x for x in r["labels"] if x != "UNREAD"] + (
                ["UNREAD"] if action == "unread" else []
            )
        elif action in ("star", "unstar"):
            r["labels"] = [x for x in r["labels"] if x != "STARRED"] + (
                ["STARRED"] if action == "star" else []
            )

    def send(self, data, original=None):
        self.sent.append(data)
        return {
            "status": "complete",
            "accepted": ["to@example.test"],
            "refused": [],
            "message": "Fixture accepted",
        }


def until(check, what, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for: {what}")


def main() -> int:
    out = Path(tempfile.mkdtemp(prefix="email-town-shortcuts-ui-"))
    paths = Paths.default(out / "home").ensure()
    # Offline and deterministic: keyword rules, never the desktop default model's 263 MB download.
    paths.config_file.write_text('[model]\nref = "rules"\n')
    readers = []

    def factory(account):
        readers.append(FixtureReader(account))
        return readers[-1]

    manager = Workbench(out / "workbench", paths, reader_factory=factory)
    manager.accounts.add(
        "Fixture", "imap", {"host": "fixture", "username": "fixture@example.test", "port": 993}
    )
    manager._refresh()
    server = LocalServer(manager)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    mailbox = readers[-1]
    try:
        with sync_playwright() as playwright:
            for engine in ("webkit", "chromium"):  # WebKit is the macOS app's engine (WKWebView)
                browser = getattr(playwright, engine).launch()
                for theme in ("light", "dark"):
                    for (
                        record
                    ) in mailbox.records:  # every pass starts from the same four unread Inbox messages
                        record.update(folder="inbox", labels=["UNREAD"])
                    manager.folder = "inbox"
                    manager.messages.clear()
                    manager._refresh()
                    context = browser.new_context(viewport={"width": 1440, "height": 900}, color_scheme=theme)
                    page = context.new_page()
                    errors = []
                    page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
                    confirms = []  # Trash keeps its confirmation when reached by a shortcut
                    page.on("dialog", lambda d, confirms=confirms: (confirms.append(d.message), d.accept()))
                    page.goto(server.launch_url)
                    expect(page.locator(".message-row")).to_have_count(4)
                    page.locator("body").click(position={"x": 5, "y": 5})
                    if theme == "dark":  # help dialog in both themes and at phone width; the rest once
                        page.keyboard.press("?")
                        expect(page.locator("#shortcutsDialog")).to_be_visible()
                        page.screenshot(path=str(out / f"{theme}-help.png"))
                        page.keyboard.press("Escape")
                        page.set_viewport_size({"width": 390, "height": 844})
                        page.locator("#shortcutsButton").click()
                        expect(page.locator("#shortcutsDialog")).to_be_visible()
                        width = page.evaluate("document.documentElement.scrollWidth")
                        assert width <= 390, f"help dialog overflows a phone screen: {width}px"
                        clipped = page.locator("#shortcutsList").evaluate(
                            "el => el.scrollWidth - el.clientWidth"
                        )
                        assert clipped <= 0, f"shortcut list is {clipped}px wider than the dialog"
                        page.screenshot(path=str(out / f"{theme}-help-390.png"))
                        page.keyboard.press("Escape")
                        break

                    # Navigate: j opens the newest, j the next, k back; arrows do the same.
                    subject = page.locator("#previewSubject")
                    page.keyboard.press("j")
                    expect(subject).to_have_text("Fixture message 1")
                    page.keyboard.press("j")
                    expect(subject).to_have_text("Fixture message 2")
                    page.keyboard.press("k")
                    expect(subject).to_have_text("Fixture message 1")
                    page.keyboard.press("ArrowDown")
                    expect(subject).to_have_text("Fixture message 2")

                    # Guards: an input method composing, a focused field and an open dialog ignore single keys.
                    page.evaluate(
                        "document.body.dispatchEvent(new KeyboardEvent('keydown', {key: 'j', isComposing: true, bubbles: true}))"
                    )
                    expect(subject).to_have_text("Fixture message 2")
                    # A focused row checkbox (tabbed to, or clicked in Chromium) is not a text field.
                    page.locator(".message-row .row-check").first.focus()
                    page.keyboard.press("j")
                    expect(subject).to_have_text("Fixture message 3")
                    page.keyboard.press("k")
                    expect(subject).to_have_text("Fixture message 2")
                    page.keyboard.press("/")
                    expect(page.locator("#searchInput")).to_be_focused()
                    page.keyboard.type("jk")
                    expect(page.locator("#searchInput")).to_have_value("jk")
                    expect(subject).to_have_text("Fixture message 2")
                    page.locator("#searchInput").fill("")
                    page.locator("body").click(position={"x": 5, "y": 5})
                    page.keyboard.press("?")
                    expect(page.locator("#shortcutsDialog")).to_be_visible()
                    expect(page.locator("#shortcutsList dl")).to_have_count(4)
                    page.screenshot(path=str(out / f"{theme}-help.png"))
                    page.keyboard.press("j")
                    expect(subject).to_have_text("Fixture message 2")  # the dialog swallowed it
                    page.keyboard.press("Escape")
                    expect(page.locator("#shortcutsDialog")).to_be_hidden()

                    # Act: star (from inside the closed More menu), read state, done, archive, trash.
                    page.keyboard.press("s")
                    until(lambda: "STARRED" in mailbox.record("m2")["labels"], "s stars the open message")
                    # Opening m2 already read it (open-to-read), so unread first, then read again.
                    until(lambda: "UNREAD" not in mailbox.record("m2")["labels"], "opening marks read")
                    page.keyboard.press("Shift+U")
                    until(lambda: "UNREAD" in mailbox.record("m2")["labels"], "Shift+U marks unread")
                    page.keyboard.press("Shift+I")
                    until(lambda: "UNREAD" not in mailbox.record("m2")["labels"], "Shift+I marks read")
                    before = manager.snapshot()["messages"][1]["done"]  # local state persists across passes
                    page.keyboard.press("d")
                    until(
                        lambda before=before: manager.snapshot()["messages"][1]["done"] is not before,
                        "d marks the open message done or reopens it",
                    )
                    page.keyboard.press("e")
                    until(lambda: mailbox.record("m2")["folder"] == "archive", "e archives the open message")
                    expect(page.locator(".message-row")).to_have_count(3)
                    page.keyboard.press("j")
                    page.keyboard.press("#")
                    until(
                        lambda: sum(r["folder"] == "trash" for r in mailbox.records) == 1, "# moves to Trash"
                    )
                    assert len(confirms) == 1 and confirms[0].startswith(
                        "Move 1 selected messages to Trash?"
                    ), confirms
                    expect(page.locator(".message-row")).to_have_count(2)

                    # Selection: x selects the open message, then e archives the selection.
                    page.keyboard.press("j")
                    chosen = subject.inner_text()
                    page.keyboard.press("x")
                    expect(page.locator(".message-row.is-selected")).to_have_count(1)
                    page.keyboard.press("j")  # open a different message: e must still act on the selection
                    opened = subject.inner_text()
                    assert opened != chosen, (opened, chosen)
                    page.keyboard.press("e")
                    until(
                        lambda chosen=chosen: (
                            next(r for r in mailbox.records if r["subject"] == chosen)["folder"] == "archive"
                        ),
                        "e archives the selection, not the open message",
                    )
                    assert next(r for r in mailbox.records if r["subject"] == opened)["folder"] == "inbox"

                    # Folders: g then a shows Archive, g then i returns to Inbox.
                    page.keyboard.press("g")
                    page.keyboard.press("a")
                    expect(page.locator(".message-row")).to_have_count(2)
                    page.keyboard.press("g")
                    page.keyboard.press("i")
                    expect(page.locator(".message-row")).to_have_count(1)

                    # Write: r replies to the open message; Ctrl/⌘+Enter sends; c opens a new message.
                    page.keyboard.press("j")
                    expect(page.locator("[data-compose-mode='reply']")).to_be_visible()
                    page.keyboard.press("r")
                    expect(page.locator("#composeDialog")).to_be_visible()
                    expect(page.locator("#composeSubject")).to_have_value(re.compile(r"^Re: "))
                    page.locator("#composeBody").fill("Synthetic reply")
                    page.keyboard.press("j")  # typing in the body: no navigation, the dialog stays
                    expect(page.locator("#composeDialog")).to_be_visible()
                    mod = "Meta" if page.evaluate("/Mac/.test(navigator.platform)") else "Control"
                    page.keyboard.press(f"{mod}+Enter")
                    until(
                        lambda: mailbox.sent and mailbox.sent[-1]["body"].startswith("Synthetic reply"),
                        "⌘/Ctrl+Enter sends",
                    )
                    expect(page.locator("#composeDialog")).to_be_hidden()
                    page.locator("body").click(position={"x": 5, "y": 5})
                    page.keyboard.press("c")
                    expect(page.locator("#composeDialog")).to_be_visible()
                    expect(page.locator("#composeSubject")).to_have_value("")
                    page.keyboard.press("Escape")
                    expect(page.locator("#composeDialog")).to_be_hidden()
                    page.keyboard.press(f"{mod}+n")
                    expect(page.locator("#composeDialog")).to_be_visible()
                    page.keyboard.press("Escape")
                    assert not errors, errors
                    context.close()
                browser.close()
                print(f"{engine}: shortcuts pass")
    finally:
        server.shutdown()
        server.server_close()
        manager.close()
    print(f"shortcuts UI passed: navigation, guards, actions, selection, folders, writing. Captures: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
