"""The compact three-pane layout, against the real local server (synthetic mail only).

Run: uv run --no-project --with-editable '.[dev]' --with playwright python scripts/check_panes_ui.py

Checked where the user sees it, in WebKit (the macOS app's engine) and Chromium:
- the pane borders drag, keep the reading pane at least 360px wide, persist across a reload, take ←/→
  when focused, and reset on double-click;
- with one account there is no "All accounts" entry and no account chip on rows or the reading pane;
- rows are two fixed lines (sender and time; subject, category, star) with the sender's name only;
- a category entry filters, a chip appears in the list header, and the chip or a second click clears it;
- the Done entry shows completed mail and choosing a folder leaves it.
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


def width(page, selector):
    return page.locator(selector).evaluate("el => el.getBoundingClientRect().width")


def serve():
    """A fresh workbench per engine: the first engine's run reads and completes mail."""
    out = Path(tempfile.mkdtemp(prefix="email-town-panes-ui-"))
    paths = Paths.default(out / "home").ensure()
    paths.config_file.write_text('[model]\nref = "rules"\n')
    manager = Workbench(out / "workbench", paths, reader_factory=FixtureReader)
    manager.accounts.add(
        "Fixture", "imap", {"host": "fixture", "username": "fixture@example.test", "port": 993}
    )
    manager._refresh()
    server = LocalServer(manager)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return out, manager, server


def main() -> int:
    with sync_playwright() as playwright:
        for engine in ("webkit", "chromium"):
            out, manager, server = serve()
            try:
                check(playwright, engine, server, out)
            finally:
                server.shutdown()
                server.server_close()
                manager.close()
    print("panes UI passed: resize/persist/keys/reset, single account, chips, Done (WebKit, Chromium)")
    return 0


def check(playwright, engine, server, out):
    browser = getattr(playwright, engine).launch()
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    errors = []
    page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
    page.goto(server.launch_url)
    rows = page.locator(".message-row")
    expect(rows.first).to_be_visible()

    # ─── One account: nothing that repeats it ───
    expect(page.locator('[data-account-filter="all"]')).to_have_count(0)
    expect(page.locator("#accountFilters .account-filter")).to_have_text("Fixture4")
    expect(rows.first.locator(".row-account")).to_be_hidden()
    expect(rows.first.locator(".row-sender")).to_have_text("Sender")  # not "<sender@…>"
    heights = {round(rows.nth(i).bounding_box()["height"]) for i in range(rows.count())}
    assert heights == {60} or max(heights) - min(heights) <= 1, heights
    rows.first.locator(".row-open").click()
    expect(page.locator("#previewSource")).to_be_hidden()

    # ─── Category entry, chip, and the way back ───
    updates = page.locator('#categoryFilters [data-category="updates"]')
    updates.click()
    chip = page.locator("#filterChips .filter-chip")
    expect(chip).to_have_count(1)
    expect(updates).to_have_attribute("aria-pressed", "true")
    chip.click()
    expect(chip).to_have_count(0)
    expect(updates).to_have_attribute("aria-pressed", "false")
    updates.click()
    updates.click()  # the same entry again shows every category
    expect(chip).to_have_count(0)

    # ─── Done entry and back to a folder ───
    rows.first.locator(".row-check").check()
    page.locator("#markDoneButton").click()
    expect(rows).to_have_count(3)
    page.locator('[data-done-filter="done"]').click()
    expect(rows).to_have_count(1)
    expect(page.locator('[data-mail-folder="inbox"]')).to_have_attribute("aria-pressed", "false")
    page.locator('[data-mail-folder="inbox"]').click()
    expect(rows).to_have_count(3)
    expect(page.locator('[data-mail-folder="inbox"]')).to_have_attribute("aria-pressed", "true")

    # ─── Pane borders ───
    list_before = width(page, ".message-pane")
    handle = page.locator('.splitter[data-split="list"]').bounding_box()
    x, y = handle["x"] + handle["width"] / 2, handle["y"] + 300
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + 120, y, steps=6)
    page.mouse.up()
    list_after = width(page, ".message-pane")
    assert abs(list_after - list_before - 120) <= 2, (list_before, list_after)
    page.mouse.move(x + 120, y)
    page.mouse.down()
    page.mouse.move(x + 2000, y, steps=6)  # far past the window
    page.mouse.up()
    assert width(page, ".preview-pane") >= 359, width(page, ".preview-pane")
    widest = width(page, ".message-pane")
    page.reload()
    expect(rows.first).to_be_visible()
    assert abs(width(page, ".message-pane") - widest) <= 2, "the width did not persist"
    box = page.locator('.splitter[data-split="list"]').bounding_box()  # a 0px border: click its spot
    page.mouse.dblclick(box["x"], box["y"] + 300)
    assert abs(width(page, ".message-pane") - 420) <= 2, width(page, ".message-pane")
    side = page.locator('.splitter[data-split="sidebar"]')
    side_before = width(page, ".sidebar")
    side.evaluate("el => el.focus()")
    page.keyboard.press("ArrowRight")
    page.keyboard.press("ArrowRight")
    assert abs(width(page, ".sidebar") - side_before - 32) <= 2

    assert not errors, errors
    page.screenshot(path=str(out / f"{engine}.png"))
    browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
