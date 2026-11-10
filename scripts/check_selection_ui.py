"""Selecting messages as in Gmail and desktop mail clients, against the real local server (synthetic mail only).

Run: uv run --with playwright python scripts/check_selection_ui.py

Shift-click ranges (rows and checkboxes, select and deselect), ⌘/Ctrl-click toggles, the select-by-state menu,
Shift+j/k, "* a"/"* n", Esc, the selection surviving read/star actions (including one still in flight), and a
bulk action on a range landing in the fixture mailbox. WebKit (the macOS app's engine) and Chromium.
"""

from __future__ import annotations

import sys
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_shortcuts_ui import FixtureReader, until

from email_town_labeler.config import Paths
from email_town_labeler.desktop.manager import Workbench
from email_town_labeler.desktop.server import LocalServer


def main() -> int:
    out = Path(tempfile.mkdtemp(prefix="email-town-selection-ui-"))
    paths = Paths.default(out / "home").ensure()
    paths.config_file.write_text('[model]\nref = "rules"\n')  # offline: never the 263 MB default model
    readers = []

    def factory(account):
        reader = FixtureReader(account)
        reader.records += [
            {
                "id": f"m{i}",
                "subject": f"Fixture message {i}",
                "folder": "inbox",
                "labels": ["UNREAD"],
                "at": reader.records[0]["at"] - i * 60_000,
            }
            for i in range(5, 8)
        ]
        readers.append(reader)
        return reader

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
            for engine in ("webkit", "chromium"):
                for record in mailbox.records:
                    record.update(folder="inbox", labels=["UNREAD"])
                manager.folder = "inbox"
                manager.messages.clear()
                manager.read_overrides.clear()
                manager._refresh()
                browser = getattr(playwright, engine).launch()
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                errors = []
                page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
                until.page = page
                page.goto(server.launch_url)
                rows = page.locator(".message-row")
                expect(rows).to_have_count(7)
                selected = page.locator(".message-row.is-selected")
                subject = page.locator("#previewSubject")

                # Shift-click rows: a range from the opened message, without opening another one.
                rows.nth(0).locator(".row-open").click()
                expect(subject).to_have_text("Fixture message 1")
                rows.nth(2).click(modifiers=["Shift"])
                expect(selected).to_have_count(3)
                expect(subject).to_have_text("Fixture message 1")

                # Shift-click checkboxes: select a range, then deselect part of it.
                page.keyboard.press("Escape")
                expect(selected).to_have_count(0)
                rows.nth(5).locator(".row-check").click()
                rows.nth(3).locator(".row-check").click(modifiers=["Shift"])
                expect(selected).to_have_count(3)  # 4, 5, 6
                rows.nth(4).locator(".row-check").click(modifiers=["Shift"])  # 4..5 off: 5 was selected
                expect(selected).to_have_count(1)
                assert rows.nth(5).get_attribute("class").count("is-selected") == 1
                assert rows.nth(5).locator(".row-check").is_checked()

                # ⌘/Ctrl-click toggles one row and does not open it.
                rows.nth(6).click(modifiers=["ControlOrMeta"])
                expect(selected).to_have_count(2)
                rows.nth(6).click(modifiers=["ControlOrMeta"])
                expect(selected).to_have_count(1)
                expect(subject).to_have_text("Fixture message 1")

                # Select by state: Message 1 was opened, so it is read; the other six are unread.
                page.locator("#selectMenu > summary").click()
                expect(page.locator("[data-select='unread']")).to_be_visible()
                page.screenshot(path=str(out / f"{engine}-menu.png"))
                trigger = page.locator("#selectMenu > summary").bounding_box()
                panel = page.locator("#selectMenu .menu-panel").bounding_box()
                assert abs(panel["x"] - trigger["x"]) <= 1, (
                    "menu opens from its trigger, toward the list",
                    panel,
                    trigger,
                )
                page.locator("[data-select='unread']").click()
                expect(selected).to_have_count(6)
                page.locator("#selectMenu > summary").click()
                page.locator("[data-select='read']").click()
                expect(selected).to_have_count(1)
                page.locator("#selectMenu > summary").click()
                page.locator("[data-select='none']").click()
                expect(selected).to_have_count(0)

                # Keyboard: Shift+j extends from the open message, * a / * n, Esc.
                page.locator("body").click(position={"x": 5, "y": 5})
                page.keyboard.press("Shift+J")
                page.keyboard.press("Shift+J")
                expect(selected).to_have_count(3)
                expect(subject).to_have_text("Fixture message 3")
                page.keyboard.press("*")
                page.keyboard.press("a")
                expect(selected).to_have_count(7)
                page.keyboard.press("*")
                page.keyboard.press("n")
                expect(selected).to_have_count(0)

                # The selection outlives actions that leave messages in place, as in Gmail, even one still
                # running when the selection was made: star the open message, Shift+J twice while that
                # write is in flight, and all three stay selected afterwards, rows and count alike.
                count = page.locator("#mailSelectionCount")
                # In flight = still in the background outbox (the list has already been updated).
                busy = lambda page=page: page.evaluate("window.EmailTownMailHost.state.outbox.length > 0")
                idle = lambda page=page: not page.evaluate("window.EmailTownMailHost.state.outbox.length > 0")
                rows.nth(0).locator(".row-open").click()
                expect(subject).to_have_text("Fixture message 1")
                mailbox.delay = 1.0
                page.keyboard.press("s")
                until(busy, "the star is in flight")
                page.keyboard.press("Shift+J")
                page.keyboard.press("Shift+J")
                assert busy(), "the star must still be in flight, or this proves nothing"
                until(idle, "the action finishes")
                mailbox.delay = 0.0
                assert "STARRED" in mailbox.record("m1")["labels"]
                expect(selected).to_have_count(3)
                expect(count).to_have_text("3 selected · loaded messages only")
                # Opening m1..m3 read them; a bulk Mark unread (in the More menu) keeps the selection too.
                page.locator("#bulkMore > summary").click()
                page.locator("#bulkMore [data-mail-action='unread']").click()
                until(
                    lambda: all("UNREAD" in mailbox.record(f"m{i}")["labels"] for i in (1, 2, 3)),
                    "bulk unread",
                )
                until(idle, "the action finishes")
                expect(selected).to_have_count(3)
                expect(count).to_have_text("3 selected · loaded messages only")
                page.keyboard.press("*")
                page.keyboard.press("n")
                expect(selected).to_have_count(0)

                # A bulk action applies to exactly the range, and the rows leave at once: the provider
                # is slow (1 s a message) and the list must not wait for it (the outbox does).
                rows.nth(1).locator(".row-check").click()
                rows.nth(3).locator(".row-check").click(modifiers=["Shift"])
                expect(selected).to_have_count(3)
                mailbox.delay = 1.0
                page.keyboard.press("e")
                expect(rows).to_have_count(4, timeout=500)
                archived = sum(r["folder"] == "archive" for r in mailbox.records)
                assert archived < 3, (
                    "the mailbox must still be working, or the instant removal proves nothing"
                )
                until(busy, "the outbox shows the archive")
                expect(page.locator("#footerStatus")).to_contain_text("Archiving")
                mailbox.delay = 0.0
                until(idle, "the outbox drains")
                expect(page.locator("#footerStatus")).to_have_text("Classified on your device")
                until(
                    lambda: (
                        sorted(r["id"] for r in mailbox.records if r["folder"] == "archive")
                        == ["m2", "m3", "m4"]
                    ),
                    "e archives the range",
                )
                expect(rows).to_have_count(4)
                assert not errors, errors
                page.screenshot(path=str(out / f"{engine}-after.png"))
                browser.close()
                print(f"{engine}: selection pass")
    finally:
        server.shutdown()
        server.server_close()
        manager.close()
    print(f"selection UI passed. Captures: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
