"""Unread only: one toggle beside the date range lists just the unread mail in whatever view is open.

Run: uv run --no-project --with-editable '.[dev]' --with playwright python scripts/check_unread_filter_ui.py

- In Updates (a category bundled in the Inbox), Unread lists only its unread messages; the list context,
  the clear button and the toggle's pressed state say so;
- a message opened there is read after 1.5 s (written to the mailbox) and stays listed, read, until the
  view changes, as in Gmail and Outlook; leaving and coming back lists only what is still unread;
- with nothing unread left, the empty state says so instead of "No messages match these filters";
- in the whole Inbox with Unread on, a bundle counts and holds only its unread mail, and a bundle with
  none left is not shown; Clear search and filters turns Unread off;
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
    ("u1", "Build Bot", "updates", True, 2),
    ("u2", "Status Page", "updates", False, 3),
    ("u3", "Security Alerts", "updates", True, 4),
    ("u4", "Build Bot", "updates", False, 5),
    ("n1", "Weekly Digest", "newsletters", False, 6),
]


class UnreadMailbox:
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
        if action in ("read", "unread"):
            record["unread"] = action == "unread"


def listed(page):
    return page.locator("#messageList > *").evaluate_all(
        "els => els.map(e => e.dataset.bundle ? 'bundle:' + e.dataset.bundle"
        " : e.dataset.messageId ? e.querySelector('.row-subject').textContent.replace('Fixture ', '') : e.className)"
    )


def choose_category(page, category):
    if page.locator("#sidebarToggle").is_visible():
        page.locator("#sidebarToggle").click()
    page.locator(f'[data-category-filter="{category}"]').click()


def wait_for_write(page, mailbox, write, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if write in mailbox.writes:
            return
        page.wait_for_timeout(50)
    raise AssertionError(f"timed out waiting for {write} in the mailbox (saw {mailbox.writes})")


def main() -> int:
    out = Path(tempfile.mkdtemp(prefix="email-town-unread-filter-ui-"))
    with sync_playwright() as playwright:
        for engine in ("webkit", "chromium"):
            browser = getattr(playwright, engine).launch()
            try:
                for theme, width in (("light", 1440), ("dark", 390)):
                    # A fresh mailbox per run: each run reads mail.
                    root = out / f"{engine}-{width}"
                    paths = Paths.default(root / "home").ensure()
                    paths.config_file.write_text('[model]\nref = "rules"\n')  # offline
                    manager = Workbench(root / "workbench", paths, reader_factory=UnreadMailbox)
                    manager.accounts.add(
                        "Fixture", "imap", {"host": "fixture", "username": "f@example.test", "port": 993}
                    )
                    manager._refresh()
                    mailbox = next(iter(manager.readers.values()))
                    server = LocalServer(manager)
                    threading.Thread(target=server.serve_forever, daemon=True).start()
                    context = browser.new_context(
                        viewport={"width": width, "height": 900}, color_scheme=theme
                    )
                    page = context.new_page()
                    errors = []
                    page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
                    page.goto(server.launch_url)
                    toggle = page.locator("#unreadFilterButton")
                    expect(page.locator(".bundle-row")).to_have_count(2)
                    expect(toggle).to_have_attribute("aria-pressed", "false")

                    # ─── Updates, then Unread: only its two unread messages ───
                    choose_category(page, "updates")
                    expect(page.locator(".message-row")).to_have_count(4)
                    toggle.click()
                    expect(toggle).to_have_attribute("aria-pressed", "true")
                    expect(toggle).to_have_class("toggle-filter is-active")
                    assert listed(page) == ["u1", "u3"], listed(page)
                    expect(page.locator("#listContext")).to_contain_text("Updates / Unread")
                    expect(page.locator("#clearFiltersButton")).to_be_visible()
                    expect(page.locator("#selectionCount")).to_have_text("2 messages")
                    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth"), (
                        engine,
                        width,
                    )
                    page.wait_for_timeout(400)  # a phone's folder pane finishes closing
                    page.screenshot(path=str(out / f"{engine}-{theme}-{width}-updates-unread.png"))

                    if width < 1200:  # the narrow layout: the toggle, its list and no overflow
                        assert not errors, errors
                        context.close()
                        server.shutdown()
                        server.server_close()
                        manager.close()
                        continue

                    # ─── Open one: it is read, written to the mailbox, and stays listed for now ───
                    page.locator(".message-row .row-subject", has_text="Fixture u1").click()
                    expect(page.locator("#previewSubject")).to_have_text("Fixture u1")
                    wait_for_write(page, mailbox, ("u1", "read"))
                    expect(page.locator(".message-row", has_text="Fixture u1")).not_to_have_class(
                        "message-row is-unread"
                    )
                    page.wait_for_timeout(1500)  # later state answers must not drop it either
                    assert listed(page) == ["u1", "u3"], listed(page)

                    # ─── Leave and come back: only what is still unread ───
                    page.locator("#filterChips button").first.click()  # the Updates chip
                    expect(page.locator('.bundle-row[data-bundle="updates"] .bundle-count')).to_have_text(
                        "1 message · 1 unread"
                    )
                    choose_category(page, "updates")
                    expect(toggle).to_have_attribute("aria-pressed", "true")
                    assert listed(page) == ["u3"], listed(page)

                    # ─── Nothing unread left: say so ───
                    page.locator(".message-row .row-subject", has_text="Fixture u3").click()
                    wait_for_write(page, mailbox, ("u3", "read"))
                    page.wait_for_timeout(300)
                    assert listed(page) == ["u3"], listed(page)
                    toggle.click()
                    toggle.click()  # off and on again: a fresh view
                    expect(page.locator(".empty-state h3")).to_have_text("No unread mail here")
                    page.screenshot(path=str(out / f"{engine}-{theme}-{width}-none-unread.png"))

                    # ─── The whole Inbox with Unread on: bundles hold only unread mail ───
                    page.locator("#filterChips button").first.click()
                    expect(page.locator(".bundle-row")).to_have_count(0)  # Updates and Newsletters: all read
                    assert listed(page) == ["a1"], listed(page)
                    page.locator("#clearFiltersButton").click()
                    expect(toggle).to_have_attribute("aria-pressed", "false")
                    expect(page.locator(".bundle-row")).to_have_count(2)
                    expect(page.locator('.bundle-row[data-bundle="updates"] .bundle-count')).to_have_text(
                        "4 messages"
                    )
                    assert not errors, errors
                    context.close()
                    server.shutdown()
                    server.server_close()
                    manager.close()
                print(f"{engine}: ok")
            finally:
                browser.close()
    print(f"captures: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
