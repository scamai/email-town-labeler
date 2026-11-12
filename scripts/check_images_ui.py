"""Formatted (HTML) view, image loading, opt-in privacy and stale-preview checks against a synthetic mailbox.

WebKit, the engine of the macOS app (WKWebView), and Chromium (close to Windows' WebView2).

Run: uv run --no-project --with-editable '.[dev]' --with playwright python scripts/check_images_ui.py
"""

from __future__ import annotations

import struct
import tempfile
import threading
import zlib
from pathlib import Path

from check_mail_ui import FixtureReader
from playwright.sync_api import expect, sync_playwright

from email_town_labeler.config import Paths
from email_town_labeler.desktop.manager import Workbench
from email_town_labeler.desktop.server import LocalServer


def fixture_png():
    """A visible synthetic swatch, so captures verify image size and responsive layout too."""

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    pixels = b"".join(
        b"\0" + b"".join(bytes((32 + x // 3, 60 + y // 2, 150)) for x in range(320)) for y in range(160)
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 320, 160, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(pixels))
        + chunk(b"IEND", b"")
    )


PNG = fixture_png()


class ImageReader(FixtureReader):
    def __init__(self, account):
        super().__init__(account)
        self.release = threading.Event()
        self.release.set()
        self.started = threading.Event()

    def detail(self, provider_id):
        detail = super().detail(provider_id)
        if provider_id == "ok":
            detail.update(
                body_html=(
                    "<script>window.senderScriptRan=true</script>"
                    '<table width="600" style="border:1px solid #ccc"><tr><td style="padding:16px">'
                    '<h1 style="color:#0b57d0">Meeting recap</h1><p>Images</p>'
                    '<a href="https://recap.example.com/view" style="background:#0b57d0;color:#fff;padding:8px">'
                    "View meeting recap</a></td></tr></table>"
                    '<img src="cid:photo" alt="Embedded photo" onload="window.senderScriptRan=true">'
                    '<img src="https://images.example.com/photo.png" alt="External photo">'
                    '<img src="https://images.example.com/broken.png" alt="Missing external photo">'
                ),
                attachments=[
                    {
                        "id": "photo",
                        "name": "photo.png",
                        "content_id": "photo",
                        "type": "image/png",
                        "size": len(PNG),
                    }
                ],
            )
        return detail

    def attachment(self, provider_id, attachment_id):
        assert provider_id == "ok" and attachment_id == "photo"
        self.started.set()
        assert self.release.wait(10), "test image release timed out"
        return {"type": "image/png"}, PNG


def main():
    out = Path(tempfile.mkdtemp(prefix="email-town-images-ui-"))
    readers = []

    def factory(account):
        reader = ImageReader(account)
        readers.append(reader)
        return reader

    paths = Paths.default(out / "home").ensure()
    # Offline and deterministic: keyword rules, never the desktop default model's 263 MB download.
    paths.config_file.write_text('[model]\nref = "rules"\n')
    manager = Workbench(out / "workbench", paths, reader_factory=factory)
    manager.demo = True
    manager.accounts.add(
        "Fixture", "imap", {"host": "fixture", "username": "fixture@example.com", "port": 993}
    )
    manager._refresh()
    reader = readers[-1]
    server = LocalServer(manager)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            for engine, theme, width in (
                ("webkit", "light", 390),
                ("webkit", "dark", 1440),
                ("chromium", "light", 1440),
                ("chromium", "dark", 390),
            ):
                browser = getattr(playwright, engine).launch()
                context = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=theme)
                context.add_init_script("""(() => {
                  const create = URL.createObjectURL.bind(URL), revoke = URL.revokeObjectURL.bind(URL);
                  window.imageURLs = new Set();
                  URL.createObjectURL = (blob) => { const u = create(blob); imageURLs.add(u); return u; };
                  URL.revokeObjectURL = (u) => { imageURLs.delete(u); revoke(u); };
                })();""")
                page = context.new_page()
                errors, external = [], []
                page.on("pageerror", lambda error, errors=errors: errors.append(str(error)))

                def image_route(route, _request, external=external):
                    external.append(route.request)
                    if route.request.url.endswith("broken.png"):
                        route.fulfill(status=404, body="missing")
                    else:
                        route.fulfill(content_type="image/png", body=PNG)

                page.route("https://images.example.com/**", image_route)
                page.goto(server.launch_url)
                expect(page.locator(".message-row")).to_have_count(2)

                def open_row(subject, page=page, width=width):
                    if width == 390 and page.locator("#previewPane").evaluate(
                        "el => el.classList.contains('is-open')"
                    ):
                        page.locator("#previewClose").click()
                    page.locator(".message-row").filter(has_text=subject).locator(".row-open").click()

                # ─── Formatted view: the sender's layout in a sandboxed frame ───
                open_row("Fixture received")
                frame = page.frame_locator(".email-frame")
                inline = frame.get_by_role("img", name="Embedded photo", exact=True)
                expect(inline).to_be_visible()
                assert inline.evaluate("img => img.complete && img.naturalWidth > 0")
                expect(frame.get_by_role("heading", name="Meeting recap")).to_be_visible()
                assert not external, "external image requested without consent"
                assert page.evaluate("window.senderScriptRan") is None
                assert frame.locator("body").evaluate("b => window.senderScriptRan") is None
                expect(frame.locator("script")).to_have_count(0)
                expect(page.locator(".preview-images")).to_have_count(0)
                assert page.locator(".email-frame").evaluate(
                    "f => Math.abs(f.offsetHeight - f.contentDocument.documentElement.scrollHeight) <= 2"
                ), "the frame is not sized to its message"
                expect(page.locator(".format-note")).to_have_text("2 external images hidden")
                page.screenshot(path=str(out / f"formatted-{engine}-{theme}-{width}.png"))
                context.route(
                    "https://recap.example.com/**",
                    lambda route: route.fulfill(body="recap", content_type="text/plain"),
                )
                with page.expect_popup() as popup:
                    frame.get_by_role("link", name="View meeting recap").click()
                popup.value.wait_for_url("https://recap.example.com/view")
                assert popup.value.evaluate("window.opener") is None, "the opened page can reach the app"
                popup.value.close()
                assert not external
                page.get_by_role("button", name="Show images").click()
                expect(frame.get_by_role("img", name="External photo", exact=True)).to_be_visible()
                expect(page.locator(".format-note")).to_have_text("External images shown for this message")
                for _ in range(50):  # the second (404) image may still be on its way
                    if len(external) >= 2:
                        break
                    page.wait_for_timeout(100)
                assert len(external) == 2, len(external)
                for request in external:
                    assert not {"authorization", "cookie", "x-email-town"} & request.all_headers().keys()
                assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
                page.screenshot(path=str(out / f"formatted-images-{engine}-{theme}-{width}.png"))
                # Keys pressed inside the message still drive the app: j opens the next message.
                frame.locator("h1").click()
                expect(page.locator("#previewBody")).to_be_focused()
                page.keyboard.press("j")
                expect(page.locator("#previewSubject")).to_have_text("Fixture partial failure")
                external.clear()

                # ─── Plain text: the images panel, as before ───
                open_row("Fixture received")
                page.get_by_role("button", name="Plain text").click()
                expect(page.locator(".email-frame")).to_have_count(0)
                embedded = page.get_by_role("img", name="Embedded photo", exact=True)
                expect(embedded).to_be_visible()
                assert embedded.evaluate("img => img.complete && img.naturalWidth > 0")
                assert not external, "external image requested without consent"
                assert page.evaluate("window.senderScriptRan") is None
                expect(page.locator(".preview-images script")).to_have_count(0)
                page.get_by_role("button", name="Load external images (2)").click()
                remote = page.get_by_role("img", name="External photo", exact=True)
                expect(remote).to_be_visible()
                assert remote.evaluate("img => img.complete && img.naturalWidth > 0")
                expect(page.locator(".message-image figcaption").last).to_contain_text("could not be loaded")
                assert len(external) == 2
                for request in external:
                    assert not {"authorization", "referer", "x-email-town"} & request.all_headers().keys()
                assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
                page.locator(".preview-images").scroll_into_view_if_needed()
                page.screenshot(path=str(out / f"plain-{engine}-{theme}-{width}.png"))

                open_row("Fixture partial failure")
                expect(page.locator(".preview-images")).to_have_count(0)
                assert page.evaluate("imageURLs.size") == 0
                reader.started.clear()
                reader.release.clear()
                open_row("Fixture received")
                assert reader.started.wait(5)
                open_row("Fixture partial failure")
                reader.release.set()
                expect(page.locator("#previewSubject")).to_have_text("Fixture partial failure")
                expect(page.locator(".preview-images")).to_have_count(0)
                assert page.evaluate("imageURLs.size") == 0
                assert len(external) == 2, "consent leaked to a later preview"
                open_row("Fixture received")  # the plain-text choice holds for this message
                expect(page.locator(".preview-images")).to_have_count(1)
                page.get_by_role("button", name="Show formatted").click()
                expect(page.locator(".email-frame")).to_have_count(1)
                expect(page.locator(".format-note")).to_have_text("2 external images hidden")
                assert len(external) == 2, "consent leaked to the formatted view"
                page.get_by_role("button", name="Plain text").click()

                # An expired image request must clear both the preview and the session.
                page.route("**/api/image?*", lambda route: route.fulfill(status=401, body="{}"))
                open_row("Fixture received")
                expect(page.locator("#previewContent")).to_be_hidden()
                expect(page.locator(".preview-images")).to_have_count(0)
                assert not errors, errors
                context.close()
                browser.close()
    finally:
        reader.release.set()
        server.shutdown()
        server.server_close()
        manager.close()
    print(
        "image UI passed: WebKit + Chromium, 2 widths x 2 themes; formatted frame, inline, remote opt-in, "
        f"links, keys, plain-text toggle, failure, switch, auth. Captures: {out}"
    )


if __name__ == "__main__":
    main()
