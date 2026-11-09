"""Nothing moves when you point at it or tab to it. Real local server, synthetic mail only.

Run: uv run --with playwright python scripts/check_layout_stability_ui.py [--report]

Hovering (and keyboard-focusing) a control may change its own colour, show controls that float over the row,
or swap one label for another in the same space; it may never shift, resize or reflow anything else. The
check points the mouse at every visible interactive element (message rows, row controls, sidebar filters,
toolbar and reading-pane buttons, folds, attachment cards), then tabs through the page, and compares every
other element's box with the box it had at rest. Any edge that moves more than half a pixel is a failure.
Desktop (1280), narrow (1000, the reading pane covers the list) and phone (390) widths, light theme.
WebKit (the macOS app's engine) and Chromium. --report lists every shift instead of stopping at the first.
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

VIEWPORTS = [(1280, 840), (1000, 800), (390, 844)]
TOLERANCE = 0.5
HOVER_TARGETS = (
    ".message-row, .row-star, .row-action, .row-check, .category-filter, .account-filter, .nav-item,"
    " .folder-tab, button:not(.row-star):not(.row-action), summary, a[href], .attachment-card"
)
# Boxes of every element, keyed by a stable id stamped once at rest. Elements that appear or disappear
# (the row's hover controls, the time they replace) are not compared: only what exists in both states.
SNAPSHOT = """() => {
  const out = {};
  let next = window.__lsNext || 0;
  for (const el of document.querySelectorAll('body *')) {
    if (!el.dataset.ls) el.dataset.ls = String(next++);
    const style = getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') continue;
    const r = el.getBoundingClientRect();
    if (!r.width && !r.height) continue;
    out[el.dataset.ls] = [r.left, r.top, r.width, r.height];
  }
  window.__lsNext = next;
  return out;
}"""
DESCRIBE = """(id) => {
  const el = document.querySelector(`[data-ls="${id}"]`);
  if (!el) return id;
  const cls = [...el.classList].slice(0, 2).map((c) => '.' + c).join('');
  return el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + cls;
}"""


def workbench(root: Path) -> Workbench:
    paths = Paths.default(root / "home").ensure()
    paths.config_file.write_text('[model]\nref = "rules"\n')  # offline: never the 263 MB default model

    def factory(account):
        reader = FixtureReader(account)
        first = reader.records[0]
        reader.records = [
            dict(
                first,
                id=f"m{i}",
                subject=(
                    "A long subject that wraps onto a second line in the list so the row is taller than most"
                    if i == 2
                    else f"Fixture message {i}"
                ),
                labels=["UNREAD"] if i % 3 else [],
                at=first["at"] - i * 60_000,
            )
            for i in range(1, 13)
        ]
        return reader

    manager = Workbench(root / "workbench", paths, reader_factory=factory)
    manager.accounts.add(
        "Fixture", "imap", {"host": "fixture", "username": "fixture@example.test", "port": 993}
    )
    manager._refresh()
    return manager


def shifts(before: dict, after: dict, skip: set[str]) -> list[tuple[str, list[float]]]:
    moved = []
    for key, box in before.items():
        if key in skip or key not in after:
            continue
        delta = [round(b - a, 1) for a, b in zip(box, after[key], strict=True)]
        if any(abs(d) > TOLERANCE for d in delta):
            moved.append((key, delta))
    return moved


def main() -> int:
    report = "--report" in sys.argv
    out = Path(tempfile.mkdtemp(prefix="email-town-layout-stability-ui-"))
    failures: list[str] = []
    with sync_playwright() as playwright:
        for engine in ("webkit", "chromium"):
            manager = workbench(out / engine)
            server = LocalServer(manager)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            browser = getattr(playwright, engine).launch()
            try:
                for width, height in VIEWPORTS:
                    page = browser.new_page(viewport={"width": width, "height": height})
                    until.page = page
                    page.goto(server.launch_url)
                    expect(page.locator(".message-row")).to_have_count(12)
                    if width >= 1200:  # the reading pane, with its own buttons, folds and cards
                        page.locator(".message-row .row-open").first.click()
                        expect(page.locator("[data-compose-mode='reply']")).to_be_visible()
                    rest = page.locator(".app-footer").bounding_box()
                    neutral = (rest["x"] + rest["width"] / 2, rest["y"] + rest["height"] / 2)
                    page.mouse.move(*neutral)
                    page.wait_for_timeout(400)
                    base = page.evaluate(SNAPSHOT)
                    targets = page.locator(HOVER_TARGETS)
                    where = f"{engine} {width}px"
                    for i in range(targets.count()):
                        target = targets.nth(i)
                        if not target.is_visible():
                            continue
                        box = target.bounding_box()
                        if not box or box["y"] + box["height"] / 2 > height or box["y"] < 0:
                            continue  # off screen: scrolled panes are not pointed at
                        key = target.get_attribute("data-ls")
                        inside = set(
                            target.evaluate(
                                "el => [...el.querySelectorAll('*')].map(e => e.dataset.ls).filter(Boolean)"
                            )
                        )
                        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                        page.wait_for_timeout(250)
                        hovered = page.evaluate(SNAPSHOT)
                        page.mouse.move(*neutral)
                        page.wait_for_timeout(250)
                        moved = shifts(base, hovered, {key, *inside})
                        if moved:
                            name = page.evaluate(DESCRIBE, key)
                            what = ", ".join(f"{page.evaluate(DESCRIBE, k)} {d}" for k, d in moved[:4])
                            failures.append(f"{where}: hovering {name} moves {len(moved)}: {what}")
                            if not report:
                                raise AssertionError(failures[-1])
                    # Keyboard focus: tab through the page; a focus ring is an outline, never a reflow.
                    page.mouse.move(*neutral)
                    page.locator("body").click(position={"x": 1, "y": 1})
                    base = page.evaluate(SNAPSHOT)
                    for _ in range(40):
                        page.keyboard.press("Tab")
                        page.wait_for_timeout(120)
                        focused = page.evaluate("document.activeElement?.dataset?.ls || ''")
                        inside = set(
                            page.evaluate(
                                "[...(document.activeElement?.querySelectorAll('*') || [])]"
                                ".map(e => e.dataset.ls).filter(Boolean)"
                            )
                        )
                        now = page.evaluate(SNAPSHOT)
                        scrolled = page.evaluate(
                            "[...document.querySelectorAll('.sidebar, .message-list, .preview-pane')]"
                            ".some(e => e.scrollTop > 0)"
                        )
                        if scrolled:
                            break  # focus scrolled a pane into view: positions legitimately differ
                        # Inside the focused row, its own controls may appear; the rest may not move.
                        row = page.evaluate(
                            "(() => { const r = document.activeElement?.closest('.message-row');"
                            " return r ? [r.dataset.ls, ...[...r.querySelectorAll('*')].map(e => e.dataset.ls)] : []; })()"
                        )
                        moved = shifts(base, now, {focused, *inside, *row})
                        if moved:
                            name = page.evaluate(DESCRIBE, focused)
                            what = ", ".join(f"{page.evaluate(DESCRIBE, k)} {d}" for k, d in moved[:4])
                            failures.append(f"{where}: focusing {name} moves {len(moved)}: {what}")
                            if not report:
                                raise AssertionError(failures[-1])
                        base = now
                    page.screenshot(path=str(out / f"{engine}-{width}.png"))
                    page.close()
                print(f"{engine}: checked {', '.join(str(w) for w, _ in VIEWPORTS)} px")
            finally:
                browser.close()
                server.shutdown()
                server.server_close()
                manager.close()
    if failures:
        print("\n".join(failures))
        print(f"{len(failures)} layout shifts. Captures: {out}")
        return 1
    print(f"layout stability UI passed: nothing moves on hover or focus. Captures: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
