"""The Gmail-style reading pane, against the real local server (synthetic mail only).

Run: uv run --with playwright python scripts/check_reader_ui.py

Quoted history folds behind "•••" whether the signature comes before it or after it, and no text is
dropped when a body has two signatures; links open safely in a new tab; recipients fold under
"to X, Y and N others"; attachments are cards with type, name and readable size, above the reply
buttons; "N of M" and newer/older follow the list; mail text is never parsed as HTML; the page raises
no Content-Security-Policy violation; nothing scrolls sideways at desktop or phone width.
WebKit (the macOS app's engine) and Chromium.
"""

from __future__ import annotations

import sys
import tempfile
import threading
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_shortcuts_ui import until

from email_town_labeler.config import Paths
from email_town_labeler.desktop.manager import Workbench
from email_town_labeler.desktop.server import LocalServer

NOW = int(time.time() * 1000)
TO = ", ".join(f"Person Number {i} <person{i}@example.test>" for i in range(1, 13))
MAIL = {  # newest first
    "news": {
        "subject": "Weekly digest",
        "sender": "Example Weekly <news@example.test>",
        "to": TO,
        "cc": "Team <team@example.test>, Another Team <another@example.test>",
        "body": "Read more: https://example.test/a?x=1.\nAlso (https://example.test/b) and https://example.test/c",
    },
    "sig-last": {
        "subject": "Re: Product review",
        "sender": "Li Wei <li.wei@example.test>",
        "to": "Me <me@example.test>",
        "body": "Wednesday at three works.\n\nOn Mon, Nov 3, 2026 at 10:12 AM Me <me@example.test> wrote:\n"
        "> Wednesday or Thursday?\n>\n-- \nLi Wei | Product Manager",
    },
    "sig-first": {
        "subject": "Re: Lunch",
        "sender": "Sam <sam@example.test>",
        "to": "Me <me@example.test>",
        "body": "Sounds good.\n-- \nSam\n\n在 2026年11月3日 写道：\n> 周三吃饭？",
    },
    "two-sigs": {
        "subject": "Two signatures",
        "sender": "Kim <kim@example.test>",
        "to": "Me <me@example.test>",
        "body": "Top text.\n-- \nFirst signature\n\nOn Mon Me wrote:\n> quoted line\n-- \nSecond signature",
    },
    "html": {
        "subject": "Markup stays text",
        "sender": "Mallory <mallory@example.test>",
        "to": "Me <me@example.test>",
        "body": '<img src="x" onerror="window.pwned=1"><b>bold?</b>',
    },
    "files": {
        "subject": "Contract documents",
        "sender": "Legal <legal@example.test>",
        "to": "Me <me@example.test>",
        "body": "Attached.",
        "attachments": [
            ("Signed contract.pdf", 2_400_000),
            ("Appendix A.pdf", 120_000),
            ("Pricing.xlsx", 45_000),
        ],
    },
}


class Reader:
    def __init__(self, account):
        self.address = "me@example.test"

    def capabilities(self):
        return {
            "send": True,
            "write": True,
            "attachments": True,
            "max_attachment_bytes": 1 << 20,
            "reason": "",
        }

    def list(self, limit, folder="inbox"):
        if folder != "inbox":
            return [], False
        return [
            {
                "provider_id": key,
                "headers": {"subject": m["subject"], "from": m["sender"]},
                "internal_date": NOW - i * 3_600_000,
                "category": "updates",
                "label_ids": [],
            }
            for i, (key, m) in enumerate(MAIL.items())
        ], False

    def detail(self, provider_id):
        m = MAIL[provider_id]
        return {
            "body_text": m["body"],
            "headers": {"from": m["sender"], "to": m["to"], "cc": m.get("cc", ""), "message-id": "<x@y>"},
            "attachments": [
                {"id": f"a{i}", "name": name, "size": size, "type": "application/octet-stream"}
                for i, (name, size) in enumerate(m.get("attachments", []))
            ],
            "thread_id": "t",
        }

    def mutate(self, provider_id, action):
        pass


def main() -> int:
    out = Path(tempfile.mkdtemp(prefix="email-town-reader-ui-"))
    paths = Paths.default(out / "home").ensure()
    paths.config_file.write_text('[model]\nref = "rules"\n')  # offline: never the 263 MB default model
    manager = Workbench(out / "workbench", paths, reader_factory=Reader)
    manager.accounts.add("Work", "imap", {"host": "h", "username": "me@example.test", "port": 993})
    manager._refresh()
    server = LocalServer(manager)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as playwright:
            for engine in ("webkit", "chromium"):
                browser = getattr(playwright, engine).launch()
                page = browser.new_page(viewport={"width": 1280, "height": 840})
                until.page = page
                errors, refused = [], []
                page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
                page.on(
                    "console",
                    lambda m, refused=refused: refused.append(m.text) if "Refused" in m.text else None,
                )
                page.goto(server.launch_url)
                rows = page.locator(".message-row")
                expect(rows).to_have_count(len(MAIL))
                body = page.locator("#previewBody")
                # Playwright's WebKit refuses its own stylesheets for search fields and selects under this
                # CSP (a blank page with one of each logs it too). Rendering mail must add none; inline
                # styles in the page's own markup are caught statically in tests/test_desktop_server.py.
                at_load = len(refused)

                def open_mail(key, page=page):
                    page.locator(".message-row").filter(has_text=MAIL[key]["subject"]).locator(
                        ".row-open"
                    ).click()
                    expect(page.locator("#previewSubject")).to_have_text(MAIL[key]["subject"])
                    expect(page.locator("[data-compose-mode='reply']")).to_be_visible()

                def order(page=page):  # the body's blocks, top to bottom
                    return page.evaluate(
                        "[...document.getElementById('previewBody').children].map(e => e.className)"
                    )

                # Signature after the quote (some clients): text, fold, signature; fold closed.
                open_mail("sig-last")
                assert order() == ["email-text", "email-quoted", "email-signature"], order()
                quoted = body.locator(".email-quoted")
                expect(quoted.locator("summary")).to_have_text("•••")
                expect(quoted.locator(".email-text")).to_be_hidden()
                quoted.locator("summary").click()
                expect(quoted.locator(".email-text")).to_contain_text("wrote:")
                expect(body.locator(".email-signature")).to_contain_text("Product Manager")

                # Signature before the quote (the usual order, here a Chinese attribution).
                open_mail("sig-first")
                assert order() == ["email-text", "email-signature", "email-quoted"], order()
                expect(body.locator(".email-quoted .email-text")).to_contain_text("写道")

                # Two signatures around a quote: nothing folded away is lost, nothing dropped.
                open_mail("two-sigs")
                for part in ("Top text.", "First signature", "quoted line", "Second signature"):
                    assert part in body.text_content(), (part, body.text_content())

                # Links: real anchors, new tab, no opener, sentence punctuation left outside.
                open_mail("news")
                links = body.locator("a")
                expect(links).to_have_count(3)
                assert [links.nth(i).get_attribute("href") for i in range(3)] == [
                    "https://example.test/a?x=1",
                    "https://example.test/b",
                    "https://example.test/c",
                ]
                for i in range(3):
                    assert links.nth(i).get_attribute("target") == "_blank"
                    assert links.nth(i).get_attribute("rel") == "noopener noreferrer"
                # Recipients: 12 To + 2 Cc, the first two named, the rest counted; full list on demand.
                headers = page.locator(".preview-headers")
                expect(headers.locator("summary")).to_have_text(
                    "to Person Number 1, Person Number 2 and 12 others"
                )
                expect(headers.locator("dl")).to_be_hidden()
                headers.locator("summary").click()
                expect(headers.locator("dt")).to_have_text(["From", "To", "Cc"])

                # Mail text is text: markup in a body is shown, never parsed.
                open_mail("html")
                expect(body.locator("img, b")).to_have_count(0)
                expect(body).to_contain_text('<img src="x"')
                assert page.evaluate("window.pwned") is None

                # Attachment cards above the reply buttons, and "N of M" with newer/older.
                open_mail("files")
                cards = page.locator(".attachment-card")
                expect(cards).to_have_count(3)
                expect(page.locator(".preview-attachments > strong")).to_have_text("3 attachments")
                expect(cards.locator(".attachment-kind")).to_have_text(["PDF", "PDF", "XLSX"])
                expect(cards.locator(".attachment-size")).to_have_text(["2.3 MB", "117 KB", "44 KB"])
                assert page.evaluate(
                    "document.querySelector('.preview-attachments').nextElementSibling"
                    ".classList.contains('reply-actions')"
                )
                expect(page.locator("#previewAvatar")).to_have_text("L")
                expect(page.locator("#previewPosition")).to_have_text(f"{len(MAIL)} of {len(MAIL)}")
                expect(page.locator("#previewOlder")).to_be_disabled()
                page.locator("#previewNewer").click()
                expect(page.locator("#previewSubject")).to_have_text(MAIL["html"]["subject"])
                expect(page.locator("#previewPosition")).to_have_text(f"{len(MAIL) - 1} of {len(MAIL)}")
                expect(page.locator("#previewOlder")).to_be_enabled()
                rendering = refused[at_load:]  # before any capture: Playwright's screenshots inject a style
                assert not rendering, f"Content-Security-Policy violations while reading mail: {rendering}"

                # Nothing scrolls sideways, at the app's window size and on a phone.
                for width, height in ((1280, 840), (390, 844)):
                    page.set_viewport_size({"width": width, "height": height})
                    if width >= 1200:
                        open_mail("news")
                    sideways = page.evaluate(
                        "(() => { const p = document.getElementById('previewPane');"
                        " return [p.scrollWidth - p.clientWidth,"
                        " document.scrollingElement.scrollWidth - innerWidth]; })()"
                    )
                    assert max(sideways) <= 1, f"{engine} {width}px scrolls sideways: {sideways}"
                    page.screenshot(path=str(out / f"{engine}-{width}.png"))
                assert not errors, errors
                print(f"{engine}: reading pane passes")
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        manager.close()
    print(f"reading pane UI passed. Captures: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
