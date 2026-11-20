"""What a category reads as, and that the view never acts on mail it does not show.

Run: uv run --no-project --with-editable '.[dev]' --with playwright python scripts/check_category_label_ui.py

Read where the user sees it:
- mail still waiting for the model reads "Sorting…", your own Sent mail "Not classified" and neither counts
  under People (only mail the model put there does);
- the "Always use this category for mail from …" box is offered for a real category, not for People or
  New sender, which a sender rule cannot choose (choosing one with the box ticked used to save the category
  and then fail);
- choosing a category in the sidebar from the keyboard keeps focus on that entry (it fell to <body>);
- messages selected inside a category are deselected when going back to the bundled Inbox, so Archive or
  Trash cannot act on mail hidden in a bundle.
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
from email_town_labeler.models.base import ModelManifest
from email_town_labeler.types import Prediction

NOW = int(time.time() * 1000)
INBOX = [
    ("person", "Teammate", "Lunch on Friday?"),
    ("news1", "Weekly Digest", "Digest one"),
    ("news2", "Weekly Digest", "Digest two"),
    ("stuck", "Someone New", "Still being sorted"),
]
SENT = [("sent1", "Me", "My own reply")]
CLASSES = {"person": "unknown", "news1": "newsletters", "news2": "newsletters"}


class Model:
    manifest = ModelManifest("fixture", "1", "rules")

    def predict(self, records):
        return [Prediction(CLASSES[r["fixture"]], 0.95, {CLASSES[r["fixture"]]: 0.95}) for r in records]


class Mailbox:
    def __init__(self, account):
        self.account = account

    def capabilities(self):
        return {"send": False, "write": True, "attachments": False, "max_attachment_bytes": 0, "reason": ""}

    def list(self, limit, folder="inbox"):
        rows = {"inbox": INBOX, "sent": SENT}.get(folder, [])
        return [
            {
                "provider_id": i,
                "headers": {"subject": subject, "from": f"{sender} <{i}@example.test>"},
                "internal_date": NOW - n * 60_000,
                "label_ids": [],
            }
            for n, (i, sender, subject) in enumerate(rows)
        ][:limit], False

    def details(self, ids):
        # "stuck" never arrives, so it stays waiting for the model.
        return {i: {"body_text": f"Body of {i}.", "fixture": i} for i in ids if i != "stuck"}

    def detail(self, provider_id):
        return {"body_text": f"Body of {provider_id}.", "fixture": provider_id}

    def mutate(self, provider_id, action):
        pass


def open_row(page, subject):
    page.locator(".message-row .row-subject", has_text=subject).click()
    expect(page.locator("#previewSubject")).to_have_text(subject)


def people_entry(page):
    return page.locator('#categoryFilters [data-category="unknown"]')


def main() -> int:
    out = Path(tempfile.mkdtemp(prefix="email-town-category-label-ui-"))
    with sync_playwright() as playwright:
        for engine in ("webkit", "chromium"):
            browser = getattr(playwright, engine).launch()
            root = out / engine
            paths = Paths.default(root / "home").ensure()
            paths.config_file.write_text('[model]\nref = "rules"\n')  # offline
            manager = Workbench(root / "workbench", paths, reader_factory=Mailbox, classifier=Model())
            manager.accounts.add(
                "Fixture", "imap", {"host": "fixture", "username": "f@example.test", "port": 993}
            )
            manager._refresh()
            server = LocalServer(manager)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                context = browser.new_context(viewport={"width": 1440, "height": 900})
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
                page.goto(server.launch_url)
                expect(page.locator(".bundle-row")).to_have_count(1)  # the two digests

                # ─── Waiting mail is "Sorting…", not People ───
                open_row(page, "Still being sorted")
                expect(page.locator("#previewCategory")).to_have_text("Sorting…")
                open_row(page, "Lunch on Friday?")
                expect(page.locator("#previewCategory")).to_have_text("People")
                page.screenshot(path=str(out / f"{engine}-people.png"))

                # ─── The sender-rule box only for categories a rule can choose ───
                if not page.locator("#previewMore").evaluate("el => el.open"):
                    page.locator("#previewMore > summary").click()
                page.locator("#previewCategoryButton").click()
                expect(page.locator("#categoryDialog")).to_be_visible()
                expect(page.locator("#senderRuleRow")).to_be_hidden()  # People is preselected
                page.locator("#categorySelect").select_option("updates")
                expect(page.locator("#senderRuleRow")).to_be_visible()
                page.locator("#categorySelect").select_option("new_sender")
                expect(page.locator("#senderRuleRow")).to_be_hidden()
                page.keyboard.press("Escape")
                expect(page.locator("#categoryDialog")).to_be_hidden()

                # ─── Keyboard choice of a category keeps focus there ───
                newsletters = page.locator('#categoryFilters [data-category="newsletters"]')
                newsletters.focus()
                page.keyboard.press("Enter")
                expect(page.locator("#filterChips .filter-chip")).to_have_count(1)
                expect(newsletters).to_be_focused()

                # ─── Selection made in a category does not follow into the bundled Inbox ───
                expect(page.locator(".message-row")).to_have_count(2)
                page.locator("#selectAll").check()
                expect(page.locator("#selectionCount")).to_have_text("2 selected")
                page.locator("#filterChips .filter-chip").click()
                expect(page.locator(".bundle-row")).to_have_count(1)
                expect(page.locator("#selectionCount")).not_to_contain_text("selected")
                page.screenshot(path=str(out / f"{engine}-deselected.png"))

                # ─── Sent mail: not classified, and not counted as People ───
                people_before = people_entry(page).get_attribute("aria-label") or ""
                page.locator('[data-mail-folder="sent"]').first.click()
                open_row(page, "My own reply")
                expect(page.locator("#previewCategory")).to_have_text("Not classified")
                label = people_entry(page).get_attribute("aria-label") or ""
                assert " of 0 loaded" in label, (engine, label, people_before)
                assert not errors, errors
                context.close()
                print(f"{engine}: ok")
            finally:
                server.shutdown()
                server.server_close()
                manager.close()
                browser.close()
    print(f"category label UI passed. Captures: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
