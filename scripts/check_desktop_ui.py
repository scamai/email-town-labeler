"""Exercise the actual desktop server/UI using synthetic mail only.

Run: uv run --with playwright python scripts/check_desktop_ui.py
Install the test browser once with: uv run --with playwright playwright install chromium
Captures go to a temporary directory, never to a mailbox or the repository.
"""

from __future__ import annotations

import json
import sys
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
        self.account = account

    def list(self, limit):
        return [
            {
                "provider_id": str(i),
                "headers": {"subject": title, "from": "Fixture <sender@example.test>"},
                "internal_date": int(time.time() * 1000) - i * 86400000,
                "category": category,
                "label_ids": ["UNREAD"],
            }
            for i, (title, category) in enumerate(
                [
                    ("确认明天的安排（示例）", "action_needed"),
                    ("订单已送达（示例）", "orders"),
                    ("本周简报（示例）", "newsletters"),
                ]
            )
        ], False

    def detail(self, provider_id):
        return {
            "body_text": "这是合成测试邮件，不包含真实邮件。\n<script>alert('fixture')</script>\n请确认安排。",
            "url": "https://mail.google.com/mail/#all/fixture",
        }


def main():
    out = Path(tempfile.mkdtemp(prefix="email-town-ui-"))
    paths = Paths.default(out / "home").ensure()
    manager = Workbench(out / "workbench", paths, reader_factory=FixtureReader)
    manager.demo = True
    for name in ("工作邮箱 · 示例", "个人邮箱 · 示例"):
        manager.accounts.add(name, "imap", {"host": name, "username": "fixture", "port": 993})
    manager._refresh()
    server = LocalServer(manager)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    results = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            for theme in ("light", "dark"):
                for width in (390, 768, 1280, 1440):
                    context = browser.new_context(
                        viewport={"width": width, "height": 900}, color_scheme=theme
                    )
                    page = context.new_page()
                    page.set_default_timeout(10000)
                    errors = []
                    page.on("pageerror", lambda error, errors=errors: errors.append(str(error)))
                    page.goto(server.launch_url)
                    expect(page.locator(".message-row")).to_have_count(6)
                    expect(page.locator("#demoBanner")).to_be_visible()
                    assert page.evaluate("location.hash") == ""
                    assert not context.cookies()
                    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth"), (
                        theme,
                        width,
                    )
                    page.screenshot(path=str(out / f"{theme}-{width}.png"))
                    page.locator(".row-open").first.focus()
                    page.keyboard.press("Enter")
                    expect(page.locator("#previewBody")).to_contain_text("合成测试邮件")
                    assert page.locator("#previewBody script").count() == 0
                    if width == 390:
                        page.locator("#previewClose").click()
                    page.locator("#searchInput").fill("订单")
                    expect(page.locator(".message-row")).to_have_count(2)
                    page.locator("#searchInput").fill("no-match-fixture")
                    expect(page.locator(".empty-state")).to_be_visible()
                    page.locator("#clearFiltersButton").click()
                    expect(page.locator(".message-row")).to_have_count(6)
                    # Reflow at equivalent 200% browser zoom (half CSS viewport).
                    page.set_viewport_size({"width": max(320, width // 2), "height": 900})
                    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth"), (
                        "zoom",
                        theme,
                        width,
                    )
                    results.append({"theme": theme, "width": width, "overflow": False, "errors": errors})
                    assert not errors
                    context.close()
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            page = context.new_page()

            def bootstrap_visibility(route):
                # Visibility can fire while OAuth/session exchange is still pending.
                page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
                route.continue_()

            page.route("**/api/session", bootstrap_visibility)
            page.goto(server.launch_url)
            expect(page.locator(".message-row")).to_have_count(6)
            page.locator("[data-account-filter]").nth(1).click()
            expect(page.locator(".message-row")).to_have_count(3)
            page.locator("#selectAll").check()
            page.locator("#markDoneButton").click()
            expect(page.locator("#toast button")).to_have_text("撤销")
            assert sum(m["done"] for m in manager.snapshot()["messages"]) == 3
            page.locator("#toast button").click()
            expect(page.locator("#toast")).to_have_text("已撤销本次更改")
            assert not any(m["done"] for m in manager.snapshot()["messages"])
            page.locator(".row-open").first.click()
            page.locator("#previewCategoryButton").click()
            page.locator("#categorySelect").select_option("travel")
            page.locator("#categoryForm button[type=submit]").click()
            expect(page.locator("#previewCategory")).to_have_text("旅行行程")
            page.reload()
            expect(page.locator(".message-row")).to_have_count(6)
            assert sum(m["category"] == "travel" for m in manager.snapshot()["messages"]) == 1
            # Poll rerender must preserve keyboard focus.
            page.locator(".row-open").first.focus()
            focus = page.evaluate("document.activeElement.dataset.focusKey")
            manager.last_refresh += 1
            page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
            expect(page.locator(f'[data-focus-key="{focus}"]')).to_be_focused()
            # Account controls reach persisted backend state; reauthorization pre-fills only safe settings.
            account_id = manager.accounts.all()[0]["id"]
            page.locator(f'[data-focus-key="manage-{account_id}"]').click()
            page.locator("#pauseAccountButton").click()
            expect(page.locator(f'[data-account-filter="{account_id}"]')).to_contain_text("暂停同步")
            assert manager.accounts.get(account_id)["enabled"] is False
            page.locator(f'[data-focus-key="manage-{account_id}"]').click()
            page.locator("#reconnectAccountButton").click()
            expect(page.locator("#providerSelect")).to_be_disabled()
            expect(page.locator("#imapUsername")).to_have_value("fixture")
            page.keyboard.press("Escape")
            # Add-account dialog has no hidden required fields blocking Gmail.
            page.locator("#addAccountButton").click()
            page.locator("#accountForm input[name=name]").fill("Fixture")
            assert page.locator("#accountForm").evaluate("form => form.checkValidity()")
            page.keyboard.press("Escape")
            manager.jobs = {
                "fixture": {
                    "status": "pending",
                    "message": "示例授权",
                    "user_code": "FIXTURE",
                    "verification_url": "https://microsoft.com/devicelogin",
                }
            }
            page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
            expect(page.locator("#jobStatus a")).to_have_attribute(
                "href", "https://microsoft.com/devicelogin"
            )
            # Session expiry must stop reads and clear port-scoped storage.
            server.session = "rotated-fixture-session"
            page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
            expect(page.locator("#syncText")).to_have_text("需要重新启动")
            assert page.evaluate("sessionStorage.getItem('email-town-session')") is None
            context.close()
            browser.close()
        (out / "results.json").write_text(json.dumps(results, indent=2))
        print(
            f"UI PASS: 8 viewport/theme combinations; real HTTP actions, undo, local category, reload, keyboard, auth. Captures: {out}"
        )
    finally:
        server.shutdown()
        server.server_close()
        manager.close()
        thread.join(2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
