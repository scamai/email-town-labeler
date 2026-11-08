"""Per-category counts in the sidebar: how many, how many done, how many the classifier got wrong.

Run: uv run --with playwright python scripts/check_category_stats_ui.py

Each category shows its loaded count with "N done · M wrong" under the name; "wrong" counts messages
that classification put in a category and the user then moved to another. Checked twice: right after
the action, with the server's state updates held back (the page's own bookkeeping), and after a reload
(the server's snapshot). Moving a message back to its original category clears the "wrong".
Real local server, synthetic mail only. WebKit (the macOS app's engine) and Chromium.
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


def workbench(root: Path) -> Workbench:
    """Four inbox messages, all labelled "updates" in the mailbox (an automatic category)."""
    paths = Paths.default(root / "home").ensure()
    paths.config_file.write_text('[model]\nref = "rules"\n')  # offline: never the 263 MB default model
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
            manager = workbench(out / engine)  # a fresh workflow per engine
            server = LocalServer(manager)
            try:
                threading.Thread(target=server.serve_forever, daemon=True).start()
                browser = getattr(playwright, engine).launch()
                page = browser.new_page(viewport={"width": 1280, "height": 840})
                until.page = page
                errors, held = [], []
                page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
                # Hold the long-poll's answers: what the sidebar shows next is the page's own update.
                long_poll = lambda url: "/api/state?wait=" in url

                def hold(route):  # never answered until released below (Playwright counts parameters)
                    held.append(route)  # noqa: B023 - used within this iteration only

                page.route(long_poll, hold)
                page.goto(server.launch_url)
                expect(page.locator(".message-row")).to_have_count(4)

                def item(category, page=page):
                    return page.locator(f"[data-category-filter='{category}']")

                def shows(category, count, stats):
                    expect(item(category).locator(".count")).to_have_text(str(count))
                    line = item(category).locator(".category-stats")
                    if stats is None:
                        expect(line).to_have_count(0)
                    else:
                        expect(line).to_have_text(stats)

                shows("all", 4, "0 done · 0 wrong")
                shows("updates", 4, "0 done")
                shows("orders", 0, None)

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

                # Done, then a correction: the sidebar follows at once, before any server update.
                open_row("Fixture message 1")
                more("#previewDoneButton")
                shows("updates", 4, "1 done")
                open_row("Fixture message 2")
                recategorize("orders")
                shows("updates", 3, "1 done · 1 wrong")
                expect(item("updates").locator(".category-wrong")).to_have_text(" · 1 wrong")
                shows("orders", 1, "0 done")
                shows("all", 4, "1 done · 1 wrong")
                assert item("updates").get_attribute("aria-label") == "Updates: 3 loaded, 1 done, 1 wrong"
                assert held, "no long-poll was held, so this did not isolate the page's own update"
                page.screenshot(path=str(out / f"{engine}-sidebar.png"))

                # The server's snapshot says the same after a reload.
                page.unroute_all(behavior="ignoreErrors")  # the held requests end with the reload
                page.reload()
                expect(page.locator(".message-row")).to_have_count(4)
                shows("updates", 3, "1 done · 1 wrong")
                shows("orders", 1, "0 done")
                shows("all", 4, "1 done · 1 wrong")

                # Moved back to where classification put it: no longer wrong.
                open_row("Fixture message 2")
                recategorize("updates")
                shows("updates", 4, "1 done")
                shows("all", 4, "1 done · 0 wrong")
                shows("orders", 0, None)
                assert not errors, errors
                print(f"{engine}: category counts pass")
                browser.close()
            finally:
                server.shutdown()
                server.server_close()
                manager.close()
    print(f"category counts UI passed. Captures: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
