"""Decide once per sender: one category choice moves that sender's other and future mail, and Undo takes it back.

Run: uv run --no-project --with-editable '.[dev]' --with playwright python scripts/check_sender_rules_ui.py

- the category dialog offers "Always use this category for mail from <sender>";
- with it, the sender's other message (never selected) and a message that arrives afterwards follow the
  rule: here they move from Action needed into the Newsletters bundle; another sender's mail does not;
- Undo restores the messages and removes the rule;
- the dialog shows the box checked where a rule exists, and unchecking it removes the rule;
- workflow.sqlite never holds the sender's address;
- 1440 light and 390 dark, no horizontal overflow; captures to look at.
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


class Mailbox:
    def __init__(self, account):
        self.account = account
        self.records = [  # (id, From, category, minutes ago)
            ("t1", "Teammate <teammate@example.test>", "action_needed", 1),
            ("d1", "Weekly Digest <digest@example.test>", "action_needed", 2),
            ("d2", "Weekly Digest <digest@example.test>", "action_needed", 3),
            ("o1", "Fixture Shop <shop@example.test>", "orders", 4),
        ]

    def capabilities(self):
        return {"send": False, "write": False, "attachments": False, "max_attachment_bytes": 0, "reason": ""}

    def list(self, limit, folder="inbox"):
        if folder != "inbox":
            return [], False
        return [
            {
                "provider_id": i,
                "headers": {"subject": f"Fixture {i}", "from": sender},
                "internal_date": NOW - minutes * 60_000,
                "category": category,
                "label_ids": [],
            }
            for i, sender, category, minutes in self.records
        ][:limit], False

    def detail(self, provider_id):
        return {"body_text": "Synthetic body.", "headers": {}, "attachments": []}


def choose_category(page, category, *, always):
    if not page.locator("#previewMore").evaluate("el => el.open"):
        page.locator("#previewMore > summary").click()
    page.locator("#previewCategoryButton").click()
    page.locator("#categorySelect").select_option(category)
    check = page.locator("#senderRuleCheck")
    expect(page.locator("#senderRuleRow")).to_be_visible()
    if check.is_checked() != always:
        check.click()
    page.locator("#categoryForm button[type=submit]").click()


def open_row(page, subject):
    page.locator(".message-row").filter(has_text=subject).locator(".row-open").click()
    expect(page.locator("#previewSubject")).to_have_text(subject)


def main() -> int:
    out = Path(tempfile.mkdtemp(prefix="email-town-sender-rules-ui-"))
    with sync_playwright() as playwright:
        for engine in ("webkit", "chromium"):
            for theme, width in (("light", 1440), ("dark", 390)):
                root = out / f"{engine}-{width}"
                paths = Paths.default(root / "home").ensure()
                paths.config_file.write_text('[model]\nref = "rules"\n')  # offline
                manager = Workbench(root / "workbench", paths, reader_factory=Mailbox)
                manager.accounts.add(
                    "Fixture", "imap", {"host": "fixture", "username": "f@example.test", "port": 993}
                )
                manager._refresh()
                mailbox = next(iter(manager.readers.values()))
                server = LocalServer(manager)
                threading.Thread(target=server.serve_forever, daemon=True).start()
                browser = getattr(playwright, engine).launch()
                try:
                    context = browser.new_context(
                        viewport={"width": width, "height": 900}, color_scheme=theme
                    )
                    page = context.new_page()
                    errors = []
                    page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
                    page.goto(server.launch_url)
                    rows = page.locator(".message-row")
                    expect(rows).to_have_count(3)  # Teammate and two Digest rows; Orders is a bundle

                    # ─── Decide once: Newsletters, always, for Weekly Digest ───
                    open_row(page, "Fixture d1")
                    if not page.locator("#previewMore").evaluate("el => el.open"):
                        page.locator("#previewMore > summary").click()
                    page.locator("#previewCategoryButton").click()
                    expect(page.locator("#senderRuleText")).to_have_text(
                        "Always use this category for mail from Weekly Digest"
                    )
                    expect(page.locator("#senderRuleCheck")).not_to_be_checked()
                    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
                    page.screenshot(path=str(out / f"{engine}-{theme}-{width}-dialog.png"))
                    page.keyboard.press("Escape")
                    choose_category(page, "newsletters", always=True)
                    expect(page.locator("#toast")).to_contain_text(
                        "Newsletters for every message from this sender"
                    )
                    if width < 1200:
                        page.locator("#previewClose").click()
                    news = page.locator('.bundle-row[data-bundle="newsletters"]')
                    expect(news.locator(".bundle-count")).to_have_text("2 messages")  # d2 was never selected
                    expect(rows).to_have_count(1)  # Teammate stays: another sender
                    toast_undo = page.locator("#toast button")

                    # ─── Mail that arrives later follows the rule ───
                    mailbox.records.append(("d3", "Weekly Digest <digest@example.test>", "action_needed", 0))
                    manager._refresh()
                    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
                    expect(news.locator(".bundle-count")).to_have_text("3 messages")
                    expect(rows).to_have_count(1)
                    page.screenshot(path=str(out / f"{engine}-{theme}-{width}-after-rule.png"))
                    stored = (root / "workbench" / "workflow.sqlite").read_bytes().lower()
                    assert b"digest@" not in stored and b"example.test" not in stored, "an address was stored"

                    # ─── Undo: the messages and the rule come back as they were ───
                    toast_undo.click()
                    expect(page.locator("#toast")).to_contain_text("Change undone")
                    expect(rows).to_have_count(4)  # Teammate and d1, d2, d3 in Action needed again
                    expect(news).to_have_count(0)
                    assert manager.sender_rules == {}

                    # ─── Where a rule exists the box is checked; unchecking removes it ───
                    open_row(page, "Fixture d2")
                    choose_category(page, "newsletters", always=True)
                    expect(news.locator(".bundle-count")).to_have_text("3 messages")
                    if width < 1200 and page.locator("#previewClose").is_visible():
                        page.locator("#previewClose").click()
                    news.locator(".bundle-senders").click()
                    open_row(page, "Fixture d3")
                    if not page.locator("#previewMore").evaluate("el => el.open"):
                        page.locator("#previewMore > summary").click()
                    page.locator("#previewCategoryButton").click()
                    expect(page.locator("#senderRuleCheck")).to_be_checked()
                    page.locator("#senderRuleCheck").click()
                    page.locator("#categoryForm button[type=submit]").click()
                    expect(page.locator("#toast")).to_contain_text("Rule removed for this sender")
                    assert manager.sender_rules == {}
                    assert not errors, errors
                    context.close()
                finally:
                    browser.close()
                    server.shutdown()
                    server.server_close()
                    manager.close()
    print(
        f"sender rules UI passed: decide once, future mail follows, Undo, checked/uncheck removes. Captures: {out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
