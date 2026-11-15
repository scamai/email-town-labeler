"""Inbox bundles: low-attention categories collapse into one row each, and one click clears a whole bundle.

Run: uv run --no-project --with-editable '.[dev]' --with playwright python scripts/check_bundles_ui.py

- In the Inbox, Newsletters and Orders (bundled by default) show as one row each, placed by their newest
  message, with a count, unread count and the first senders; Action needed stays as its own rows;
- the count says how many messages are in bundles; j/k step from message to message over the bundles;
- opening a bundle shows its messages and a bar saying how the category shows in the Inbox; "Show one by
  one" turns bundling off for that category on this device (the server keeps it), "Bundle them" back on;
- a search, another folder, or a chosen category lists messages one by one;
- "Archive all" archives exactly the bundle's messages with the same 5 s Undo: Undo sends nothing, and
  after the window every one of them, and nothing else, is archived in the mailbox;
- no horizontal overflow at 390 px; light and dark captures to look at.
WebKit (the macOS app's engine) and Chromium; synthetic data only.
"""

from __future__ import annotations

import tempfile
import threading
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from email_town_labeler.config import Paths
from email_town_labeler.desktop.manager import Workbench
from email_town_labeler.desktop.server import LocalServer

NOW = int(time.time() * 1000)
FIXTURES = [  # (id, sender, category, unread, minutes ago)
    ("a1", "Teammate", "action_needed", True, 1),
    ("n1", "Weekly Digest", "newsletters", True, 2),
    ("n2", "Product Notes", "newsletters", True, 3),
    ("o1", "Fixture Shop", "orders", False, 4),
    ("a2", "Landlord", "action_needed", False, 5),
    ("n3", "Weekly Digest", "newsletters", False, 6),
]


class BundleMailbox:
    def __init__(self, account):
        self.account = account
        self.records = [
            {"id": i, "sender": s, "category": c, "unread": u, "at": NOW - m * 60_000, "folder": "inbox"}
            for i, s, c, u, m in FIXTURES
        ]
        self.writes = []

    def capabilities(self):
        return {"send": False, "write": True, "attachments": False, "max_attachment_bytes": 0, "reason": ""}

    def list(self, limit, folder="inbox"):
        return [
            {
                "provider_id": r["id"],
                "headers": {
                    "subject": f"Fixture {r['id']}",
                    "from": f"{r['sender']} <{r['id']}@example.test>",
                },
                "internal_date": r["at"],
                "category": r["category"],
                "label_ids": ["UNREAD"] if r["unread"] else [],
            }
            for r in self.records
            if r["folder"] == folder
        ][:limit], False

    def detail(self, provider_id):
        return {"body_text": "Synthetic body.", "headers": {}, "attachments": []}

    def mutate(self, provider_id, action):
        self.writes.append((provider_id, action))
        record = next(r for r in self.records if r["id"] == provider_id)
        if action in ("archive", "trash"):
            record["folder"] = action
        elif action == "restore":
            record["folder"] = "inbox"


def rows_in_order(page):
    return page.locator("#messageList > *").evaluate_all(
        "els => els.map(e => e.dataset.bundle ? 'bundle:' + e.dataset.bundle : e.dataset.messageId ? 'row' : e.className)"
    )


def main() -> int:
    out = Path(tempfile.mkdtemp(prefix="email-town-bundles-ui-"))
    with sync_playwright() as playwright:
        for engine in ("webkit", "chromium"):
            root = out / engine
            paths = Paths.default(root / "home").ensure()
            paths.config_file.write_text('[model]\nref = "rules"\n')  # offline
            manager = Workbench(root / "workbench", paths, reader_factory=BundleMailbox, undo_seconds=1.5)
            manager.accounts.add(
                "Fixture", "imap", {"host": "fixture", "username": "f@example.test", "port": 993}
            )
            manager._refresh()
            mailbox = next(iter(manager.readers.values()))
            server = LocalServer(manager)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            browser = getattr(playwright, engine).launch()
            try:
                for theme, width in (("light", 1440), ("dark", 390)):
                    context = browser.new_context(
                        viewport={"width": width, "height": 900}, color_scheme=theme
                    )
                    page = context.new_page()
                    errors = []
                    page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
                    page.goto(server.launch_url)

                    # ─── Inbox: two bundles in date order between the rows that need you ───
                    expect(page.locator(".bundle-row")).to_have_count(2)
                    expect(page.locator(".message-row")).to_have_count(2)
                    expect(page.locator("#selectionCount")).to_have_text("2 messages · 4 in bundles")
                    assert rows_in_order(page) == ["row", "bundle:newsletters", "bundle:orders", "row"], (
                        rows_in_order(page)
                    )
                    news = page.locator('.bundle-row[data-bundle="newsletters"]')
                    expect(news.locator(".bundle-name")).to_have_text("Newsletters")
                    expect(news.locator(".bundle-count")).to_have_text("3 messages · 2 unread")
                    expect(news.locator(".bundle-senders")).to_have_text("Weekly Digest, Product Notes")
                    expect(news).to_have_class("bundle-row is-unread")
                    expect(page.locator('.bundle-row[data-bundle="orders"] .bundle-count')).to_have_text(
                        "1 message"
                    )
                    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth"), (
                        engine,
                        width,
                    )
                    page.screenshot(path=str(out / f"{engine}-{theme}-{width}-inbox.png"))

                    # ─── j/k move between messages and step over the bundles ───
                    if width >= 1200:
                        page.locator("body").click(position={"x": 5, "y": 5})
                        page.keyboard.press("j")
                        expect(page.locator("#previewSubject")).to_have_text("Fixture a1")
                        page.keyboard.press("j")
                        expect(page.locator("#previewSubject")).to_have_text("Fixture a2")
                        page.keyboard.press("k")
                        expect(page.locator("#previewSubject")).to_have_text("Fixture a1")

                    # ─── Search, a category, another folder: one by one ───
                    page.locator("#searchInput").fill("Fixture")
                    expect(page.locator(".bundle-row")).to_have_count(0)
                    expect(page.locator(".message-row")).to_have_count(6)
                    page.locator("#searchInput").fill("")
                    expect(page.locator(".bundle-row")).to_have_count(2)

                    # ─── Open a bundle; unbundle it; bundle it again ───
                    news.locator(".bundle-senders").click()
                    expect(page.locator(".message-row")).to_have_count(3)
                    expect(page.locator(".bundle-row")).to_have_count(0)
                    toggle = page.locator("#bundleToggle")
                    expect(page.locator("#bundleBarText")).to_have_text("Shown as one row in your Inbox.")
                    expect(toggle).to_have_text("Show one by one")
                    page.screenshot(path=str(out / f"{engine}-{theme}-{width}-open-bundle.png"))
                    toggle.click()
                    expect(page.locator("#toast")).to_contain_text("Newsletters will show one by one")
                    assert "newsletters" not in manager.prefs["bundled"]
                    expect(toggle).to_have_text("Bundle them")
                    page.locator("#filterChips button").first.click()  # back to the whole Inbox
                    expect(page.locator(".bundle-row")).to_have_count(1)
                    expect(page.locator(".message-row")).to_have_count(5)
                    page.reload()  # the choice is the server's, not the page's
                    expect(page.locator(".bundle-row")).to_have_count(1)
                    if page.locator("#sidebarToggle").is_visible():
                        page.locator("#sidebarToggle").click()
                    page.locator('[data-category-filter="newsletters"]').click()
                    expect(toggle).to_have_text("Bundle them")
                    toggle.click()
                    expect(toggle).to_have_text("Show one by one")
                    assert "newsletters" in manager.prefs["bundled"]
                    page.locator("#filterChips button").first.click()
                    expect(page.locator(".bundle-row")).to_have_count(2)

                    # ─── Archive all, Undo: nothing reaches the mailbox ───
                    news.locator(".bundle-archive").click()
                    expect(page.locator(".bundle-row")).to_have_count(1)
                    expect(page.locator("#toast")).to_contain_text("Archived 3 messages")
                    page.locator("#toast button").click()
                    expect(page.locator("#toast")).to_contain_text("Undone")
                    expect(page.locator(".bundle-row")).to_have_count(2)
                    page.wait_for_timeout(manager.undo_seconds * 1000 + 500)
                    assert mailbox.writes == [], mailbox.writes
                    assert not errors, errors
                    context.close()

                # ─── Archive all for real: exactly the bundle's three messages ───
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                page.goto(server.launch_url)
                page.locator('.bundle-row[data-bundle="newsletters"] .bundle-archive').click()
                deadline = time.monotonic() + 8
                while len(mailbox.writes) < 3 and time.monotonic() < deadline:
                    page.wait_for_timeout(50)
                assert sorted(mailbox.writes) == [("n1", "archive"), ("n2", "archive"), ("n3", "archive")], (
                    mailbox.writes
                )
                expect(page.locator(".bundle-row")).to_have_count(1)
                expect(page.locator(".message-row")).to_have_count(2)
                page.locator('[data-mail-folder="archive"]').click()
                expect(page.locator(".bundle-row")).to_have_count(0)  # bundles are an Inbox view only
            finally:
                browser.close()
                server.shutdown()
                server.server_close()
                manager.close()
    print(
        f"bundles UI passed: grouping, order, open, unbundle/rebundle, search/folder, Archive all + Undo. Captures: {out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
