"""Keyed list updates retain DOM identity, focus, scroll and geometry.

Run: uv run --with playwright python scripts/check_list_reconciliation_ui.py
Set EMAIL_TOWN_UI_CAPTURE_DIR to retain captures. Synthetic mail only; WebKit and Chromium.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_shortcuts_ui import NOW, FixtureReader, until

from email_town_labeler.config import Paths
from email_town_labeler.desktop.manager import Workbench
from email_town_labeler.desktop.server import LocalServer


class RowsMailbox(FixtureReader):
    def __init__(self, account):
        super().__init__(account)
        self.records = [
            {
                "id": f"m{i}",
                "subject": f"Reconciliation message {i} (synthetic)",
                "folder": "inbox",
                "labels": ["UNREAD"],
                "at": NOW - i * 60_000,
            }
            for i in range(1, 33)
        ]


def capture_directory() -> Path:
    out = Path(
        os.environ.get("EMAIL_TOWN_UI_CAPTURE_DIR", tempfile.mkdtemp(prefix="email-town-reconciliation-ui-"))
    )
    out.mkdir(parents=True, exist_ok=True)
    return out


def workbench(root: Path) -> Workbench:
    paths = Paths.default(root / "home").ensure()
    paths.config_file.write_text('[model]\nref = "rules"\n')
    manager = Workbench(root / "workbench", paths, reader_factory=RowsMailbox)
    manager.demo = True
    manager.accounts.add("Gmail one fixture", "gmail", {"config_dir": str(root / "gmail-one")})
    manager._refresh()
    return manager


def main() -> int:
    out = capture_directory()
    with sync_playwright() as playwright:
        for engine in ("webkit", "chromium"):
            state_root = Path(tempfile.mkdtemp(prefix=f"email-town-reconciliation-{engine}-"))
            manager = workbench(state_root)
            server = LocalServer(manager)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            browser = getattr(playwright, engine).launch()
            try:
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                until.page = page
                errors = []
                page.on("pageerror", lambda error, errors=errors: errors.append(str(error)))
                if os.environ.get("EMAIL_TOWN_UI_BASELINE_ASSETS") == "1":

                    def baseline_asset(body):
                        def serve(route):
                            route.fulfill(status=200, content_type="text/javascript", body=body)

                        return serve

                    for asset in ("app.js", "rowactions.js"):
                        body = subprocess.check_output(
                            ["git", "show", f"e178fd0:src/email_town_labeler/desktop/web/{asset}"], text=True
                        )
                        page.route(f"**/{asset}", baseline_asset(body))
                page.goto(server.launch_url)
                rows = page.locator(".message-row")
                expect(rows).to_have_count(32)
                page.evaluate(
                    """() => {
                      window.__rows = [...document.querySelectorAll('.message-row')].map(row => [row.dataset.messageId, row]);
                      const row = window.__rows[0][1];
                      window.__first = {row, open: row.querySelector('.row-open'),
                        check: row.querySelector('.row-check'), star: row.querySelector('.row-star')};
                      window.__removedRows = 0;
                      new MutationObserver(records => {
                        for (const record of records) for (const node of record.removedNodes)
                          if (node.classList?.contains('message-row')) window.__removedRows += 1;
                      }).observe(document.getElementById('messageList'), {childList: true});
                    }"""
                )
                identical = """() => window.__rows.every(([id, row]) =>
                  document.querySelector(`.message-row[data-message-id="${CSS.escape(id)}"]`) === row)"""
                first_identical = """() => {
                  const row = window.__rows[0][1];
                  const current = document.querySelector(`.message-row[data-message-id="${CSS.escape(row.dataset.messageId)}"]`);
                  return current === window.__first.row && window.__first.open === row.querySelector('.row-open')
                    && window.__first.check === row.querySelector('.row-check')
                    && window.__first.star === row.querySelector('.row-star');
                }"""

                rows.first.locator(".row-open").focus()
                page.keyboard.press("Enter")
                expect(rows.first).not_to_have_class(re.compile(r"\bis-unread\b"))
                assert page.evaluate(first_identical), f"{engine}: open/read replaced row or controls"
                expect(rows.first.locator('[data-row-action="unread"]')).to_have_count(1)
                expect(rows.first.locator(".row-open")).to_be_focused()

                # Reordering an existing focused row can blur it even when the node stays connected.
                reorder_id = rows.nth(20).get_attribute("data-message-id")
                rows.nth(20).locator(".row-open").focus()
                page.evaluate(
                    """() => {
                      window.EmailTownMailHost.state.messages.reverse();
                      window.EmailTownMailHost.renderList();
                    }"""
                )
                reordered = page.locator(f'.message-row[data-message-id="{reorder_id}"]')
                expect(reordered.locator(".row-open")).to_be_focused()
                reorder_geometry = reordered.evaluate(
                    """row => {
                      const rect = row.getBoundingClientRect(), list = document.getElementById('messageList');
                      const bounds = list.getBoundingClientRect();
                      return {visible: rect.top >= bounds.top - 1 && rect.bottom <= bounds.bottom + 1,
                        row: [rect.top, rect.bottom], list: [bounds.top, bounds.bottom], scroll: list.scrollTop};
                    }"""
                )
                assert reorder_geometry["visible"], reorder_geometry
                assert page.evaluate(identical), f"{engine}: reorder replaced a row"
                page.evaluate(
                    """() => {
                      window.EmailTownMailHost.state.messages.reverse();
                      window.EmailTownMailHost.renderList();
                    }"""
                )
                page.evaluate(
                    "() => new Promise(resolve => requestAnimationFrame(() => { window.__removedRows = 0; resolve(); }))"
                )

                rows.nth(11).locator(".row-open").click()
                expect(page.locator("#previewSubject")).to_have_text("Reconciliation message 12 (synthetic)")
                before = page.evaluate(
                    """() => {
                      const list = document.getElementById('messageList');
                      const rect = window.__rows[20][1].getBoundingClientRect();
                      return {scroll: list.scrollTop, top: rect.top, height: rect.height};
                    }"""
                )
                prior_seq = page.evaluate("window.EmailTownMailHost.state.seq")
                manager.last_refresh += 1
                manager._touch()
                page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
                until(
                    lambda page=page, prior_seq=prior_seq: (
                        page.evaluate("window.EmailTownMailHost.state.seq") > prior_seq
                    ),
                    "background snapshot crosses the browser boundary",
                )
                after = page.evaluate(
                    """() => {
                      const list = document.getElementById('messageList');
                      const rect = window.__rows[20][1].getBoundingClientRect();
                      return {scroll: list.scrollTop, top: rect.top, height: rect.height};
                    }"""
                )
                assert after == before, f"{engine}: background redraw moved list: {before} -> {after}"
                assert page.evaluate(identical), f"{engine}: background refresh replaced a row"

                for key in ("j", "j", "j", "j", "k", "k", "ArrowDown", "ArrowUp"):
                    page.keyboard.press(key)
                assert page.evaluate(identical), f"{engine}: repeated keys replaced rows"
                assert page.evaluate("window.__removedRows") == 0, f"{engine}: rows removed during navigation"
                assert page.evaluate("window.__rows[0][1].getAnimations({subtree:true}).length") == 0

                # Hold a real immediate response after the server creates it; let a newer long-poll win.
                page.evaluate(
                    """() => {
                      window.__originalFetch = window.fetch;
                      window.__heldState = [];
                      window.__holdImmediate = true;
                      window.__heldResponseConsumed = false;
                      window.fetch = async (...args) => {
                        const response = await window.__originalFetch(...args);
                        if (window.__holdImmediate && String(args[0]).endsWith('/api/state')) {
                          window.__holdImmediate = false;
                          const seq = (await response.clone().json()).seq;
                          const json = response.json.bind(response);
                          response.json = async (...jsonArgs) => {
                            const data = await json(...jsonArgs);
                            window.__heldResponseConsumed = true;
                            return data;
                          };
                          return new Promise(resolve => window.__heldState.push({response, resolve, seq}));
                        }
                        return response;
                      };
                      document.dispatchEvent(new Event('visibilitychange'));
                    }"""
                )
                until(
                    lambda page=page: page.evaluate("window.__heldState.length === 1"), "held immediate state"
                )
                manager.last_refresh += 1
                manager._touch()
                until(
                    lambda page=page: page.evaluate(
                        "window.EmailTownMailHost.state.seq > window.__heldState[0].seq"
                    ),
                    "newer long-poll applies while immediate response is held",
                )
                newer = page.evaluate("window.EmailTownMailHost.state.seq")
                page.evaluate(
                    """() => {
                      const held = window.__heldState.shift();
                      held.resolve(held.response);
                      window.fetch = window.__originalFetch;
                    }"""
                )
                until(
                    lambda page=page: page.evaluate(
                        "window.__heldResponseConsumed && !window.EmailTownMailHost.state.syncing"
                    ),
                    "held response is consumed and its caller settles",
                )
                assert page.evaluate("window.EmailTownMailHost.state.seq") == newer
                assert page.evaluate(identical), f"{engine}: out-of-order response replaced rows"

                # Done removes a focused pending row; focus advances. Undo restores the active row.
                page.locator('[data-done-filter="pending"]').click()
                rows.nth(11).locator(".row-open").click()
                rows.nth(11).locator(".row-open").focus()
                completed_id = rows.nth(11).get_attribute("data-message-id")
                page.keyboard.press("d")
                expect(page.locator(f'.message-row[data-message-id="{completed_id}"]')).to_have_count(0)
                expect(rows.nth(11).locator(".row-open")).to_be_focused()
                page.locator("#toast button").click()
                restored = page.locator(f'.message-row[data-message-id="{completed_id}"]')
                expect(restored).to_have_count(1)
                expect(restored.locator(".row-open")).to_be_focused()
                page.screenshot(path=str(out / f"{engine}-after-undo.png"))

                # Bulk read updates preserve selection and every unaffected row/control.
                page.locator('[data-done-filter="all"]').click()
                for index in range(3):
                    rows.nth(index).locator(".row-check").click()
                page.locator("#bulkMore > summary").click()
                page.locator('#bulkMore [data-mail-action="unread"]').click()
                until(lambda manager=manager: not manager.snapshot()["outbox"], "bulk unread outbox")
                expect(page.locator(".message-row.is-selected")).to_have_count(3)
                assert page.evaluate(identical), f"{engine}: bulk action replaced rows"
                assert rows.nth(3).evaluate("row => row === window.__rows[3][1]")

                # Narrow Back and background updates keep the original row, focus and list scroll.
                page.set_viewport_size({"width": 1000, "height": 900})
                page.keyboard.press("Escape")
                rows.nth(4).locator(".row-open").click()
                expect(page.locator("#previewClose")).to_be_focused()
                scroll = page.locator("#messageList").evaluate("list => list.scrollTop")
                narrow_seq = page.evaluate("window.EmailTownMailHost.state.seq")
                manager.last_refresh += 1
                manager._touch()
                page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
                until(
                    lambda page=page, narrow_seq=narrow_seq: (
                        page.evaluate("window.EmailTownMailHost.state.seq") > narrow_seq
                    ),
                    "narrow background snapshot crosses the browser boundary",
                )
                expect(page.locator("#previewClose")).to_be_focused()
                page.locator("#previewClose").click()
                expect(rows.nth(4).locator(".row-open")).to_be_focused()
                assert page.locator("#messageList").evaluate("list => list.scrollTop") == scroll
                assert not errors, errors
                print(f"{engine}: identity, focus, scroll, Done/undo, bulk and held responses pass")
            finally:
                browser.close()
                server.shutdown()
                server.server_close()
                manager.close()
    print(f"list reconciliation UI passed. Captures: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
