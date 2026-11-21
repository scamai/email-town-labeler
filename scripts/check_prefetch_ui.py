"""Opening the next message must not wait on the network: it is prefetched while the current one is read.

Run: ``uv run --with playwright python scripts/check_prefetch_ui.py``

The fixture mailbox takes 1 s per body. Opening the first message really waits, so "Reading message…"
must appear (the placeholder still works); the three below it are then fetched in one batch, and
pressing j must draw the next body at once without the placeholder ever showing. When the server skips
the prefetch (a busy account), the page reads only the message it opened. All data is synthetic;
captures go to a temporary directory.
"""

from __future__ import annotations

import sys
import tempfile
import threading
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_shortcuts_ui import FixtureReader, until

from email_town_labeler.config import Paths
from email_town_labeler.desktop.manager import Workbench
from email_town_labeler.desktop.server import LocalServer
from email_town_labeler.desktop.state import BUNDLEABLE

BODY_DELAY = 1.0  # seconds per body request, batched or not


def flat_inbox(manager):
    """List every message as its own row, as before Inbox bundles: this check is about the rows themselves."""
    for category in BUNDLEABLE:
        manager.set_bundled(category, False)


class SlowBodies(FixtureReader):
    read_ahead = 0  # nothing is read ahead at list time: only the prefetch around the open message

    def __init__(self, account):
        super().__init__(account)
        self.batches = []

    def detail(self, provider_id):
        time.sleep(BODY_DELAY)
        return super().detail(provider_id)

    def details(self, provider_ids):
        time.sleep(BODY_DELAY)  # one request for the whole batch, as Gmail and IMAP do
        self.batches.append(list(provider_ids))
        return {pid: super(SlowBodies, self).detail(pid) for pid in provider_ids}


def main() -> int:
    out = Path(tempfile.mkdtemp(prefix="email-town-prefetch-ui-"))
    paths = Paths.default(out / "home").ensure()
    paths.config_file.write_text('[model]\nref = "rules"\n')  # offline: never the default model
    readers = []

    def factory(account):
        readers.append(SlowBodies(account))
        return readers[-1]

    manager = Workbench(out / "workbench", paths, reader_factory=factory)
    flat_inbox(manager)  # bundles have their own check (check_bundles_ui.py)
    manager.accounts.add(
        "Fixture", "imap", {"host": "fixture", "username": "fixture@example.test", "port": 993}
    )
    manager._refresh()
    server = LocalServer(manager)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as playwright:
            for engine in ("webkit", "chromium"):
                manager.bodies.clear()
                readers[-1].batches.clear()
                browser = getattr(playwright, engine).launch()
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                errors = []
                page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
                until.page = page
                page.goto(server.launch_url)
                rows = page.locator(".message-row")
                expect(rows).to_have_count(4)
                body = page.locator("#previewBody")

                rows.nth(0).locator(".row-open").click()
                expect(body).to_have_text("Reading message…", timeout=900)  # a real 1 s wait says so
                expect(body).to_contain_text("Synthetic body.", timeout=3000)
                keys = [m["id"] for m in manager.snapshot()["messages"]]
                until(
                    lambda keys=keys: all(k in manager.bodies for k in keys[1:4]),
                    "the next three are prefetched",
                )
                assert [len(b) for b in readers[-1].batches] == [3], readers[-1].batches  # one batch

                # Record every text the body shows from here on: the placeholder must never appear.
                page.evaluate(
                    """() => {
                      const el = document.getElementById("previewBody");
                      window.__bodyTexts = [];
                      new MutationObserver(() => window.__bodyTexts.push(el.textContent))
                        .observe(el, {childList: true, characterData: true, subtree: true});
                    }"""
                )
                started = time.monotonic()
                page.keyboard.press("j")
                expect(page.locator("#previewSubject")).to_have_text("Fixture message 2")
                expect(body).to_contain_text("Synthetic body.", timeout=500)
                elapsed = time.monotonic() - started
                texts = page.evaluate("window.__bodyTexts")
                assert not any("Reading message" in t for t in texts), texts
                assert elapsed < BODY_DELAY, f"the next body waited {elapsed:.2f} s: not prefetched"
                assert not errors, errors
                page.screenshot(path=str(out / f"{engine}-next.png"))

                # A busy account: the server skips the prefetch. The page must not ask for each neighbour
                # one by one (each such read queued behind the account for up to 20 s and held a connection).
                prefetch = manager.prefetch
                manager.prefetch = lambda keys: {"fetched": 0, "ids": []}
                manager.bodies.clear()
                page.reload()
                expect(rows).to_have_count(4)
                reads = []
                page.on("request", lambda r, reads=reads: "/api/message?" in r.url and reads.append(r.url))
                rows.nth(0).locator(".row-open").click()
                expect(body).to_contain_text("Synthetic body.", timeout=3000)
                page.wait_for_timeout(600)  # the prefetch fires 150 ms after the open
                manager.prefetch = prefetch
                assert len(reads) == 1, f"{len(reads)} message reads after a skipped prefetch: {reads}"
                assert not errors, errors
                browser.close()
                print(f"{engine}: next message drawn in {elapsed * 1000:.0f} ms without a placeholder")
                print(f"{engine}: a skipped prefetch reads only the open message")
    finally:
        server.shutdown()
        server.server_close()
        manager.close()
    print(f"prefetch UI passed. Captures: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
