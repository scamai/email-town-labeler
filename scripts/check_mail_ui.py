"""Real local-server mail workbench fixture checks (synthetic provider only).

Run: uv run --with playwright python scripts/check_mail_ui.py
WebKit: EMAIL_TOWN_UI_ENGINE=webkit uv run --with playwright python scripts/check_mail_ui.py
"""

from __future__ import annotations

import base64
import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from email_town_labeler.config import Paths
from email_town_labeler.desktop.manager import Workbench
from email_town_labeler.desktop.server import LocalServer


class FixtureReader:
    def __init__(self, account):
        self.address = account["settings"].get("username", "fixture@example.test")
        self.records = [
            {
                "provider_id": "ok",
                "subject": "Fixture received",
                "from": "Sender <sender@example.test>",
                "folder": "inbox",
                "label_ids": ["UNREAD"],
            },
            {
                "provider_id": "fail",
                "subject": "Fixture partial failure",
                "from": "Other <other@example.test>",
                "folder": "inbox",
                "label_ids": ["UNREAD"],
            },
        ]
        self.sent = []
        self.unknown_next = False
        # Fixed, distinct dates: "ok" is always the first row. Both used to read the clock separately, and
        # a millisecond tick between them put "fail" first, so ".row-open" opened the wrong message.
        self.base = int(time.time() * 1000)
        self.dates = {"ok": self.base, "fail": self.base - 60_000}

    def capabilities(self):
        return {
            "send": True,
            "write": True,
            "attachments": True,
            "max_attachment_bytes": 1024 * 1024,
            "reason": "",
        }

    def list(self, limit, folder="inbox"):
        rows = [r for r in self.records if r["folder"] == folder]
        return [
            {
                "provider_id": r["provider_id"],
                "headers": {
                    "subject": r["subject"],
                    "from": r["from"],
                    "to": "fixture@example.test",
                    "cc": "copy@example.test",
                    "reply-to": '"intruder@outside.test" <reply@example.test>',
                },
                "internal_date": self.dates.get(r["provider_id"], self.base - 120_000),
                "category": "updates",
                "label_ids": r["label_ids"],
            }
            for r in rows[:limit]
        ], False

    def detail(self, provider_id):
        return {
            "body_text": "Synthetic body for UI testing.",
            "headers": {
                "from": "Sender <sender@example.test>",
                "to": "fixture@example.test",
                "cc": "copy@example.test",
                "reply-to": '"intruder@outside.test" <reply@example.test>',
                "message-id": "<fixture@example.test>",
                "references": "<root@example.test>",
            },
            "attachments": [{"id": "att1", "name": "fixture.txt", "size": 13, "type": "text/plain"}],
            "thread_id": "thread-fixture",
        }

    def attachment(self, provider_id, attachment_id):
        assert attachment_id == "att1"
        return ({"name": "fixture.txt", "type": "text/plain"}, b"fixture bytes")

    def mutate(self, provider_id, action):
        if provider_id == "fail":
            raise RuntimeError("fixture partial failure")
        record = next(r for r in self.records if r["provider_id"] == provider_id)
        if action in ("trash", "archive"):
            record["folder"] = action
        elif action == "restore":
            record["folder"] = "inbox"
        elif action == "read":
            record["label_ids"] = []
        elif action == "unread":
            record["label_ids"] = ["UNREAD"]
        elif action == "star":
            record["label_ids"] = ["STARRED"]

    def send(self, data, original=None):
        self.sent.append(data)
        if self.unknown_next:
            self.unknown_next = False
            return {"status": "unknown", "accepted": [], "refused": [], "message": "Fixture lost response"}
        return {
            "status": "complete",
            "accepted": ["fixture@example.test"],
            "refused": [],
            "message": "Fixture accepted",
        }


def main():
    out = Path(tempfile.mkdtemp(prefix="email-town-mail-ui-"))
    paths = Paths.default(out / "home").ensure()
    # Offline and deterministic: keyword rules, never the desktop default model's 263 MB download.
    paths.config_file.write_text('[model]\nref = "rules"\n')
    readers = []

    def factory(account):
        reader = FixtureReader(account)
        readers.append(reader)
        return reader

    manager = Workbench(out / "workbench", paths, reader_factory=factory)
    manager.demo = True
    manager.accounts.add(
        "Fixture", "imap", {"host": "fixture", "username": "fixture@example.test", "port": 993}
    )
    manager._refresh()
    server = LocalServer(manager)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            engine = os.environ.get("EMAIL_TOWN_UI_ENGINE", "chromium")
            browser = getattr(playwright, engine).launch()
            for theme, width in (("light", 390), ("dark", 390), ("light", 1440), ("dark", 1440)):
                if readers:
                    for record in readers[-1].records:
                        record["folder"] = "inbox"
                        record["label_ids"] = ["UNREAD"]
                    manager.folder = "inbox"
                    manager.messages.clear()
                    manager._refresh()
                context = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=theme)
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error, errors=errors: errors.append(str(error)))
                page.on("dialog", lambda dialog: dialog.accept())
                page.goto(server.launch_url)
                expect(page.locator(".message-row")).to_have_count(2)
                # Observe the list AND the open preview after a real mailbox action.
                row = page.locator(".message-row").filter(has_text="Fixture received")
                row.locator(".row-open").click()
                # Opening reads it, as in Gmail: row and preview lose Unread and the mailbox gets the write.
                expect(row).not_to_have_class(re.compile(r"\bis-unread\b"))
                expect(page.locator("#previewUnread")).to_be_hidden()
                ok_record = next(r for r in readers[-1].records if r["provider_id"] == "ok")
                deadline = time.monotonic() + 5
                while "UNREAD" in ok_record["label_ids"] and time.monotonic() < deadline:
                    time.sleep(0.05)
                assert "UNREAD" not in ok_record["label_ids"], "opening did not mark the message read"
                expect(page.locator("[data-single-action='unread']")).to_be_enabled()
                page.locator("#previewMore > summary").click()
                page.locator("[data-single-action='unread']").click()
                expect(row).to_have_class(re.compile(r"\bis-unread\b"))
                expect(page.locator("#previewUnread")).to_be_visible()
                page.wait_for_timeout(1500)  # marked unread while open: it stays unread until opened again
                expect(row).to_have_class(re.compile(r"\bis-unread\b"))
                if not page.locator("#previewMore").evaluate("el => el.open"):
                    page.locator("#previewMore > summary").click()
                page.locator("[data-single-action='read']").click()
                expect(row).not_to_have_class(re.compile(r"\bis-unread\b"))
                if not page.locator("#previewMore").evaluate("el => el.open"):
                    page.locator("#previewMore > summary").click()
                page.locator("[data-single-action='unread']").click()
                expect(row).to_have_class(re.compile(r"\bis-unread\b"))
                expect(page.locator("#previewUnread")).to_be_visible()
                if width == 390:
                    page.locator("#previewClose").click()
                prior_drafts = len(manager.mail_store.drafts()["drafts"])
                page.locator("#composeButton").click()
                expect(page.locator("#composeDialog")).to_be_visible()
                page.locator("#composeTo").fill("recipient@example.test")
                page.locator("#composeSubject").fill("Fixture send")
                page.locator("#composeBody").fill("Fixture body")
                attachment = out / "fixture.txt"
                attachment.write_bytes(b"fixture bytes")
                page.locator("#composeAttachments").set_input_files(str(attachment))
                expect(page.locator("#attachmentSummary")).to_contain_text("fixture.txt")
                page.screenshot(path=str(out / f"{theme}-{width}-compose.png"))
                assert not page.evaluate(
                    "document.querySelector('#composeDialog').scrollWidth > document.querySelector('#composeDialog').clientWidth"
                )
                page.locator("#saveDraftButton").click()
                expect(page.locator("#toast")).to_contain_text("Draft saved")
                page.locator("#composeDialog [data-compose-close]").first.click()
                expect(page.locator("#composeDialog")).to_be_hidden()
                if page.locator("#sidebarToggle").is_visible():  # Drafts is in the folder pane
                    page.locator("#sidebarToggle").click()
                page.locator("#draftsButton").click()
                expect(page.locator(".draft-item")).to_have_count(prior_drafts + 1)
                page.locator(".draft-item .text-button").first.click()
                expect(page.locator("#composeTo")).to_have_value("recipient@example.test")
                page.locator("#sendButton").click()
                expect(page.locator("#composeDialog")).to_be_hidden(timeout=8000)
                reader = readers[-1]
                assert reader.sent[-1]["to"] == "recipient@example.test"
                assert reader.sent[-1]["cc"] == ""
                assert reader.sent[-1]["bcc"] == ""
                assert reader.sent[-1]["attachments"][0]["name"] == "fixture.txt"
                assert base64.b64decode(reader.sent[-1]["attachments"][0]["data"]) == b"fixture bytes"
                assert len(manager.mail_store.drafts()["drafts"]) == prior_drafts
                page.locator(".row-open").first.click()
                expect(page.locator("[data-compose-mode='reply-all']")).to_be_visible(timeout=5000)
                with page.expect_download() as downloaded:
                    page.locator(".preview-attachments button").first.click()
                assert Path(downloaded.value.path()).read_bytes() == b"fixture bytes"
                page.locator("[data-compose-mode='forward']").click()
                expect(page.locator("#composeTo")).to_have_value("")
                expect(page.locator("#composeSubject")).to_have_value("Fwd: Fixture received")
                page.locator("#composeDialog [data-compose-close]").first.click()
                page.locator("[data-compose-mode='reply-all']").click()
                assert page.locator("#composeTo").input_value() == "reply@example.test"
                expect(page.locator("#composeAccount")).to_be_disabled()
                assert "copy@example.test" in page.locator("#composeCc").input_value()
                page.locator("#composeDialog [data-compose-close]").first.click()
                if width == 390:
                    page.locator("#previewClose").click()
                page.locator("#composeButton").click()
                page.locator("#composeTo").fill("recipient@example.test")
                page.locator("#composeSubject").fill("Unknown")
                reader.unknown_next = True
                page.locator("#sendButton").click()
                expect(page.locator("#sendButton")).to_be_disabled(timeout=8000)
                expect(page.locator("#composeError")).to_contain_text("Fixture lost response", timeout=8000)
                assert page.locator("#composeDialog").is_visible()
                page.locator("#composeBody").fill("New unsent edits after the old request")
                pending = json.loads(page.evaluate("sessionStorage.getItem('email-town-pending-send')"))
                manager.mail_store.finish(
                    pending["request_id"],
                    {
                        "status": "complete",
                        "succeeded": [],
                        "failed": [],
                        "message": "Fixture accepted later",
                    },
                )
                page.get_by_role("button", name="Check send result", exact=True).click()
                expect(page.locator("#composeError")).to_contain_text("current edits are preserved")
                expect(page.locator("#composeBody")).to_have_value("New unsent edits after the old request")
                expect(page.locator("#composeDialog")).to_be_visible()
                page.locator("#composeDialog [data-compose-close]").first.click()
                page.locator("#selectAll").check()
                page.locator("[data-mail-action='trash']").click()
                expect(page.locator("#toast")).to_contain_text("failed", timeout=8000)
                expect(page.locator("#composeTo")).to_be_enabled()
                if page.locator("#sidebarToggle").is_visible():  # folders are in the folder pane
                    page.locator("#sidebarToggle").click()
                page.locator("[data-mail-folder='trash']").click()
                expect(page.locator(".message-row")).to_have_count(1, timeout=8000)
                page.screenshot(path=str(out / f"{theme}-{width}-trash.png"))
                page.locator("#selectAll").check()
                page.locator("[data-mail-action='restore']").click()
                expect(page.locator(".message-row")).to_have_count(0, timeout=8000)
                # A background poll may remove the row before the action receipt arrives.
                expect(page.locator("#composeTo")).to_be_enabled()
                if page.locator("#sidebarToggle").is_visible():  # folders are in the folder pane
                    page.locator("#sidebarToggle").click()
                page.locator("[data-mail-folder='inbox']").click()
                expect(page.locator(".message-row")).to_have_count(2, timeout=8000)
                assert not errors, errors
                context.close()
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        manager.close()
    print(f"mail UI fixture passed ({engine}); captures in {out}")


if __name__ == "__main__":
    main()
