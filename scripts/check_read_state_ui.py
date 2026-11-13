"""Open-to-read, against the real local server (synthetic mail only).

Run: uv run --no-project --with-editable '.[dev]' --with playwright python scripts/check_read_state_ui.py

As in Gmail's reading pane, staying on a message reads it and passing over it with j does not: the read
is written after 1.5 s on the same message. Mark unread on the open message sticks for that open, even
inside the delay. A state answer the server produced before the read was queued, arriving after it, does
not turn the row back to unread (it did, for a few milliseconds per open, in a real session's log).
WebKit (the macOS app's engine) and Chromium.
"""

from __future__ import annotations

import json
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

UNREAD = re.compile(r"\bis-unread\b")


def main() -> int:
    out = Path(tempfile.mkdtemp(prefix="email-town-read-state-ui-"))
    paths = Paths.default(out / "home").ensure()
    paths.config_file.write_text('[model]\nref = "rules"\n')  # offline: never the 263 MB default model
    readers = []

    def factory(account):
        reader = FixtureReader(account)
        reader.records += [
            {
                "id": f"m{i}",
                "subject": f"Fixture message {i}",
                "folder": "inbox",
                "labels": ["UNREAD"],
                "at": reader.records[0]["at"] - i * 60_000,
            }
            for i in range(5, 8)
        ]
        readers.append(reader)
        return reader

    manager = Workbench(out / "workbench", paths, reader_factory=factory)
    manager.accounts.add(
        "Fixture", "imap", {"host": "fixture", "username": "fixture@example.test", "port": 993}
    )
    manager._refresh()
    server = LocalServer(manager)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    mailbox = readers[-1]
    real_mark_read = manager.mark_read
    try:
        with sync_playwright() as playwright:
            for engine in ("webkit", "chromium"):
                for record in mailbox.records:
                    record.update(folder="inbox", labels=["UNREAD"])
                manager.folder = "inbox"
                manager.messages.clear()
                manager._refresh()
                browser = getattr(playwright, engine).launch()
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                errors = []
                page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
                until.page = page
                # Every long-poll passes through here from the first one on, so the answer in flight when
                # the read is queued is the one replaced (a route added later misses it, and the client
                # then drops the stale answer by its seq: that proved nothing).
                stale = {}

                def mark_read_after_a_snapshot(key, read=True, stale=stale):
                    stale["body"] = manager.snapshot()  # produced before the write is queued
                    return real_mark_read(key, read)

                def deliver_late(route, _request=None, stale=stale):
                    response = route.fetch()
                    body = stale.pop("body", None)
                    if body is None:
                        route.fulfill(response=response)
                    else:  # what a long-poll already in flight would have carried
                        route.fulfill(response=response, body=json.dumps(body))

                page.route("**/api/state?wait=*", deliver_late)
                # The page's own event log, as desktop.log receives it: a flip shows up as state-flags.
                events = []

                def collect(request, events=events):
                    if request.url.endswith("/api/ui-log"):
                        events.extend(json.loads(request.post_data or "{}").get("events", []))

                page.on("request", collect)
                page.goto(server.launch_url)
                rows = page.locator(".message-row")
                expect(rows.first).to_be_visible()
                count = rows.count()
                assert count >= 5, count

                # ─── Passing over messages with j reads none of them; staying reads the last ───
                rows.nth(0).locator(".row-open").click()
                for _ in range(3):
                    page.wait_for_timeout(300)
                    page.keyboard.press("j")
                expect(page.locator("#previewSubject")).to_have_text(
                    rows.nth(3).locator(".row-open").inner_text()
                )
                page.wait_for_timeout(2200)
                for i in range(3):
                    expect(rows.nth(i)).to_have_class(UNREAD)  # passed over: still unread
                expect(rows.nth(3)).not_to_have_class(UNREAD)  # stayed on: read
                expect(page.locator("#previewUnread")).to_be_hidden()

                # ─── Mark unread inside the delay sticks for this open ───
                rows.nth(4).locator(".row-open").click()
                page.wait_for_timeout(400)
                page.keyboard.press("Shift+U")
                page.wait_for_timeout(2500)
                expect(rows.nth(4)).to_have_class(UNREAD)
                expect(page.locator("#previewUnread")).to_be_visible()

                # ─── A state answer older than the read cannot undo it ───
                manager.mark_read = mark_read_after_a_snapshot
                target = rows.nth(1)
                short = (target.get_attribute("data-message-id") or "")[:8]
                target.locator(".row-open").click()
                expect(target).not_to_have_class(UNREAD, timeout=5000)
                page.wait_for_timeout(1500)  # the late answer arrives and is applied
                assert "body" not in stale, "the stale answer was never delivered: the check proves nothing"
                page.wait_for_timeout(1200)  # the page's log batch is sent once a second
                changes = [str(e.get("changes")) for e in events if e.get("type") == "state-flags"]
                flips = [c for c in changes if f"{short}:unread" in c]
                held = [e for e in events if e.get("type") == "flag-held" and e.get("id") == short]
                assert not flips, f"an older state answer turned the read row unread: {flips}"
                assert held, "the late answer never reached the page: the check proves nothing"
                expect(target).not_to_have_class(UNREAD)
                page.unroute_all(behavior="ignoreErrors")  # a long-poll may still be in flight
                manager.mark_read = real_mark_read

                assert not errors, errors
                page.screenshot(path=str(out / f"{engine}.png"))
                browser.close()
    finally:
        manager.mark_read = real_mark_read
        server.shutdown()
        server.server_close()
        manager.close()
    print(f"read state UI passed: pass-over, delayed read, explicit unread, late state. Captures: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
