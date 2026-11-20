"""⌘A (Ctrl A off the Mac) selects the open message's text, never the folders, the list or the bundles.

Run: uv run --no-project --with-editable '.[dev]' --with playwright python scripts/check_select_all_ui.py

Read where the user sees it, the document's own selection:
- no message open: ⌘A selects nothing (before, the whole window: folders, rows, bundle rows);
- a plain-text message: exactly its body, not the "Show formatted" bar, the subject or a row;
- a formatted (HTML) message: the text inside its frame, and nothing in the window around it; focus
  stays with the app, so k still moves to the newer message;
- in the search box ⌘A still selects the field's text, as the system does.
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
PLAIN = "Plain body line one.\nPlain body line two."
HTML_TEXT = "Formatted body paragraph."


class SelectMailbox:
    def __init__(self, account):
        self.account = account

    def capabilities(self):
        return {"send": False, "write": True, "attachments": False, "max_attachment_bytes": 0, "reason": ""}

    def list(self, limit, folder="inbox"):
        if folder != "inbox":
            return [], False
        rows = [
            ("p1", "Teammate", "action_needed", "Plain one"),
            ("h1", "Partner", "action_needed", "Formatted one"),
            ("u1", "Build Bot", "updates", "Bundled update"),  # a bundle row in the Inbox
        ]
        return [
            {
                "provider_id": i,
                "headers": {"subject": subject, "from": f"{sender} <{i}@example.test>"},
                "internal_date": NOW - n * 60_000,
                "category": category,
                "label_ids": [],
            }
            for n, (i, sender, category, subject) in enumerate(rows)
        ][:limit], False

    def detail(self, provider_id):
        detail = {"body_text": PLAIN, "headers": {}, "attachments": []}
        if provider_id == "h1":
            detail.update(body_text=HTML_TEXT, body_html=f"<p>{HTML_TEXT}</p>")
        return detail

    def mutate(self, provider_id, action):
        pass


SELECTED = "() => getSelection().toString()"
FRAME_SELECTED = (
    "() => { const f = document.querySelector('#previewBody iframe.email-frame');"
    " return f ? f.contentWindow.getSelection().toString() : null; }"
)


def main() -> int:
    out = Path(tempfile.mkdtemp(prefix="email-town-select-all-ui-"))
    with sync_playwright() as playwright:
        for engine in ("webkit", "chromium"):
            browser = getattr(playwright, engine).launch()
            root = out / engine
            paths = Paths.default(root / "home").ensure()
            paths.config_file.write_text('[model]\nref = "rules"\n')  # offline
            manager = Workbench(root / "workbench", paths, reader_factory=SelectMailbox)
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
                mod = "Meta" if page.evaluate("/Mac/.test(navigator.platform)") else "Control"
                expect(page.locator(".bundle-row")).to_have_count(1)

                # ─── No message open: nothing, not the window ───
                page.locator("body").click(position={"x": 5, "y": 5})
                page.keyboard.press(f"{mod}+a")
                assert page.evaluate(SELECTED) == "", page.evaluate(SELECTED)[:200]

                # ─── A plain message: its body only ───
                page.locator(".message-row .row-subject", has_text="Plain one").click()
                expect(page.locator("#previewBody")).to_contain_text("Plain body line two.")
                page.locator("#previewSubject").click()  # focus in the reader, outside any field
                page.keyboard.press(f"{mod}+a")
                selected = page.evaluate(SELECTED)
                assert "Plain body line one." in selected and "Plain body line two." in selected, selected
                for outside in ("Plain one", "Formatted one", "Build Bot", "Inbox", "Show formatted"):
                    assert outside not in selected, (engine, outside, selected[:300])
                page.screenshot(path=str(out / f"{engine}-plain.png"))

                # ─── A formatted message: the frame's text, nothing around it ───
                page.locator(".message-row .row-subject", has_text="Formatted one").click()
                frame = page.frame_locator("#previewBody iframe.email-frame")
                expect(frame.locator("p")).to_have_text(HTML_TEXT)
                page.locator("#previewSubject").click()
                page.keyboard.press(f"{mod}+a")
                assert page.evaluate(FRAME_SELECTED).strip() == HTML_TEXT, page.evaluate(FRAME_SELECTED)
                assert page.evaluate(SELECTED) == "", page.evaluate(SELECTED)[:200]
                page.screenshot(path=str(out / f"{engine}-formatted.png"))
                # The shortcuts still work: focus did not move into the frame.
                page.keyboard.press("k")
                expect(page.locator("#previewSubject")).to_have_text("Plain one")

                # ─── In the search box ⌘A is the field's own ───
                page.locator("#searchInput").fill("bundled")
                page.locator("#searchInput").focus()
                page.keyboard.press(f"{mod}+a")
                ends = page.locator("#searchInput").evaluate("e => [e.selectionStart, e.selectionEnd]")
                assert ends == [0, len("bundled")], ends
                assert not errors, errors
                context.close()
                print(f"{engine}: ok")
            finally:
                server.shutdown()
                server.server_close()
                manager.close()
                browser.close()
    print(f"select-all UI passed. Captures: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
