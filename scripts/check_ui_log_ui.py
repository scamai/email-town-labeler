"""The page's events in desktop.log describe what the user acted on (synthetic mail only).

Run: ``uv run --extra dev --with playwright python scripts/check_ui_log_ui.py``

Read from a real desktop.log (2026-11-20): every Backspace/e line named the message the app had moved on
to and `selected=0`, because the key was logged after its action had archived or trashed the open
message and advanced. Every page load also logged `open-changed from=- to=-` before anything was opened.
This drives the real page and server in WebKit (the macOS app's engine) and Chromium and reads the
events the page posts to /api/ui-log, as desktop.log receives them.
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_prefetch_ui import flat_inbox
from check_shortcuts_ui import FixtureReader, until

from email_town_labeler.config import Paths
from email_town_labeler.desktop.manager import Workbench
from email_town_labeler.desktop.server import LocalServer


def main() -> int:
    out = Path(tempfile.mkdtemp(prefix="email-town-ui-log-"))
    paths = Paths.default(out / "home").ensure()
    paths.config_file.write_text('[model]\nref = "rules"\n')  # offline: never the default model
    manager = Workbench(out / "workbench", paths, reader_factory=FixtureReader)
    flat_inbox(manager)
    manager.accounts.add(
        "Fixture", "imap", {"host": "fixture", "username": "fixture@example.test", "port": 993}
    )
    manager._refresh()
    server = LocalServer(manager)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as playwright:
            for engine in ("webkit", "chromium"):
                manager._refresh()
                browser = getattr(playwright, engine).launch()
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                errors, events = [], []
                page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
                page.on(
                    "request",
                    lambda r, events=events: (
                        r.url.endswith("/api/ui-log")
                        and events.extend(json.loads(r.post_data or "{}").get("events", []))
                    ),
                )
                until.page = page
                page.goto(server.launch_url)
                rows = page.locator(".message-row")
                expect(rows.first).to_be_visible()
                rows.first.locator(".row-open").click()
                expect(page.locator("#previewSubject")).not_to_be_empty()
                until(lambda events=events: any(e.get("type") == "open" for e in events), "the open event")
                first = next(e for e in events if e.get("type") == "open")["id"]  # its 8-character key
                page.keyboard.press("e")  # archive the open message; the reader moves on to the next
                until(
                    lambda events=events: any(e.get("type") == "key" for e in events),
                    "the key event",
                    show=lambda events=events: events,
                )

                opens = [e for e in events if e.get("type") == "open-changed"]
                assert opens and opens[0].get("to") != "-", f"a no-op open-changed at page load: {opens[:2]}"
                key = next(e for e in events if e.get("type") == "key")
                assert key.get("key") == "e" and key.get("active") == first, (key, first)
                assert not errors, errors
                browser.close()
                print(f"{engine}: 'e' is logged on the message it archived, no open-changed before an open")
    finally:
        server.shutdown()
        server.server_close()
        manager.close()
    print(f"UI log check passed. Captures: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
