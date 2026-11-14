"""First run: someone with no mailbox connected can tell what to do, and is not offered dead ends.

Run: uv run --no-project --with-editable '.[dev]' --with playwright python scripts/check_first_run_ui.py

- the list says what Email Town is and has one primary button, Add an email account;
- nothing that needs a mailbox shows: sync status, refresh, Compose;
- the search ✕ appears only when there is a search or filter to clear;
- Add account opens on the route that works in this copy: with no Google sign-in client, the email-address
  form (app passwords cover Gmail too), with the address field focused; no "Account settings" eyebrow.
WebKit (the macOS app's engine) and Chromium; synthetic data only.
"""

from __future__ import annotations

import sys
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_shortcuts_ui import FixtureReader

from email_town_labeler.config import Paths
from email_town_labeler.desktop.manager import Workbench
from email_town_labeler.desktop.server import LocalServer


def main() -> int:
    out = Path(tempfile.mkdtemp(prefix="email-town-first-run-ui-"))
    with sync_playwright() as playwright:
        for engine in ("webkit", "chromium"):
            root = out / engine
            paths = Paths.default(root / "home").ensure()
            paths.config_file.write_text('[model]\nref = "rules"\n')  # offline
            manager = Workbench(root / "workbench", paths, reader_factory=FixtureReader)
            server = LocalServer(manager)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            browser = getattr(playwright, engine).launch()
            try:
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                errors = []
                page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
                page.goto(server.launch_url)

                # ─── The empty list explains itself and offers the one next step ───
                add = page.locator("#emptyAddAccount")
                expect(add).to_be_visible()
                expect(add).to_have_text("Add an email account")
                expect(page.locator(".empty-state h3")).to_have_text("Welcome to Email Town")
                expect(page.locator("#previewEmptyTitle")).to_have_text("Your mail will show here")

                # ─── Nothing that needs a mailbox ───
                for control in ("#syncIndicator", "#refreshButton", "#composeButton"):
                    expect(page.locator(control)).to_be_hidden()

                # ─── ✕ only with something to clear ───
                clear = page.locator("#clearFiltersButton")
                expect(clear).to_be_hidden()
                page.locator("#searchInput").fill("anything")
                expect(clear).to_be_visible()
                clear.click()
                expect(clear).to_be_hidden()
                expect(page.locator("#searchInput")).to_have_value("")

                # ─── Add account starts where this copy can connect ───
                add.click()
                dialog = page.locator("#accountDialog")
                expect(dialog).to_be_visible()
                expect(dialog.locator(".eyebrow")).to_have_count(0)
                expect(page.locator("#providerSelect")).to_have_value("imap")
                expect(page.locator("#imapEmail")).to_be_focused()
                page.screenshot(path=str(out / f"{engine}-add-account.png"))
                page.keyboard.press("Escape")

                # ─── With a mailbox, the mail controls come back ───
                manager.accounts.add(
                    "Fixture", "imap", {"host": "fixture", "username": "fixture@example.test", "port": 993}
                )
                manager._refresh()
                page.reload()
                expect(page.locator(".message-row").first).to_be_visible()
                for control in ("#syncIndicator", "#refreshButton", "#composeButton"):
                    expect(page.locator(control)).to_be_visible()
                expect(page.locator("#emptyAddAccount")).to_have_count(0)
                expect(page.locator("#previewEmptyTitle")).to_have_text("Select a message")
                assert not errors, errors
                page.screenshot(path=str(out / f"{engine}-with-account.png"))
            finally:
                browser.close()
                server.shutdown()
                server.server_close()
                manager.close()
    print(
        f"first-run UI passed: welcome + Add account, no mail controls, ✕ on demand, email-first dialog. Captures: {out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
