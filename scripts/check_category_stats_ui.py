"""Scoped category counts: unread (shown), automatic classifications and user corrections (named).

Run: uv run --with playwright python scripts/check_category_stats_ui.py
Checks immediate browser bookkeeping with long-poll responses held, then the persisted server snapshot
after reload. Counts describe facts, never classification accuracy. Synthetic mail; WebKit and Chromium.
"""

from __future__ import annotations

import re
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


def workbench(root: Path) -> Workbench:
    paths = Paths.default(root / "home").ensure()
    paths.config_file.write_text('[model]\nref = "rules"\n')
    manager = Workbench(root / "workbench", paths, reader_factory=FixtureReader)
    manager.accounts.add(
        "Fixture", "imap", {"host": "fixture", "username": "fixture@example.test", "port": 993}
    )
    manager._refresh()
    return manager


def main() -> int:
    out = Path(tempfile.mkdtemp(prefix="email-town-category-stats-ui-"))
    with sync_playwright() as playwright:
        for engine in ("webkit", "chromium"):
            manager = workbench(out / engine)
            server = LocalServer(manager)
            try:
                threading.Thread(target=server.serve_forever, daemon=True).start()
                browser = getattr(playwright, engine).launch()
                page = browser.new_page(viewport={"width": 1280, "height": 840})
                until.page = page
                errors, held = [], []
                page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))

                def hold(route):
                    held.append(route)  # noqa: B023 - used within this iteration only

                page.route(lambda url: "/api/state?wait=" in url, hold)
                page.goto(server.launch_url)
                expect(page.locator(".message-row")).to_have_count(4)

                def item(category, page=page):
                    return page.locator(f"#categoryFilters [data-category='{category}']")

                # The entry shows one number, its unread mail; the rest is in its accessible name.
                def shows(category, count, *, unread=0, automatic=0, corrected=0, page=page):
                    expect(item(category).locator(".nav-count")).to_have_text(str(unread) if unread else "")
                    expect(item(category)).to_have_attribute(
                        "aria-label",
                        re.compile(
                            rf"\b{unread} unread of {count} loaded in current scope.*"
                            rf"\b{automatic} sorted automatically, {corrected} corrected by you"
                        ),
                    )
                    assert "wrong" not in item(category).get_attribute("aria-label").lower()

                def open_row(subject, page=page):
                    page.locator(".message-row").filter(has_text=subject).locator(".row-open").click()
                    expect(page.locator("#previewSubject")).to_have_text(subject)

                def more(control, page=page):
                    page.locator("#previewMore > summary").click()
                    page.locator(control).click()

                def recategorize(category, page=page):
                    more("#previewCategoryButton")
                    page.locator("#categorySelect").select_option(category)
                    page.locator("#categoryForm [type=submit]").click()

                shows("updates", 4, unread=4, automatic=4)
                shows("orders", 0)

                open_row("Fixture message 1")
                page.locator('.preview-actions [data-single-action="archive"]').click()
                expect(page.locator(".message-row")).to_have_count(3)
                shows("updates", 3, unread=3, automatic=3)

                open_row("Fixture message 2")
                recategorize("orders")
                shows("updates", 2, unread=2, automatic=2, corrected=1)
                shows("orders", 1, unread=0, corrected=1)
                assert held, "no long-poll was held, so this did not isolate the page's own update"
                page.screenshot(path=str(out / f"{engine}-sidebar.png"))

                page.unroute_all(behavior="ignoreErrors")
                page.reload()
                expect(page.locator(".message-row")).to_have_count(3)
                shows("updates", 2, unread=2, automatic=2, corrected=1)
                shows("orders", 1, unread=0, corrected=1)

                open_row("Fixture message 2")
                recategorize("updates")
                shows("updates", 3, unread=2, automatic=2)
                shows("orders", 0)
                assert "wrong" not in page.locator("#categoryFilters").inner_text().lower()
                assert not errors, errors
                print(f"{engine}: scoped category counts pass")
                browser.close()
            finally:
                server.shutdown()
                server.server_close()
                manager.close()
    print(f"category counts UI passed. Captures: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
