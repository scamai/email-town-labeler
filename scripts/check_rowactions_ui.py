"""Gmail-style row controls and auto-advance, against the real local server (synthetic mail only).

Run: uv run --with playwright python scripts/check_rowactions_ui.py

Hover shows Archive / Trash / read-unread in place of the time; each lands in the fixture mailbox without
opening the row. The star toggles. Trashing the open message opens the next (older) one in a wide window
and returns to the list in a narrow one. Touch devices get no hover controls; a read-only account gets none.
WebKit (the macOS app's engine) and Chromium.
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


class ReadOnly(FixtureReader):
    def capabilities(self):
        return {**super().capabilities(), "write": False, "send": False, "reason": "read only"}


def main() -> int:
    out = Path(tempfile.mkdtemp(prefix="email-town-rowactions-ui-"))
    paths = Paths.default(out / "home").ensure()
    paths.config_file.write_text('[model]\nref = "rules"\n')  # offline: never the 263 MB default model
    readers = {}

    def factory(account):
        reader = (ReadOnly if account["name"] == "Read only" else FixtureReader)(account)
        if account["name"] == "Read only":
            reader.records = [
                dict(r, id="ro" + r["id"], subject="Read-only " + r["subject"]) for r in reader.records[:1]
            ]
        readers[account["name"]] = reader
        return reader

    manager = Workbench(out / "workbench", paths, reader_factory=factory)
    manager.accounts.add(
        "Fixture", "imap", {"host": "fixture", "username": "fixture@example.test", "port": 993}
    )
    manager.accounts.add("Read only", "imap", {"host": "ro", "username": "ro@example.test", "port": 993})
    manager._refresh()
    server = LocalServer(manager)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    mailbox = readers["Fixture"]

    def reset():
        for record in mailbox.records:
            record.update(folder="inbox", labels=["UNREAD"])
        manager.folder = "inbox"
        manager.messages.clear()
        manager.read_overrides.clear()
        manager._refresh()

    try:
        with sync_playwright() as playwright:
            for engine in ("webkit", "chromium"):
                reset()
                browser = getattr(playwright, engine).launch()
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                until.page = page
                errors, confirms = [], []
                page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
                page.on("dialog", lambda d, confirms=confirms: (confirms.append(d.message), d.accept()))
                page.goto(server.launch_url)
                row = lambda subject, page=page: page.locator(".message-row").filter(has_text=subject)
                expect(page.locator(".message-row")).to_have_count(5)
                subject = page.locator("#previewSubject")

                # A read-only account's rows carry no controls; writable rows do.
                expect(row("Read-only").locator(".row-star")).to_have_count(0)
                expect(row("Fixture message 2").locator(".row-star")).to_have_count(1)

                # Hover: the controls replace the time, and act without opening the row.
                target = row("Fixture message 2")
                expect(target.locator(".row-actions")).to_be_hidden()
                target.hover()
                expect(target.locator(".row-actions")).to_be_visible()
                expect(target.locator(".row-title time")).to_be_hidden()
                page.screenshot(path=str(out / f"{engine}-hover.png"))
                target.locator("[data-row-action='read']").click()
                until(lambda: "UNREAD" not in mailbox.record("m2")["labels"], "the hover read control")
                expect(page.locator("#previewContent")).to_be_hidden()  # nothing was opened
                target.hover()
                target.locator("[data-row-action='unread']").click()
                until(lambda: "UNREAD" in mailbox.record("m2")["labels"], "the hover unread control")

                # The star toggles both ways and shows its state.
                star = row("Fixture message 3").locator(".row-star")
                star.click()
                until(lambda: "STARRED" in mailbox.record("m3")["labels"], "the star stars")
                expect(row("Fixture message 3").locator(".row-star")).to_have_attribute(
                    "aria-pressed", "true"
                )
                row("Fixture message 3").locator(".row-star").click()
                until(lambda: "STARRED" not in mailbox.record("m3")["labels"], "the star unstars")

                # Hover Archive.
                row("Fixture message 4").hover()
                row("Fixture message 4").locator("[data-row-action='archive']").click()
                until(lambda: mailbox.record("m4")["folder"] == "archive", "the hover archive control")
                expect(page.locator(".message-row")).to_have_count(4)

                # Auto-advance (wide): trashing the open message opens the next, older one.
                row("Fixture message 2").locator(".row-open").click()
                expect(subject).to_have_text("Fixture message 2")
                row("Fixture message 2").hover()
                row("Fixture message 2").locator("[data-row-action='trash']").click()
                until(lambda: mailbox.record("m2")["folder"] == "trash", "the hover trash control")
                assert confirms and confirms[-1].startswith("Move 1 selected messages to Trash?"), confirms
                expect(subject).to_have_text("Fixture message 3")

                # Narrow: archiving the open message returns to the list instead.
                page.set_viewport_size({"width": 1000, "height": 900})  # the open preview now covers the list
                expect(subject).to_have_text("Fixture message 3")
                page.locator("[data-single-action='archive']").click()
                until(lambda: mailbox.record("m3")["folder"] == "archive", "archive from the narrow preview")
                expect(page.locator("#previewContent")).to_be_hidden()
                assert not errors, errors
                browser.close()

                # Touch: no hover controls, the star stays.
                browser = getattr(playwright, engine).launch()
                context = browser.new_context(
                    viewport={"width": 1440, "height": 900}, has_touch=True, is_mobile=engine == "chromium"
                )
                page = context.new_page()
                until.page = page
                page.goto(server.launch_url)
                first = (
                    page.locator(".message-row")
                    .filter(has_text="Fixture message 1")
                    .filter(has_not_text="Read-only")
                )
                expect(first.locator(".row-star")).to_be_visible()
                first.locator(".row-content").tap() if engine == "chromium" else first.locator(
                    ".row-content"
                ).click()
                hover_none = page.evaluate("matchMedia('(hover: none)').matches")
                assert hover_none, "the touch emulation must report hover:none, or this check proves nothing"
                expect(first.locator(".row-actions")).to_be_hidden()
                print(f"{engine}: row controls pass (touch device reports hover:none = {hover_none})")
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        manager.close()
    print(f"row controls UI passed. Captures: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
