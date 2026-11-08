"""Verify responsive mail-workbench layout and keyboard behavior with fixtures.

Run: ``uv run --with playwright python scripts/check_layout_ui.py``

All browser state is synthetic and all captures are written to a temporary
directory.  This verifier deliberately does not submit account, mail, or
category forms.
"""

from __future__ import annotations

import re
import sys
import tempfile
import threading
from pathlib import Path

from check_desktop_ui import contrast
from check_mail_ui import FixtureReader
from playwright.sync_api import expect, sync_playwright

from email_town_labeler.config import Paths
from email_town_labeler.desktop.manager import Workbench
from email_town_labeler.desktop.server import LocalServer


def assert_no_overflow(page, label: str) -> None:
    """Reject document and visible-pane overflow, including nested controls."""
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), label
    assert page.evaluate(
        """
        [...document.querySelectorAll('.message-pane, .preview-pane, dialog[open]')]
          .filter((el) => getComputedStyle(el).display !== 'none')
          .every((el) => el.scrollWidth <= el.clientWidth + 1)
        """
    ), f"{label}: visible pane overflow"


def bounds(page, selector: str) -> dict:
    result = page.locator(selector).bounding_box()
    assert result is not None, selector
    return result


def assert_list_geometry(page, label: str) -> None:
    search = bounds(page, ".search-field")
    checkbox = page.locator(".message-row .row-check").first.bounding_box()
    assert checkbox is not None
    assert abs(search["x"] - checkbox["x"]) <= 1, (label, search, checkbox)
    assert_no_overflow(page, label)


def open_preview(page) -> None:
    page.locator(".row-open").first.click()
    expect(page.locator("#previewContent")).to_be_visible()
    expect(page.locator("#previewBody")).to_contain_text("Synthetic body")


def assert_preview_keyboard(page) -> None:
    page.locator("#previewMore summary").focus()
    page.keyboard.press("Enter")
    expect(page.locator("#previewMore")).to_have_attribute("open", "")
    assert page.locator("#previewMore .menu-panel button:visible").count() > 0
    page.keyboard.press("Escape")
    expect(page.locator("#previewMore")).not_to_have_attribute("open", "")
    expect(page.locator("#previewMore summary")).to_be_focused()
    assert page.locator("#previewMore:not([open]) .menu-panel button:visible").count() == 0
    assert page.locator("#previewMore:not([open]) .menu-panel a:visible").count() == 0


def assert_compose(page, capture: Path, attachment: Path) -> None:
    page.locator("#composeButton").click()
    expect(page.locator("#composeDialog")).to_be_visible()
    left_edges = [
        bounds(page, f"#composeDialog {selector}")["x"]
        for selector in ("#composeTo", "#composeCc", "#composeBcc", "#composeSubject", "#composeBody")
    ]
    assert max(left_edges) - min(left_edges) <= 1, left_edges
    colors = page.locator("#composeTo").evaluate(
        "el => ({border:getComputedStyle(el).borderColor, bg:getComputedStyle(el).backgroundColor})"
    )
    assert contrast(colors["border"], colors["bg"]) >= 3, colors
    assert page.locator("#composeBody").evaluate(
        "el => getComputedStyle(el).fontFamily === getComputedStyle(document.body).fontFamily"
    )
    page.locator(".compose-fields").evaluate("el => el.scrollTop = el.scrollHeight")
    page.locator("#composeAttachments").set_input_files(str(attachment))
    expect(page.locator(".attachment-item").first).to_be_visible()
    label_box = bounds(page, ".attachment-item span")
    remove_box = bounds(page, ".attachment-item button")
    assert abs(label_box["y"] - remove_box["y"]) <= 1, (label_box, remove_box)
    assert remove_box["y"] + remove_box["height"] <= label_box["y"] + label_box["height"] + 1
    assert_no_overflow(page, "compose")
    actions = bounds(page, "#composeDialog .dialog-actions")
    assert actions["y"] + actions["height"] <= page.viewport_size["height"] + 1, actions
    page.screenshot(path=str(capture))
    page.locator("#composeDialog [data-compose-close]").first.click()
    expect(page.locator("#composeDialog")).to_be_hidden()


def assert_account(page, capture: Path) -> None:
    if page.viewport_size["width"] < 768:
        page.locator("#sidebarToggle").click()
    if page.viewport_size["width"] < 768:
        expect(page.locator("#sidebarBackdrop")).to_be_visible()
        page.locator("#mobileRefreshButton").focus()
        page.keyboard.press("Shift+Tab")
        expect(page.locator("#sidebar button").last).to_be_focused()
        page.keyboard.press("Tab")
        expect(page.locator("#mobileRefreshButton")).to_be_focused()
    page.locator("#addAccountButton").click()
    expect(page.locator("#accountDialog")).to_be_visible()
    page.locator("#providerSelect").select_option("imap")
    expect(page.locator("#imapFields")).to_be_visible()
    server = bounds(page, "#imapHost")
    port = bounds(page, "#imapPort")
    assert abs(server["y"] - port["y"]) <= 1, (server, port)
    assert_no_overflow(page, "account")
    page.screenshot(path=str(capture))
    # A known address fills the server settings and folds them away; only the app password remains.
    page.locator("#imapEmail").fill("fixture@icloud.com")
    expect(page.locator("#imapHost")).to_have_value("imap.mail.me.com")
    expect(page.locator("#smtpHost")).to_have_value("smtp.mail.me.com")
    expect(page.locator("#imapUsername")).to_have_value("fixture@icloud.com")
    expect(page.locator("#imapPresetHelp")).to_be_visible()
    expect(page.locator("#imapServerDetails")).not_to_have_attribute("open", "")
    expect(page.locator("#imapPassword")).to_be_visible()
    assert_no_overflow(page, "account preset")
    page.wait_for_timeout(300)  # let the disclosure chevron finish turning before the capture
    page.screenshot(path=str(capture.with_name(capture.stem + "-preset.png")))
    # Outlook.com addresses are routed to Microsoft sign-in instead of an app password.
    page.locator("#imapEmail").fill("fixture@outlook.com")
    page.locator("#useMicrosoftButton").click()
    expect(page.locator("#providerSelect")).to_have_value("microsoft")
    expect(page.locator("#microsoftHelp")).to_be_visible()
    page.locator("#providerSelect").select_option("gmail")
    expect(page.locator("#googleStatus")).not_to_be_empty()
    page.screenshot(path=str(capture.with_name(capture.stem + "-google.png")))
    page.keyboard.press("Escape")
    expect(page.locator("#accountDialog")).to_be_hidden()


def main() -> int:
    out = Path(tempfile.mkdtemp(prefix="email-town-layout-ui-"))
    paths = Paths.default(out / "home").ensure()
    readers: list[FixtureReader] = []

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
            browser = playwright.chromium.launch()
            for theme in ("light", "dark"):
                for width in (390, 768, 1280, 1440):
                    context = browser.new_context(
                        viewport={"width": width, "height": 900}, color_scheme=theme
                    )
                    page = context.new_page()
                    page.set_default_timeout(8000)
                    page.on("dialog", lambda dialog: dialog.accept())
                    page.goto(server.launch_url)
                    expect(page.locator(".message-row")).to_have_count(2)
                    assert_list_geometry(page, f"{theme}-{width}")
                    if width == 390:
                        page.screenshot(path=str(out / f"{theme}-list.png"))
                    open_preview(page)
                    page.screenshot(path=str(out / f"{theme}-{width}-preview.png"))
                    if width == 768:
                        workspace = bounds(page, ".workspace")
                        preview = bounds(page, "#previewPane")
                        assert abs(preview["height"] - workspace["height"]) <= 1, (workspace, preview)
                        expect(page.locator("#previewClose")).to_be_visible()
                        page.locator("#previewClose").click()
                        expect(page.locator("#previewContent")).to_be_hidden()
                        expect(page.locator(".message-pane")).to_be_visible()
                        expect(page.locator(".row-open").first).to_be_focused()
                    elif width < 1200:
                        page.locator("#previewClose").click()
                    else:
                        assert page.locator(".message-pane").is_visible()
                    if width == 1440:
                        open_preview(page)
                        assert_preview_keyboard(page)
                    page.locator("#searchInput").fill("no-such-fixture")
                    expect(page.locator(".empty-state")).to_be_visible()
                    page.locator("#clearFiltersButton").click()
                    expect(page.locator(".message-row")).to_have_count(2)
                    page.locator(".row-open").first.click()
                    page.locator("#previewMore summary").click()
                    page.locator("#previewCategoryButton").click()
                    expect(page.locator("#categoryDialog")).to_be_visible()
                    page.keyboard.press("Escape")
                    expect(page.locator("#categoryDialog")).to_be_hidden()
                    expect(page.locator("#previewMore summary")).to_be_focused()
                    if width < 1200:
                        page.locator("#previewClose").click()
                    if width == 390:
                        attachment = out / "fixture-long-synthetic-attachment-name.txt"
                        attachment.write_text("synthetic attachment")
                        page.set_viewport_size({"width": 390, "height": 640})
                        assert_compose(page, out / f"{theme}-compose.png", attachment)
                        assert_account(page, out / f"{theme}-account.png")
                    context.close()

                context = browser.new_context(viewport={"width": 320, "height": 640}, color_scheme=theme)
                page = context.new_page()
                page.goto(server.launch_url)
                expect(page.locator(".message-row")).to_have_count(2)
                assert_list_geometry(page, f"{theme}-320")
                page.locator(".row-check").first.check()
                page.locator("#bulkMore summary").click()
                panel = page.locator("#bulkMore .menu-panel")
                expect(panel).to_be_visible()
                expect(panel).to_have_attribute("style", re.compile(r"top:"))
                box = panel.bounding_box()
                assert box and box["x"] >= 0 and box["x"] + box["width"] <= 320, box
                assert box["y"] >= 0 and box["y"] + box["height"] <= 640, box
                page.locator("#reopenButton").scroll_into_view_if_needed()
                assert page.locator("#reopenButton").is_visible()
                page.screenshot(path=str(out / f"{theme}-320-layout.png"))
                page.keyboard.press("Escape")
                page.locator("#sidebarToggle").click()
                expect(page.locator("#mobileRefreshButton")).to_be_visible()
                page.locator("#mobileRefreshButton").click()
                expect(page.locator("#sidebarBackdrop")).to_be_hidden()
                # State captures stay on the real app/CSP; only synthetic API snapshots vary.
                base = manager.snapshot()
                cases = (
                    ("loading", {**base, "messages": [], "refreshing": True}, ".loading-state"),
                    ("empty", {**base, "messages": [], "refreshing": False}, ".empty-state"),
                    (
                        "error",
                        {**base, "model_error": "Fixture classifier unavailable. Mail is still readable."},
                        "#listStatus",
                    ),
                )
                for name, snapshot, selector in cases:

                    def fixture_state(route, request, snapshot=snapshot):
                        route.fulfill(json=snapshot)

                    page.route("**/api/state*", fixture_state)
                    page.reload()
                    expect(page.locator(selector)).to_be_visible()
                    assert_no_overflow(page, name)
                    page.screenshot(path=str(out / f"{theme}-320-{name}.png"))
                    page.unroute("**/api/state*", fixture_state)

                def expired_session(route):
                    route.fulfill(status=401, json={"error": "fixture session expired"})

                page.route("**/api/state*", expired_session)
                page.reload()
                expect(page.locator("#restartButton")).to_be_visible()
                expect(page.locator(".error-state h3")).to_have_text("Session expired")
                expect(page.locator(".loading-state")).to_have_count(0)
                footer = bounds(page, ".app-footer")
                assert footer["y"] + footer["height"] <= 640, footer
                assert_no_overflow(page, "session-expired")
                page.screenshot(path=str(out / f"{theme}-320-session.png"))
                context.close()
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
        manager.close()
    print(f"layout UI fixture passed; captures in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
