"""Routine progress never resizes the footer or moves the mail list.

Run: uv run --extra dev --with playwright python scripts/check_outbox_layout_ui.py
Use --assets-rev REV to replay the check against an earlier app.js and style.css.
Real LocalServer, synthetic mail only; WebKit and Chromium at 1440, 1000 and 390 px.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_list_reconciliation_ui import RowsMailbox

from email_town_labeler.config import Paths
from email_town_labeler.desktop.manager import Workbench
from email_town_labeler.desktop.server import LocalServer

VIEWPORTS = [(1440, 900), (1000, 900), (390, 900)]
STATES = (
    (0, False),
    (1, False),
    (20, False),
    (0, False),
    (0, True),
    (1, True),
    (20, True),
    (0, True),
    (0, False),
)
TOLERANCE = 0.5
METRICS = """() => {
  const footer = document.querySelector('.app-footer');
  const list = document.getElementById('messageList');
  const status = document.getElementById('footerStatus');
  const shortcuts = document.getElementById('shortcutsButton');
  const inlineStatus = document.getElementById('listStatus');
  const syncIndicator = document.getElementById('syncIndicator');
  const active = document.activeElement;
  const box = element => {
    const r = element.getBoundingClientRect();
    return [r.x, r.y, r.width, r.height];
  };
  const shortcutBox = shortcuts.getBoundingClientRect();
  const footerBox = footer.getBoundingClientRect();
  return {
    footer: box(footer), list: box(list), shortcuts: box(shortcuts),
    scroll: list.scrollTop,
    focusedRow: active?.closest('.message-row')?.dataset.messageId || '',
    focusedControl: active?.classList.contains('row-open') || false,
    body: [document.body.scrollWidth, document.body.scrollHeight],
    viewport: [innerWidth, innerHeight],
    shortcutsVisible: shortcutBox.left >= 0 && shortcutBox.right <= innerWidth
      && shortcutBox.top >= footerBox.top && shortcutBox.bottom <= footerBox.bottom,
    statusText: status.textContent,
    statusTitle: status.title,
    statusStyle: [getComputedStyle(status).whiteSpace, getComputedStyle(status).textOverflow],
    statusOverflow: status.scrollWidth > status.clientWidth,
    inlineStatusText: inlineStatus.textContent,
    inlineStatusHeight: inlineStatus.getBoundingClientRect().height,
    headerSyncText: document.getElementById('syncText').textContent,
    headerSyncVisible: syncIndicator.getBoundingClientRect().width > 0
      && getComputedStyle(syncIndicator).visibility !== 'hidden',
  };
}"""


def workbench(root: Path) -> Workbench:
    paths = Paths.default(root / "home").ensure()
    paths.config_file.write_text('[model]\nref = "rules"\n')
    manager = Workbench(root / "workbench", paths, reader_factory=RowsMailbox)
    manager.accounts.add("Gmail fixture", "gmail", {"config_dir": str(root / "gmail")})
    manager._refresh()
    return manager


def set_progress(manager: Workbench, count: int, refreshing: bool) -> None:
    with manager.lock:
        manager.outbox_jobs = {
            f"job-{index}": {
                "id": f"job-{index}",
                "action": "read",
                "done": 0,
                "failed": 0,
                "total": 1,
            }
            for index in range(count)
        }
        manager.refreshing = refreshing
        manager._touch()


def expected_status(count: int, refreshing: bool) -> str:
    if count:
        return " · ".join(["Marking read 0 of 1"] * count)
    return "Syncing mail…" if refreshing else "Classified on your device"


def route_assets(page, revision: str) -> None:
    for name, content_type in (("app.js", "text/javascript"), ("style.css", "text/css")):
        body = subprocess.check_output(
            ["git", "show", f"{revision}:src/email_town_labeler/desktop/web/{name}"], text=True
        )

        def asset_handler(body, content_type):
            def serve(route):
                route.fulfill(status=200, content_type=content_type, body=body)

            return serve

        page.route(f"**/{name}", asset_handler(body, content_type))


def close_enough(left: float, right: float) -> bool:
    return abs(left - right) <= TOLERANCE


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--assets-rev", help="serve app.js and style.css from this Git revision")
    args = parser.parse_args()
    out = Path(tempfile.mkdtemp(prefix="email-town-outbox-layout-ui-"))
    failures: list[str] = []
    results: list[dict] = []

    with sync_playwright() as playwright:
        for engine in ("webkit", "chromium"):
            manager = workbench(out / engine)
            server = LocalServer(manager)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            browser = getattr(playwright, engine).launch()
            try:
                for width, height in VIEWPORTS:
                    page = browser.new_page(viewport={"width": width, "height": height})
                    errors = []
                    page.on("pageerror", lambda error, errors=errors: errors.append(str(error)))
                    try:
                        if args.assets_rev:
                            route_assets(page, args.assets_rev)
                        page.goto(server.launch_url)
                        expect(page.locator(".message-row")).to_have_count(32)
                        row_open = page.locator(".message-row").nth(20).locator(".row-open")
                        row_open.focus()
                        expect(row_open).to_be_focused()
                        focused_id = row_open.evaluate("el => el.closest('.message-row').dataset.messageId")
                        baseline = None
                        captured = set()
                        for count, refreshing in STATES:
                            set_progress(manager, count, refreshing)
                            expected = expected_status(count, refreshing)
                            sync_text = "Syncing" if refreshing else "Synced"
                            expect(page.locator("#syncText")).to_have_text(sync_text)
                            expect(page.locator("#footerStatus")).to_be_visible()
                            expect(page.locator("#footerStatus")).to_have_text(expected)
                            current = page.evaluate(METRICS)
                            where = f"{engine} {width}px, {count} jobs, refreshing={refreshing}"
                            results.append(
                                {
                                    "engine": engine,
                                    "width": width,
                                    "jobs": count,
                                    "refreshing": refreshing,
                                    "footer": current["footer"],
                                    "list": current["list"],
                                    "shortcuts": current["shortcuts"],
                                    "inlineStatusHeight": current["inlineStatusHeight"],
                                    "scroll": current["scroll"],
                                }
                            )
                            print(json.dumps(results[-1], separators=(",", ":")), flush=True)
                            if baseline is None:
                                baseline = current
                            for name in ("footer", "list", "shortcuts"):
                                if any(
                                    not close_enough(a, b)
                                    for a, b in zip(baseline[name], current[name], strict=True)
                                ):
                                    failures.append(
                                        f"{where}: {name} moved {baseline[name]} -> {current[name]}"
                                    )
                            if not close_enough(baseline["scroll"], current["scroll"]):
                                failures.append(
                                    f"{where}: list scroll moved {baseline['scroll']} -> {current['scroll']}"
                                )
                            if current["focusedRow"] != focused_id or not current["focusedControl"]:
                                failures.append(
                                    f"{where}: focused row control was lost: {current['focusedRow']}"
                                )
                            if any(current["body"][i] > current["viewport"][i] for i in (0, 1)):
                                failures.append(
                                    f"{where}: body overflows: {current['body']} > {current['viewport']}"
                                )
                            if not current["shortcutsVisible"]:
                                failures.append(f"{where}: keyboard shortcuts control is clipped")
                            if current["statusText"] != expected:
                                failures.append(f"{where}: full status text is not available")
                            if current["statusTitle"] != expected:
                                failures.append(f"{where}: full status hover text is not available")
                            if current["inlineStatusText"]:
                                failures.append(
                                    f"{where}: duplicate routine inline status: {current['inlineStatusText']}"
                                )
                            if current["headerSyncText"] != sync_text:
                                failures.append(f"{where}: header syncing text is not {sync_text}")
                            if width > 767 and not current["headerSyncVisible"]:
                                failures.append(f"{where}: header syncing indicator is hidden")
                            capture = (
                                "long-jobs"
                                if count == 20 and not refreshing
                                else "refreshing"
                                if count == 0 and refreshing
                                else ""
                            )
                            if capture and capture not in captured:
                                page.screenshot(path=str(out / f"{engine}-{width}-{capture}.png"))
                                captured.add(capture)
                            if count == 20:
                                if (
                                    current["statusStyle"] != ["nowrap", "ellipsis"]
                                    or not current["statusOverflow"]
                                ):
                                    failures.append(
                                        f"{where}: long status is not ellipsized: {current['statusStyle']}"
                                    )
                                if not refreshing:
                                    page.locator("#shortcutsButton").click()
                                    expect(page.locator("#shortcutsDialog")).to_be_visible()
                                    page.keyboard.press("Escape")
                                    expect(page.locator("#shortcutsDialog")).not_to_be_visible()
                                    row_open.focus()
                                    expect(row_open).to_be_focused()
                        if captured != {"long-jobs", "refreshing"}:
                            failures.append(f"{engine} {width}px: missing progress captures: {captured}")
                        if errors:
                            failures.append(f"{engine} {width}px: browser errors: {errors}")
                    finally:
                        set_progress(manager, 0, False)
                        page.close()
                print(
                    f"{engine}: exercised outbox and refreshing states at 1440, 1000 and 390 px", flush=True
                )
            finally:
                set_progress(manager, 0, False)
                browser.close()
                server.shutdown()
                server.server_close()
                manager.close()

    if failures:
        print("\n".join(failures))
        print(f"{len(failures)} progress layout failures. Captures: {out}")
        return 1
    print(f"progress layout UI passed: stable footer, list, focus and scroll. Captures: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
