"""Synthetic image messages across provider parsing and the preview boundary."""

import base64
import threading
from email.message import EmailMessage

import pytest

from email_town_labeler.config import Config
from email_town_labeler.desktop.images import body_images
from email_town_labeler.desktop.providers import GmailReader, ImapReader, MicrosoftReader
from email_town_labeler.desktop.server import LocalServer
from email_town_labeler.gmail.client import GmailClient
from tests.fakes.fake_service import FakeGmail
from tests.test_desktop_manager import FakeReader, _record, _workbench
from tests.test_desktop_server import request

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWQAAAABJRU5ErkJggg=="
)


def image_message():
    message = EmailMessage()
    message["From"] = "sender@example.com"
    message["Subject"] = "Synthetic images"
    message.set_content("Plain alternative")
    message.add_alternative('<p>Hello</p><img src="cid:photo%40example.com" alt="Photo">', subtype="html")
    message.get_payload()[1].add_related(
        b"synthetic bytes", maintype="image", subtype="png", cid="<photo@example.com>"
    )
    return message


def test_gmail_preview_retains_html_and_unnamed_cid_without_changing_classifier_input():
    service = FakeGmail()
    mid = service.deliver(image_message().as_bytes())
    labels_before = service.labels_of(mid)
    client = GmailClient(service, max_gets_per_minute=None)
    reader = GmailReader.__new__(GmailReader)
    reader.client, reader.cfg = client, Config()
    reader.address, reader.identity = "owner@example.com", "fixture"
    reader.cache, reader.history_id, reader.folder_ids = {}, None, {}
    rows, _ = reader.list(10)
    detail = reader.detail(rows[0]["provider_id"])
    assert "cid:photo" in detail["body_html"]
    assert detail["attachments"][0]["content_id"] == "photo@example.com"
    assert detail["attachments"][0]["id"]
    _meta, raw = reader.attachment(rows[0]["provider_id"], detail["attachments"][0]["id"])
    assert raw == b"synthetic bytes"
    assert service.labels_of(mid) == labels_before
    default = client.get_records([mid])[0]
    assert default["body_text"].strip() == "Plain alternative"
    assert "body_html" not in default


def test_imap_preview_retains_html_and_unnamed_cid():
    detail = ImapReader.parse(image_message().as_bytes(), "1", b"FLAGS ()")
    assert "cid:photo" in detail["body_html"]
    assert detail["body_text"].strip() == "Plain alternative"
    image = body_images(detail["body_html"], detail["attachments"])[0]
    assert image["attachment_id"] == detail["attachments"][0]["id"]
    assert image["type"] == "image/png"


def test_graph_inline_only_mail_looks_up_content_id_even_without_has_attachments(monkeypatch, tmp_path):
    reader = MicrosoftReader({"token_path": str(tmp_path / "absent")})
    reader.identity, reader.cache = "fixture", {"m": {"id": "m", "receivedDateTime": "2026-11-04T12:00:00Z"}}
    urls = []

    def get(url):
        urls.append(url)
        if "/attachments?" in url:
            return {
                "value": [
                    {"id": "img", "contentId": "photo@example.com", "contentType": "image/png", "size": 10}
                ]
            }
        return {
            "hasAttachments": False,
            "body": {"contentType": "html", "content": '<img src="cid:photo@example.com">'},
        }

    monkeypatch.setattr(reader, "get", get)
    detail = reader.detail("fixture:m")
    assert any("/attachments?" in url and "contentId" in url for url in urls)
    assert body_images(detail["body_html"], detail["attachments"])[0]["attachment_id"] == "img"


@pytest.mark.parametrize(
    "src",
    [
        "javascript:alert(1)",
        "file:///tmp/image.png",
        "http://example.com/a.png",
        "//example.com/a.png",
        "data:image/svg+xml,unsafe",
        "https://user:pass@example.com/a",
        "https://localhost/a",
        "https://127.0.0.1/a",
        "https://[::1]/a",
        "https://127.1/a",
        "https://0x7f.1/a",
        "https://0177.0.0.1/a",
    ],
)
def test_unsafe_image_sources_are_not_exposed(src):
    assert not body_images(f'<img src="{src}">', [])


def test_images_are_bounded_deduplicated_and_do_not_execute_html():
    html = '<script>alert(1)</script><img src="https://images.example.com/a.png?x=1&amp;y=2" alt="&lt;script&gt;">'
    images = body_images(html * 100, [])
    assert images == [{"url": "https://images.example.com/a.png?x=1&y=2", "alt": "<script>"}]
    assert (
        len(body_images("".join(f'<img src="https://images.example.com/{i}">' for i in range(100)), [])) == 50
    )
    assert not body_images(
        '<img src="cid:missing"><img src="cid:svg">',
        [{"id": "x", "content_id": "svg", "type": "image/svg+xml"}],
    )


def test_ambiguous_or_oversized_inline_images_do_not_download():
    attachment = {"id": "img", "content_id": "photo", "type": "image/png", "size": 10}
    html = '<img src="cid:photo">'
    assert not body_images(html, [attachment, {**attachment, "id": "other"}])
    assert not body_images(html, [{**attachment, "size": 8 * 1024 * 1024 + 1}])
    assert not body_images(html, [{**attachment, "size": None}])


def test_preview_exposes_image_descriptors_without_retaining_raw_html(tmp_path):
    workbench, readers, first, _ = _workbench(tmp_path)
    readers[first] = FakeReader([_record("image")])
    workbench._refresh()
    key = workbench.snapshot()["messages"][0]["id"]
    try:
        workbench._cache_body(
            key, {"body_text": "Hello", "body_html": '<img src="https://images.example.com/photo.png">'}
        )
        detail = workbench.detail(key)
        assert detail["images"][0]["url"] == "https://images.example.com/photo.png"
        assert "body_html" not in detail
        assert "body_html" not in workbench.bodies[key]
        assert "images" not in workbench.snapshot()["messages"][0]
    finally:
        workbench.close()


def test_only_referenced_raster_images_cross_authenticated_http_boundary(tmp_path):
    workbench, readers, first, _ = _workbench(tmp_path)

    class ImageReader(FakeReader):
        data = PNG

        def detail(self, provider_id):
            return {
                "body_html": '<img src="cid:photo">',
                "attachments": [
                    {"id": "img", "content_id": "photo", "type": "image/png", "size": len(self.data)}
                ],
            }

        def attachment(self, provider_id, aid):
            assert aid == "img"
            return {"type": "image/png"}, self.data

    reader = readers[first] = ImageReader([_record("image")])
    workbench._refresh()
    key = workbench.snapshot()["messages"][0]["id"]
    server = LocalServer(workbench)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    path = f"/api/image?id={key}&attachment_id=img"
    try:
        assert request(server, path)[0] == 401
        status, headers, raw = request(server, path, auth=True)
        assert status == 200 and raw == PNG
        assert headers["Content-Type"] == "image/png"
        assert headers["Cache-Control"] == "no-store"
        assert request(server, f"/api/image?id={key}&attachment_id=unrelated", auth=True)[0] == 400
        reader.data = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        assert request(server, path, auth=True)[0] == 400
    finally:
        server.shutdown()
        server.server_close()
        workbench.close()
