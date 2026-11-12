"""Pending Inbox, Completed, composable scopes, and factual classification counts.

Run: uv run --with playwright python scripts/check_completion_ui.py
Set EMAIL_TOWN_UI_CAPTURE_DIR to retain captures in a chosen directory.
Uses two synthetic accounts and the real local server in WebKit and Chromium.
"""

from __future__ import annotations

import os
import re
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

TODAY = int(time.time() * 1000)
EARLIER = TODAY - 3 * 86_400_000


class ScopedMailbox(FixtureReader):
    def __init__(self, account):
        super().__init__(account)
        prefix = "alpha" if account["name"] == "Alpha fixture" else "beta"
        self.address = f"{prefix}@example.test"
        self.records = [
            self.message("1", "Today updates", "updates", ["UNREAD"], TODAY),
            self.message("2", "Corrected order", "orders", [], TODAY),
            self.message("3", "Earlier completed", "updates", ["UNREAD"], EARLIER),
            self.message("4", "Awaiting classification", None, ["UNREAD"], TODAY),
        ]
        if prefix == "beta":
            self.records = [
                self.message("1", "Other account updates", "updates", ["UNREAD"], TODAY),
                self.message("2", "Other account earlier", "orders", [], EARLIER),
            ]

    @staticmethod
    def message(number, subject, category, labels, at):
        return {
            "id": f"m{number}",
            "subject": f"{subject} (synthetic)",
            "folder": "inbox",
            "labels": labels,
            "category": category,
            "at": at,
        }

    def list(self, limit, folder="inbox"):
        rows = [r for r in self.records if r["folder"] == folder]
        return [
            {
                "provider_id": r["id"],
                "headers": {"subject": r["subject"], "from": "Sender <sender@example.test>"},
                "internal_date": r["at"],
                "category": r.get("category"),
                "label_ids": list(r["labels"]),
            }
            for r in rows[:limit]
        ], False


def capture_directory() -> Path:
    out = Path(
        os.environ.get("EMAIL_TOWN_UI_CAPTURE_DIR", tempfile.mkdtemp(prefix="email-town-completion-ui-"))
    )
    out.mkdir(parents=True, exist_ok=True)
    return out


def workbench(root: Path) -> tuple[Workbench, dict[str, ScopedMailbox]]:
    paths = Paths.default(root / "home").ensure()
    paths.config_file.write_text('[model]\nref = "rules"\n')
    readers = {}

    def factory(account):
        reader = ScopedMailbox(account)
        readers[account["name"]] = reader
        return reader

    manager = Workbench(root / "workbench", paths, reader_factory=factory)
    manager.demo = True
    for name in ("Alpha fixture", "Beta fixture"):
        manager.accounts.add(name, "gmail", {"config_dir": str(root / name)})
    manager._refresh()
    pending = next(
        row for row in manager.messages.values() if row["subject"] == "Awaiting classification (synthetic)"
    )
    pending["source"] = "pending"
    rows = {row["subject"]: row["id"] for row in manager.snapshot()["messages"]}
    manager.action({"ids": [rows["Earlier completed (synthetic)"]], "action": "done"})
    manager.action({"ids": [rows["Corrected order (synthetic)"]], "action": "category", "category": "travel"})
    return manager, readers


def main() -> int:
    out = capture_directory()
    with sync_playwright() as playwright:
        for engine in ("webkit", "chromium"):
            state_root = Path(tempfile.mkdtemp(prefix=f"email-town-completion-{engine}-"))
            manager, readers = workbench(state_root)
            server = LocalServer(manager)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            browser = getattr(playwright, engine).launch()
            try:
                page = browser.new_page(viewport={"width": 1440, "height": 960})
                until.page = page
                errors = []
                page.on("pageerror", lambda error, errors=errors: errors.append(str(error)))
                page.on("dialog", lambda dialog: dialog.accept())
                page.goto(server.launch_url)
                rows = page.locator(".message-row")
                expect(rows).to_have_count(5)
                page.screenshot(path=str(out / f"{engine}-inbox.png"))

                expect(page.locator('[data-done-filter="pending"]')).to_have_attribute("aria-pressed", "true")
                expect(rows).to_have_count(5)
                expect(page.locator("#listTitle")).to_have_text("Inbox")
                expect(page.locator("#listContext")).to_contain_text("Pending")
                assert "Earlier completed" not in rows.all_inner_texts()
                expect(page.locator("#countAll")).to_have_text("5")
                expect(page.locator("#unreadAll")).to_have_text("3 unread")
                expect(page.locator("#countToday")).to_have_text("4")
                expect(page.locator("#countBacklog")).to_have_text("1")

                alpha = page.locator("[data-account-filter]").filter(has_text="Alpha fixture")
                beta = page.locator("[data-account-filter]").filter(has_text="Beta fixture")
                expect(alpha.locator(".count")).to_have_text("3")
                expect(alpha.locator(".unread-count")).to_have_text("2 unread")
                expect(beta.locator(".count")).to_have_text("2")
                expect(beta.locator(".unread-count")).to_have_text("1 unread")
                assert "3 loaded" in alpha.get_attribute("aria-label")
                assert "2 unread" in alpha.get_attribute("aria-label")

                updates = page.locator('[data-category-filter="updates"]')
                expect(updates.locator(".count")).to_have_text("2")
                expect(updates.locator(".unread-count")).to_have_text("2 unread")
                assert "automatic" in updates.get_attribute("aria-label")
                assert "correction" in updates.get_attribute("aria-label")
                assert "wrong" not in page.locator("#categoryFilters").inner_text().lower()
                unknown = page.locator('[data-category-filter="unknown"]')
                expect(unknown.locator(".awaiting-count")).to_have_text("1 awaiting classification")

                alpha.click()
                expect(rows).to_have_count(3)
                updates.click()
                expect(rows).to_have_count(1)
                expect(rows.locator(".category-badge")).to_have_text("Updates")
                page.locator('[data-done-filter="done"]').click()
                expect(rows).to_have_count(1)
                expect(page.locator("#listTitle")).to_have_text("Completed")
                expect(rows.locator(".category-badge")).to_have_text("Updates")
                context = page.locator("#listContext").inner_text()
                for scope in ("Alpha fixture", "Inbox", "Updates", "Completed"):
                    assert scope in context, context
                page.screenshot(path=str(out / f"{engine}-completed.png"))
                page.locator('[data-type-filter="today"]').click()
                expect(rows).to_have_count(0)
                expect(page.locator(".empty-state")).to_contain_text("No messages match")
                page.locator("#searchInput").fill("no synthetic match")
                expect(page.locator("#listContext")).to_contain_text("no synthetic match")

                page.locator("#clearFiltersButton").click()
                expect(rows).to_have_count(5)
                expect(page.locator('[data-done-filter="pending"]')).to_have_attribute("aria-pressed", "true")
                expect(page.locator('[data-account-filter="all"]')).to_have_attribute("aria-pressed", "true")
                expect(page.locator('[data-category-filter="all"]')).to_have_attribute("aria-pressed", "true")
                expect(page.locator("#searchInput")).to_have_value("")
                account_ids = {account["name"]: account["id"] for account in manager.accounts.all()}

                def check_reply_sender(subject, account_name, page=page, rows=rows, account_ids=account_ids):
                    rows.filter(has_text=subject).locator(".row-open").click()
                    page.locator('[data-compose-mode="reply"]').click()
                    expect(page.locator("#composeAccount")).to_have_value(account_ids[account_name])
                    expect(page.locator("#composeAccount")).to_be_disabled()
                    expect(page.locator("#composeTo")).to_have_value("sender@example.test")
                    page.locator("#composeDialog [data-compose-close]").first.click()
                    expect(page.locator("#composeDialog")).to_be_hidden()

                check_reply_sender("Today updates", "Alpha fixture")

                # Opening changes read state only. Done/reopen itself must not touch provider fields.
                target = rows.filter(has_text="Today updates")
                target.locator(".row-open").click()
                expect(target).not_to_have_class(re.compile(r"\bis-unread\b"))
                expect(page.locator("#unreadAll")).to_have_text("2 unread")
                record = readers["Alpha fixture"].record("m1")
                until(
                    lambda record=record: "UNREAD" not in record["labels"],
                    "open-to-read provider write settles",
                )
                before = (record["folder"], list(record["labels"]), record["category"])
                page.locator("#previewMore > summary").click()
                page.locator("#previewDoneButton").click()
                expect(rows).to_have_count(4)
                expect(target).to_have_count(0)
                assert before == (record["folder"], list(record["labels"]), record["category"])
                page.locator('[data-done-filter="done"]').click()
                completed = rows.filter(has_text="Today updates")
                expect(completed).to_have_count(1)
                expect(completed.locator(".category-badge")).to_have_text("Updates")
                completed.locator(".row-open").click()
                page.locator("#previewMore > summary").click()
                page.locator("#previewDoneButton").click()
                expect(completed).to_have_count(0)
                assert before == (record["folder"], list(record["labels"]), record["category"])
                page.locator('[data-done-filter="pending"]').click()
                expect(rows.filter(has_text="Today updates")).to_have_count(1)
                page.locator('[data-done-filter="all"]').click()
                expect(rows).to_have_count(6)
                check_reply_sender("Other account updates", "Beta fixture")
                page.screenshot(path=str(out / f"{engine}-all-scopes.png"))

                # Finishing every pending row leads to a useful empty state, not a dead-end Clear action.
                page.locator("#clearFiltersButton").click()
                page.locator("#selectAll").check()
                page.locator("#markDoneButton").click()
                expect(rows).to_have_count(0)
                expect(page.locator(".empty-state h3")).to_have_text("No pending mail")
                expect(page.get_by_role("button", name="View completed", exact=True)).to_be_visible()
                page.screenshot(path=str(out / f"{engine}-empty-pending.png"))
                page.get_by_role("button", name="View completed", exact=True).click()
                expect(page.locator("#messageList")).to_be_focused()
                expect(rows).to_have_count(6)
                expect(page.locator('[data-done-filter="done"]')).to_have_attribute("aria-pressed", "true")
                expect(rows.first.locator(".category-badge")).to_have_text("Updates")
                rows.filter(has_text="Today updates").locator(".row-open").click()
                page.locator("#previewMore > summary").click()
                page.locator("#previewDoneButton").click()
                expect(rows).to_have_count(5)
                page.locator('[data-done-filter="pending"]').click()
                expect(rows.filter(has_text="Today updates")).to_have_count(1)

                # A configured but empty folder must not suggest adding another account.
                page.locator('[data-mail-folder="sent"]').click()
                expect(rows).to_have_count(0)
                expect(page.locator(".empty-state h3")).to_have_text("No mail in Sent")
                assert "Add an account" not in page.locator(".empty-state").inner_text()
                page.locator('[data-mail-folder="inbox"]').click()
                expect(rows.filter(has_text="Today updates")).to_have_count(1)

                # Exercise failure/paused UI through the real state endpoint; provider failure
                # isolation itself is covered by the manager tests. Healthy-account mail remains.
                original_snapshot = manager.snapshot
                unavailable = {"paused": False}
                beta_id = account_ids["Beta fixture"]

                def unavailable_snapshot(original=original_snapshot, mode=unavailable, account_id=beta_id):
                    data = original()
                    data["messages"] = [m for m in data["messages"] if m["account_id"] != account_id]
                    for account in data["accounts"]:
                        if account["id"] == account_id:
                            account["enabled"] = not mode["paused"]
                            account["status"] = "error"  # A paused account may retain its last error.
                    data["etag"] += ":fixture-paused" if mode["paused"] else ":fixture-error"
                    return data

                manager.snapshot = unavailable_snapshot
                try:
                    page.locator(f'[data-account-filter="{beta_id}"]').click()
                    manager._touch()
                    expect(page.locator(".empty-state h3")).to_have_text("Mail could not be loaded")
                    page.locator('[data-account-filter="all"]').click()
                    expect(rows.filter(has_text="Today updates")).to_have_count(1)
                    page.locator(f'[data-account-filter="{beta_id}"]').click()
                    unavailable["paused"] = True
                    manager._touch()
                    expect(page.locator(".empty-state h3")).to_have_text("Accounts in this view are paused")
                finally:
                    manager.snapshot = original_snapshot
                assert not errors, errors
                print(f"{engine}: pending, Completed, composed scopes, unread and classification counts pass")
            finally:
                browser.close()
                server.shutdown()
                server.server_close()
                manager.close()
    print(f"completion UI passed. Captures: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
