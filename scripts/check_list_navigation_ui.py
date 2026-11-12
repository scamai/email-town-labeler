"""Regression: repeated list navigation keeps the live row focused and visible.

Run: uv run --with playwright python scripts/check_list_navigation_ui.py
Uses a synthetic mailbox and the real server in Chromium and WebKit.
"""

from __future__ import annotations

import tempfile
import threading
from pathlib import Path

from check_shortcuts_ui import NOW, FixtureReader
from playwright.sync_api import expect, sync_playwright

from email_town_labeler.config import Paths
from email_town_labeler.desktop.manager import Workbench
from email_town_labeler.desktop.server import LocalServer


class LongMailbox(FixtureReader):
    def __init__(self, account):
        super().__init__(account)
        self.records = [
            {
                "id": f"m{i}",
                "subject": f"Fixture message {i}",
                "folder": "inbox",
                "labels": [],
                "at": NOW - i * 60_000,
            }
            for i in range(1, 41)
        ]


def main() -> None:
    out = Path(tempfile.mkdtemp(prefix="email-town-navigation-"))
    paths = Paths.default(out / "home").ensure()
    paths.config_file.write_text('[model]\nref = "rules"\n')
    manager = Workbench(out / "workbench", paths, reader_factory=LongMailbox)
    manager.accounts.add(
        "Fixture", "imap", {"host": "fixture", "username": "fixture@example.test", "port": 993}
    )
    manager._refresh()
    server = LocalServer(manager)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as playwright:
            for engine in ("webkit", "chromium"):
                browser = getattr(playwright, engine).launch()
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                page.goto(server.launch_url)
                rows = page.locator(".message-row")
                expect(rows).to_have_count(40)
                rows.first.locator(".row-open").click()
                # Cross the redraw boundary: inspect actual row bounds and browser focus.
                for key, indices in (
                    ("ArrowDown", range(1, 40)),
                    ("ArrowDown", [39]),
                    ("ArrowUp", range(38, -1, -1)),
                    ("ArrowUp", [0]),
                    ("j", range(1, 22)),
                    ("k", range(20, -1, -1)),
                ):
                    for index in indices:
                        page.keyboard.press(key)
                        expect(page.locator("#previewSubject")).to_have_text(f"Fixture message {index + 1}")
                        row = rows.nth(index)
                        visible = row.evaluate("""el => {
                            const row = el.getBoundingClientRect();
                            const list = document.getElementById('messageList').getBoundingClientRect();
                            return row.top >= list.top - 1 && row.bottom <= list.bottom + 1;
                        }""")
                        assert visible, f"{engine}: {key} row {index + 1} outside list viewport"
                        expect(row.locator(".row-open")).to_be_focused()
                page.screenshot(path=str(out / f"{engine}.png"))
                browser.close()
                print(f"{engine}: repeated arrows/j/k, focus, scrolling and list boundaries pass")
    finally:
        server.shutdown()
        server.server_close()
        manager.close()


if __name__ == "__main__":
    main()
