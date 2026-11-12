"""Transient list/preview recovery against the real server and synthetic mail.

Run: uv run --extra dev --with playwright python scripts/check_recovery_ui.py
Set EMAIL_TOWN_UI_ENGINE=chromium or webkit for a single engine.
"""

from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from check_shortcuts_ui import NOW, FixtureReader, until
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect, sync_playwright

from email_town_labeler.config import Paths
from email_town_labeler.desktop.manager import Workbench
from email_town_labeler.desktop.server import LocalServer


class RecoveryMailbox(FixtureReader):
    def __init__(self, account):
        super().__init__(account)
        self.prefix = "alpha" if account["name"] == "Alpha fixture" else "beta"
        self.address = f"{self.prefix}@example.test"
        count = 24 if self.prefix == "alpha" else 2
        offset = 0 if self.prefix == "alpha" else 100
        self.records = [
            {
                "id": f"{self.prefix}{index}",
                "subject": f"{self.prefix.title()} recovery {index} (synthetic)",
                "folder": "inbox",
                "labels": ["UNREAD"],
                "at": NOW - (index + offset) * 60_000,
            }
            for index in range(1, count + 1)
        ]

    def detail(self, provider_id):
        return {
            "body_text": f"Synthetic body for {provider_id}.",
            "headers": {
                "from": "Sender <sender@example.test>",
                "to": f"{self.prefix}@example.test",
                "message-id": f"<{provider_id}@example.test>",
            },
            "attachments": [],
            "thread_id": f"thread-{provider_id}",
        }


def workbench(root: Path):
    paths = Paths.default(root / "home").ensure()
    paths.config_file.write_text('[model]\nref = "rules"\n')
    readers = []

    def factory(account):
        reader = RecoveryMailbox(account)
        readers.append(reader)
        return reader

    manager = Workbench(root / "workbench", paths, reader_factory=factory)
    manager.demo = True
    for name in ("Alpha fixture", "Beta fixture"):
        manager.accounts.add(name, "gmail", {"config_dir": str(root / name)})
    manager._refresh()
    return manager, readers


def abort_routes(routes):
    for route in routes:
        try:
            route.abort()
        except PlaywrightError:
            pass


def track_session(page, counts):
    def session(route):
        counts["session"] += 1
        route.continue_()

    page.route("**/api/session", session)


def check_initial_retry(page, manager, readers):
    counts = {"session": 0, "immediate": 0}
    held_polls = []
    held_retry = []
    track_session(page, counts)

    def state(route):
        if "wait" in parse_qs(urlsplit(route.request.url).query):
            held_polls.append(route)
            return
        counts["immediate"] += 1
        if counts["immediate"] == 1:
            route.fulfill(status=503, json={"error": "fixture initial read failed"})
        else:
            held_retry.append(route)

    page.route("**/api/state*", state)
    navigations = []
    page.on(
        "framenavigated", lambda frame: navigations.append(frame.url) if frame == page.main_frame else None
    )
    try:
        page.goto(page.fixture_launch_url)
        retry = page.get_by_role("button", name="Retry mail load", exact=True)
        expect(retry).to_be_visible()
        expect(page.locator("#listError")).to_contain_text("fixture initial read failed")
        expect(page.locator(".loading-state")).to_have_count(0)
        expect(page.locator(".error-state h3")).to_have_text("Mail could not be loaded")
        token = page.evaluate("sessionStorage.getItem('email-town-session')")
        assert token
        navigation_count = len(navigations)

        retry.click()
        until(lambda: len(held_retry) == 1, "held failed initial retry response")
        expect(retry).to_be_disabled()
        search = page.locator("#searchInput")
        search.focus()
        search.press_sequentially("recovery")
        held_retry.pop(0).fulfill(status=503, json={"error": "fixture initial retry failed"})
        expect(page.locator("#listError")).to_contain_text("fixture initial retry failed")
        expect(retry).to_be_enabled()
        expect(search).to_be_focused()
        expect(search).to_have_value("recovery")
        assert counts == {"session": 1, "immediate": 2}

        retry.click()
        until(lambda: len(held_retry) == 1, "held successful initial retry response")
        expect(retry).to_be_disabled()
        held_retry.pop(0).continue_()

        expect(page.locator(".message-row")).to_have_count(26)
        expect(page.locator("#listError")).to_be_hidden()
        expect(page.locator("#messageList")).to_be_focused()
        expect(search).to_have_value("recovery")
        expect(page.locator("#restartButton")).to_be_hidden()
        assert page.evaluate("sessionStorage.getItem('email-town-session')") == token
        assert len(navigations) == navigation_count
        assert counts == {"session": 1, "immediate": 3}
    finally:
        abort_routes([*held_polls, *held_retry])


def check_background_recovery(page, manager, readers):
    armed = {"value": False}
    counts = {"immediate": 0, "failed_polls": 0}
    held_polls = []
    held_retry = []

    def state(route):
        if not armed["value"]:
            route.continue_()
            return
        if "wait" in parse_qs(urlsplit(route.request.url).query):
            if counts["failed_polls"] == 0:
                counts["failed_polls"] += 1
                route.fulfill(status=503, json={"error": "fixture background poll failed"})
            else:
                held_polls.append(route)
            return
        counts["immediate"] += 1
        if counts["immediate"] == 1:
            route.fulfill(status=503, json={"error": "fixture recovery read failed"})
        else:
            held_retry.append(route)

    page.route("**/api/state*", state)
    navigations = []
    page.on(
        "framenavigated", lambda frame: navigations.append(frame.url) if frame == page.main_frame else None
    )
    try:
        page.goto(page.fixture_launch_url)
        rows = page.locator(".message-row")
        expect(rows).to_have_count(26)
        alpha_id = next(
            account["id"] for account in manager.accounts.all() if account["name"] == "Alpha fixture"
        )
        page.locator(f'[data-account-filter="{alpha_id}"]').click()
        expect(rows).to_have_count(24)
        rows.nth(0).locator(".row-check").check()
        rows.nth(1).locator(".row-check").check()

        page.locator("#composeButton").click()
        page.locator("#composeSubject").fill("Retained recovery draft (synthetic)")
        page.locator("#composeBody").fill("This synthetic draft must survive mail recovery.")
        page.locator("#saveDraftButton").click()
        expect(page.locator("#toast")).to_contain_text("Draft saved")
        page.locator("#composeDialog [data-compose-close]").first.click()
        expect(page.locator("#composeDialog")).to_be_hidden()

        retained = page.evaluate(
            """() => {
              const list = document.getElementById('messageList');
              list.scrollTop = 240;
              window.__recoveryRows = [...document.querySelectorAll('.message-row')].map(row => [row.dataset.messageId, row]);
              return {
                scroll: list.scrollTop,
                selected: [...window.EmailTownMailHost.state.selected],
                token: sessionStorage.getItem('email-town-session'),
              };
            }"""
        )
        navigation_count = len(navigations)
        armed["value"] = True
        manager.last_refresh += 1
        manager._touch()

        retry = page.get_by_role("button", name="Retry mail load", exact=True)
        expect(retry).to_be_visible(timeout=8000)
        expect(page.locator("#listError")).to_contain_text("fixture recovery read failed")
        expect(page.locator("#listStatus")).to_contain_text("showing the last loaded data")
        expect(rows).to_have_count(24)
        expect(page.locator(".message-row.is-selected")).to_have_count(2)
        expect(page.locator(f'[data-account-filter="{alpha_id}"]')).to_have_attribute("aria-pressed", "true")
        assert (
            page.evaluate(
                "window.EmailTownMailHost.state.selected && [...window.EmailTownMailHost.state.selected]"
            )
            == retained["selected"]
        )
        assert page.locator("#messageList").evaluate("list => list.scrollTop") == retained["scroll"]
        assert page.evaluate(
            """() => window.__recoveryRows.every(([id, row]) =>
              document.querySelector(`.message-row[data-message-id="${CSS.escape(id)}"]`) === row)"""
        )

        retry.click()
        until(lambda: len(held_retry) == 1, "held background retry response")
        expect(retry).to_be_disabled()
        search = page.locator("#searchInput")
        search.focus()
        search.press_sequentially("Alpha recovery")
        held_retry.pop(0).continue_()

        expect(page.locator("#listError")).to_be_hidden()
        expect(page.locator("#listStatus")).not_to_contain_text("showing the last loaded data")
        expect(page.locator("#syncText")).to_have_text("Synced")
        expect(search).to_be_focused()
        expect(search).to_have_value("Alpha recovery")
        expect(rows).to_have_count(24)
        expect(page.locator(".message-row.is-selected")).to_have_count(2)
        assert page.evaluate("[...window.EmailTownMailHost.state.selected]") == retained["selected"]
        assert page.locator("#messageList").evaluate("list => list.scrollTop") == retained["scroll"]
        assert page.evaluate(
            """() => window.__recoveryRows.every(([id, row]) =>
              document.querySelector(`.message-row[data-message-id="${CSS.escape(id)}"]`) === row)"""
        )
        assert page.evaluate("sessionStorage.getItem('email-town-session')") == retained["token"]
        assert len(navigations) == navigation_count
        assert counts == {"immediate": 2, "failed_polls": 1}

        page.locator("#draftsButton").click()
        expect(page.locator(".draft-item").filter(has_text="Retained recovery draft")).to_have_count(1)
        page.locator("[data-drafts-close]").click()
    finally:
        abort_routes([*held_polls, *held_retry])


def check_preview_retry(page, manager, readers):
    target = {"id": ""}
    counts = {"detail": 0, "read": 0}
    held_initial = []
    held_retry = []

    def detail(route):
        message_id = parse_qs(urlsplit(route.request.url).query).get("id", [""])[0]
        if message_id != target["id"]:
            route.continue_()
            return
        counts["detail"] += 1
        if counts["detail"] == 1:
            held_initial.append(route)
        else:
            held_retry.append(route)

    def mark_read(route):
        counts["read"] += 1
        route.continue_()

    page.route("**/api/message?id=*", detail)
    page.route("**/api/mark-read", mark_read)
    try:
        page.goto(page.fixture_launch_url)
        row = page.locator(".message-row").first
        target["id"] = row.get_attribute("data-message-id")
        row.locator(".row-open").click()
        until(lambda: len(held_initial) == 1, "held slow initial preview")
        expect(page.locator("#previewBody")).to_have_text("Reading message…")
        held_initial.pop(0).fulfill(status=503, json={"error": "fixture delayed read failure"})

        retry = page.get_by_role("button", name="Retry message", exact=True)
        expect(retry).to_be_visible()
        expect(page.locator("#previewError")).to_contain_text("fixture delayed read failure")
        expect(page.locator("#previewBody")).to_have_text("")
        assert counts["detail"] == 1

        retry.click()
        until(lambda: len(held_retry) == 1, "held preview retry response")
        expect(retry).to_be_disabled()
        page.locator("#composeButton").click()
        editor = page.locator("#composeBody")
        editor.press_sequentially("Keep writing during successful preview recovery.")
        held_retry.pop(0).continue_()

        expect(page.locator("#previewBody")).to_contain_text("Synthetic body for alpha1.")
        expect(page.locator("#previewError")).to_be_hidden()
        expect(editor).to_be_focused()
        expect(editor).to_have_value("Keep writing during successful preview recovery.")
        page.locator("#composeDialog [data-compose-close]").first.click()
        expect(page.locator("#composeDialog")).to_be_hidden()
        until(lambda: counts["read"] == 1, "one automatic mark-read after preview retry")
        until(lambda: "UNREAD" not in readers[0].record("alpha1")["labels"], "provider read settles")
        assert counts == {"detail": 2, "read": 1}
    finally:
        abort_routes([*held_initial, *held_retry])


def check_preview_failed_retry(page, manager, readers):
    target = {"id": ""}
    counts = {"detail": 0, "read": 0}
    held_retry = []

    def detail(route):
        message_id = parse_qs(urlsplit(route.request.url).query).get("id", [""])[0]
        if message_id != target["id"]:
            route.continue_()
            return
        counts["detail"] += 1
        if counts["detail"] == 1:
            route.fulfill(status=503, json={"error": "fixture preview read failed"})
        else:
            held_retry.append(route)

    def mark_read(route):
        counts["read"] += 1
        route.continue_()

    page.route("**/api/message?id=*", detail)
    page.route("**/api/mark-read", mark_read)
    try:
        page.goto(page.fixture_launch_url)
        row = page.locator(".message-row").nth(2)
        target["id"] = row.get_attribute("data-message-id")
        row.locator(".row-open").click()
        retry = page.get_by_role("button", name="Retry message", exact=True)
        expect(retry).to_be_visible()

        retry.click()
        until(lambda: len(held_retry) == 1, "held failed preview retry")
        expect(retry).to_be_disabled()
        page.locator("#composeButton").click()
        editor = page.locator("#composeBody")
        editor.press_sequentially("Keep writing during failed preview recovery.")
        held_retry.pop(0).fulfill(status=503, json={"error": "fixture preview retry failed"})

        expect(page.locator("#previewError")).to_contain_text("fixture preview retry failed")
        expect(page.locator("#previewBody")).to_have_text("")
        expect(retry).to_be_enabled()
        expect(editor).to_be_focused()
        expect(editor).to_have_value("Keep writing during failed preview recovery.")
        page.locator("#composeDialog [data-compose-close]").first.click()
        expect(page.locator("#composeDialog")).to_be_hidden()

        retry.click()
        until(lambda: len(held_retry) == 1, "held ordinary preview retry")
        expect(retry).to_be_disabled()
        held_retry.pop(0).continue_()
        expect(page.locator("#previewBody")).to_contain_text("Synthetic body for alpha3.")
        expect(page.locator("#previewError")).to_be_hidden()
        expect(page.locator("#previewBody")).to_be_focused()
        until(lambda: counts["read"] == 1, "one automatic mark-read after failed retry")
        assert counts == {"detail": 3, "read": 1}
    finally:
        abort_routes(held_retry)


def check_preview_failure_race(page, manager, readers):
    ids = {"first": "", "second": ""}
    held_initial = []

    def detail(route):
        message_id = parse_qs(urlsplit(route.request.url).query).get("id", [""])[0]
        if message_id == ids["first"]:
            held_initial.append(route)
        else:
            route.continue_()

    page.route("**/api/message?id=*", detail)
    try:
        page.goto(page.fixture_launch_url)
        rows = page.locator(".message-row")
        expect(rows).to_have_count(26)
        ids["first"] = rows.nth(0).get_attribute("data-message-id")
        ids["second"] = rows.nth(1).get_attribute("data-message-id")
        rows.nth(0).locator(".row-open").click()
        until(lambda: len(held_initial) == 1, "held first preview failure")
        page.evaluate(
            """id => {
              window.__oldFailureObserved = window.EmailTownMailHost.messageDetail(id).then(
                () => 'success', () => 'failure'
              );
            }""",
            ids["first"],
        )

        rows.nth(1).locator(".row-open").click()
        expect(page.locator("#previewBody")).to_contain_text("Synthetic body for alpha2.")
        held_initial.pop(0).fulfill(status=503, json={"error": "fixture late preview failure"})
        assert page.evaluate("() => window.__oldFailureObserved") == "failure"
        expect(page.locator("#previewSubject")).to_have_text("Alpha recovery 2 (synthetic)")
        expect(page.locator("#previewBody")).to_contain_text("Synthetic body for alpha2.")
        expect(page.locator("#previewError")).to_be_hidden()
    finally:
        abort_routes(held_initial)


def check_preview_retry_race(page, manager, readers):
    ids = {"first": "", "second": ""}
    counts = {"first": 0}
    held_retry = []

    def detail(route):
        message_id = parse_qs(urlsplit(route.request.url).query).get("id", [""])[0]
        if message_id != ids["first"]:
            route.continue_()
            return
        counts["first"] += 1
        if counts["first"] == 1:
            route.fulfill(status=503, json={"error": "fixture retryable preview failure"})
        else:
            held_retry.append(route)

    page.route("**/api/message?id=*", detail)
    try:
        page.goto(page.fixture_launch_url)
        rows = page.locator(".message-row")
        expect(rows).to_have_count(26)
        ids["first"] = rows.nth(0).get_attribute("data-message-id")
        ids["second"] = rows.nth(1).get_attribute("data-message-id")
        rows.nth(0).locator(".row-open").click()
        retry = page.get_by_role("button", name="Retry message", exact=True)
        expect(retry).to_be_visible()
        retry.click()
        until(lambda: len(held_retry) == 1, "held old preview retry")
        expect(retry).to_be_disabled()
        page.evaluate(
            """id => {
              window.__oldRetryObserved = window.EmailTownMailHost.messageDetail(id).then(
                () => 'success', () => 'failure'
              );
            }""",
            ids["first"],
        )

        rows.nth(1).locator(".row-open").click()
        expect(page.locator("#previewBody")).to_contain_text("Synthetic body for alpha2.")
        expect(rows.nth(1).locator(".row-open")).to_be_focused()
        held_retry.pop(0).continue_()
        assert page.evaluate("() => window.__oldRetryObserved") == "success"
        expect(page.locator("#previewSubject")).to_have_text("Alpha recovery 2 (synthetic)")
        expect(page.locator("#previewBody")).to_contain_text("Synthetic body for alpha2.")
        expect(page.locator("#previewBody")).not_to_contain_text("alpha1")
        expect(page.locator("#previewError")).to_be_hidden()
        expect(rows.nth(1).locator(".row-open")).to_be_focused()
        assert counts["first"] == 2
    finally:
        abort_routes(held_retry)


def check_unauthorized_state(page, manager, readers):
    counts = {"session": 0, "immediate": 0}
    track_session(page, counts)

    def expired(route):
        counts["immediate"] += 1
        route.fulfill(status=401, json={"error": "fixture session expired"})

    page.route("**/api/state*", expired)
    page.goto(page.fixture_launch_url)
    expect(page.locator(".error-state h3")).to_have_text("Session expired")
    expect(page.locator("#restartButton")).to_be_visible()
    expect(page.locator("#retryMailButton")).to_be_hidden()
    expect(page.locator("#retryPreviewButton")).to_be_hidden()
    assert page.evaluate("sessionStorage.getItem('email-town-session')") is None
    page.evaluate("() => window.EmailTownMailHost.loadState()")
    assert counts == {"session": 1, "immediate": 1}


def check_unauthorized_preview(page, manager, readers):
    counts = {"session": 0, "state": 0, "detail": 0}
    held_polls = []
    track_session(page, counts)

    def state(route):
        if "wait" in parse_qs(urlsplit(route.request.url).query):
            held_polls.append(route)
            return
        counts["state"] += 1
        route.continue_()

    def expired(route):
        counts["detail"] += 1
        route.fulfill(status=401, json={"error": "fixture session expired"})

    page.route("**/api/state*", state)
    page.route("**/api/message?id=*", expired)
    try:
        page.goto(page.fixture_launch_url)
        expect(page.locator(".message-row")).to_have_count(26)
        page.locator(".message-row").first.locator(".row-open").click()
        expect(page.locator(".error-state h3")).to_have_text("Session expired")
        expect(page.locator("#previewContent")).to_be_hidden()
        expect(page.locator("#restartButton")).to_be_visible()
        expect(page.locator("#retryPreviewButton")).to_be_hidden()
        expect(page.locator("#retryMailButton")).to_be_hidden()
        assert page.evaluate("sessionStorage.getItem('email-town-session')") is None
        page.evaluate("() => window.EmailTownMailHost.loadState()")
        assert counts == {"session": 1, "state": 1, "detail": 1}
    finally:
        abort_routes(held_polls)


def main():
    selected = os.environ.get("EMAIL_TOWN_UI_ENGINE")
    engines = (selected,) if selected else ("chromium", "webkit")
    with sync_playwright() as playwright:
        for engine in engines:
            root = Path(tempfile.mkdtemp(prefix=f"email-town-recovery-{engine}-"))
            manager, readers = workbench(root)
            server = LocalServer(manager)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            browser = getattr(playwright, engine).launch()
            try:
                for check in (
                    check_initial_retry,
                    check_background_recovery,
                    check_preview_retry,
                    check_preview_failed_retry,
                    check_preview_failure_race,
                    check_preview_retry_race,
                    check_unauthorized_state,
                    check_unauthorized_preview,
                ):
                    context = browser.new_context(viewport={"width": 1440, "height": 900})
                    page = context.new_page()
                    page.fixture_launch_url = server.launch_url
                    until.page = page
                    errors = []
                    page.on("pageerror", lambda error, errors=errors: errors.append(str(error)))
                    page.on("dialog", lambda dialog: dialog.accept())
                    try:
                        check(page, manager, readers)
                        assert not errors, errors
                    finally:
                        context.close()
                    print(f"{engine}: {check.__name__} pass")
            finally:
                browser.close()
                server.shutdown()
                server.server_close()
                manager.close()
    print(f"recovery UI passed ({', '.join(engines)})")


if __name__ == "__main__":
    main()
