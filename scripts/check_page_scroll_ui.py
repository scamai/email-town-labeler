"""The app page never scrolls as a whole; only its panes do. Real local server, synthetic mail only.

Run: uv run --with playwright python scripts/check_page_scroll_ui.py

With a long list (60 rows), the document must be exactly the window's height at desktop, mid and phone
widths, and a trackpad scroll over a part that cannot scroll itself (a short message, the footer) must
leave the page where it is. Before the fix, each row's screen-reader preview escaped the list and made
the document ~7,000 px tall, so one scroll over the reading pane slid the whole app up and left an
empty band below it. Two independent guards are checked separately, so removing either one fails:
rows contain their previews (the workspace does not overflow), and the app shell clips anything that
still escapes (an injected stray element does not lengthen the page). Menus, which are fixed-position,
must still open in full. WebKit (the macOS app's engine) and Chromium.
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

ROWS = 60
VIEWPORTS = [(1280, 812), (1000, 800), (390, 844)]  # the app's default window, a narrower one, a phone
PAGE = "[document.scrollingElement.scrollHeight, innerHeight, scrollY]"


def main() -> int:
    out = Path(tempfile.mkdtemp(prefix="email-town-page-scroll-ui-"))
    paths = Paths.default(out / "home").ensure()
    paths.config_file.write_text('[model]\nref = "rules"\n')  # offline: never the 263 MB default model

    def factory(account):
        reader = FixtureReader(account)
        first = reader.records[0]
        reader.records = [
            dict(first, id=f"m{i}", subject=f"Fixture message {i}", at=first["at"] - i * 60_000)
            for i in range(1, ROWS + 1)
        ]
        return reader

    manager = Workbench(out / "workbench", paths, reader_factory=factory)
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
                for width, height in VIEWPORTS:
                    page = browser.new_page(viewport={"width": width, "height": height})
                    until.page = page
                    errors = []
                    page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
                    page.goto(server.launch_url)
                    rows = page.locator(".message-row")
                    expect(rows).to_have_count(ROWS)
                    where = f"{engine} {width}x{height}"

                    def fits(what, page=page, height=height, where=where):
                        full, inner, scrolled = page.evaluate(PAGE)
                        assert (full, inner, scrolled) == (height, height, 0), (
                            f"{where}, {what}: the page is {full} px tall in a {inner} px window, "
                            f"scrolled {scrolled} px"
                        )

                    fits("loaded")
                    # The workspace holds the row previews: it may not overflow (the cause, on its own).
                    overflow = page.evaluate(
                        "(() => { const w = document.querySelector('.workspace');"
                        " return w.scrollHeight - w.clientHeight; })()"
                    )
                    assert overflow <= 1, f"{where}: the workspace overflows by {overflow} px"

                    # A trackpad scroll over parts that cannot scroll themselves leaves the page put.
                    rows.nth(0).locator(".row-open").click()
                    expect(page.locator("#previewSubject")).to_have_text("Fixture message 1")
                    body = page.locator("#previewBody").bounding_box()
                    page.mouse.move(body["x"] + 20, min(height - 80, body["y"] + body["height"] + 40))
                    page.mouse.wheel(0, 800)
                    page.wait_for_timeout(300)
                    fits("after a scroll over the reading pane")
                    footer = page.locator(".app-footer").bounding_box()
                    page.mouse.move(footer["x"] + footer["width"] / 2, footer["y"] + footer["height"] / 2)
                    page.mouse.wheel(0, 800)
                    page.wait_for_timeout(300)
                    fits("after a scroll over the footer")
                    page.screenshot(path=str(out / f"{engine}-{width}.png"))

                    # The shell clips whatever still escapes (the guard, on its own).
                    page.evaluate(
                        "(() => { const stray = document.createElement('div');"
                        " stray.id = 'strayCheck';"
                        " stray.style.cssText = 'position:absolute;top:5000px;left:0;width:10px;height:10px';"
                        " document.querySelector('.workspace').append(stray); })()"
                    )
                    fits("with a stray element 5,000 px down")
                    page.evaluate("document.getElementById('strayCheck').remove()")

                    # Fixed-position menus are not clipped by the shell.
                    if width >= 1200:
                        page.locator("#previewMore > summary").click()
                        panel = page.locator("#previewMore .menu-panel")
                        expect(panel).to_be_visible()
                        box = panel.bounding_box()
                        assert box["height"] > 120 and box["y"] + box["height"] <= height, box
                        page.keyboard.press("Escape")
                    assert not errors, errors
                    page.close()
                print(f"{engine}: the page never scrolls at {', '.join(f'{w}x{h}' for w, h in VIEWPORTS)}")
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        manager.close()
    print(f"page scroll UI passed. Captures: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
