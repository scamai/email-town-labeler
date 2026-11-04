"""Image loading, opt-in privacy and stale-preview checks against a synthetic local mailbox.

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
                    "<script>window.senderScriptRan=true</script><p>Images</p>"
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

    manager = Workbench(out / "workbench", Paths.default(out / "home").ensure(), reader_factory=factory)
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
            browser = playwright.chromium.launch()
            for theme, width in (("light", 390), ("dark", 390), ("light", 1440), ("dark", 1440)):
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

                open_row("Fixture received")
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
                page.screenshot(path=str(out / f"{theme}-{width}.png"))

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
        f"image UI passed: 4 viewport/theme combinations; inline, remote opt-in, failure, switch, auth. Captures: {out}"
    )


if __name__ == "__main__":
    main()
