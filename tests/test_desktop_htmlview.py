"""The formatted (HTML) message view: sanitizer, capability addresses, and the served document's policy."""

import json
import threading

import pytest

from email_town_labeler.desktop.htmlview import remote_count, sanitize, view_csp
from email_town_labeler.desktop.server import LocalServer
from tests.test_desktop_images import PNG
from tests.test_desktop_manager import FakeReader, _record, _workbench
from tests.test_desktop_server import request

CID = [{"id": "img", "content_id": "<photo@x>", "type": "image/png", "size": 10}]


def clean(html, remote=False, attachments=()):
    document, blocked = sanitize(html, attachments, image_base="/view/T/image/", remote=remote)
    body = document.split("<body>", 1)[1].rsplit("</body>", 1)[0]
    return body, blocked


# ─── sanitizer: what must never survive ───


@pytest.mark.parametrize(
    "html",
    [
        "<script>alert(1)</script>",
        "<SCRIPT SRC=//evil/x.js></SCRIPT>",
        '<img src=x onerror="alert(1)">',
        "<body onload=alert(1)>",
        '<a href="javascript:alert(1)">x</a>',
        '<a href=" JaVaScRiPt:alert(1)">x</a>',
        '<a href="data:text/html,<script>alert(1)</script>">x</a>',
        '<iframe src="https://evil"></iframe>',
        '<object data="x"></object><embed src="x">',
        "<svg><script>alert(1)</script></svg>",
        '<svg onload="alert(1)"/>',
        '<math><mi xlink:href="javascript:alert(1)">x</mi></math>',
        '<form action="https://evil"><input name=p></form>',
        '<meta http-equiv="refresh" content="0;url=https://evil">',
        '<base href="https://evil/">',
        '<link rel=stylesheet href="https://evil/x.css">',
        '<div style="background:url(javascript:alert(1))">x</div>',
        '<div style="width:expression(alert(1))">x</div>',
        "<style>@import url(https://evil/x.css);</style>",
        "<style>a{} </style><script>alert(1)</script><style>",
        "<!--[if mso]><script>alert(1)</script><![endif]-->",
        '<img src="data:image/svg+xml;base64,PHN2Zz4=">',
        "<noscript><p>x</p><script>alert(1)</script></noscript>",
        "<template><img src=x onerror=alert(1)></template>",
        '<p title="&quot;><script>alert(1)</script>">x</p>',
    ],
)
def test_active_content_never_survives(html):
    body, _ = clean(html)
    lowered = body.lower()
    for needle in (
        "<script",
        "onerror",
        "onload",
        "javascript:",
        "<iframe",
        "<object",
        "<embed",
        "<svg",
        "<math",
        "<form",
        "<input",
        "<meta http",
        "<base",
        "<link",
        "expression(",
        "@import",
        "image/svg",
        "https://evil",
        "data:text",
    ):
        assert needle not in lowered, (needle, body)


def test_quotes_in_attributes_are_escaped_not_breaking_out():
    body, _ = clean('<p title="&quot;><b>">x</p>')
    assert body == '<p title="&quot;&gt;&lt;b&gt;">x</p>'


def test_layout_and_text_survive():
    html = (
        "<html><head><title>T</title><style>.x{color:red}</style></head>"
        '<body bgcolor="#fff"><table width="600" cellpadding="0"><tr><td align="center" style="padding:8px">'
        "<h1>Recap</h1><p>Hi <b>there</b> &amp; welcome</p></td></tr></table></body></html>"
    )
    body, blocked = clean(html)
    assert "<style>.x{color:red}</style>" in body
    assert '<table width="600" cellpadding="0">' in body
    assert '<td align="center" style="padding:8px">' in body
    assert "<p>Hi <b>there</b> &amp; welcome</p>" in body
    assert "<title>" not in body and ">T<" not in body
    assert blocked == 0


def test_links_open_outside_and_only_web_or_mail_schemes_are_kept():
    body, _ = clean(
        '<a href="https://tldv.io/x?a=1&b=2">View</a><a href="mailto:a@b.c">m</a><a href="#top">t</a>'
    )
    assert (
        '<a href="https://tldv.io/x?a=1&amp;b=2" target="_blank" rel="noopener noreferrer">View</a>' in body
    )
    assert 'href="mailto:a@b.c"' in body
    assert "<a>t</a>" in body


def test_external_images_wait_for_the_reader_and_are_counted():
    html = '<img src="https://img.example.com/a.png" alt="Logo"><img src="http://insecure.example.com/b.png">'
    body, blocked = clean(html)
    assert "img.example.com" not in body and 'alt="Logo"' in body and "data-blocked" in body
    assert blocked == 1  # plain http is never loaded, so it is not offered either
    assert remote_count(html) == 1
    shown, _ = clean(html, remote=True)
    assert 'src="https://img.example.com/a.png"' in shown
    assert "insecure.example.com" not in shown


def test_private_network_images_are_never_loaded_even_when_allowed():
    shown, blocked = clean(
        '<img src="https://127.0.0.1/x.png"><img src="https://192.168.1.1/x.png">', remote=True
    )
    assert "127.0.0.1" not in shown and "192.168" not in shown and blocked == 0


def test_inline_cid_images_point_at_this_view_only():
    body, _ = clean('<img src="cid:photo@x"><img src="cid:other">', attachments=CID)
    assert '<img src="/view/T/image/img">' in body
    assert body.count("data-blocked") == 1
    ambiguous, _ = clean('<img src="cid:photo@x">', attachments=CID + [{**CID[0], "id": "two"}])
    assert "/view/" not in ambiguous


def test_raster_data_images_are_kept():
    body, _ = clean('<img src="data:image/png;base64,iVBORw0KGgo=">')
    assert 'src="data:image/png;base64,iVBORw0KGgo="' in body


def test_unclosed_dropped_tag_swallows_only_what_follows_it():
    body, _ = clean("<p>before</p><svg><p>inside")
    assert body == "<p>before</p>"


def test_view_policy_blocks_scripts_and_gates_external_images():
    strict = view_csp(False)
    assert "default-src 'none'" in strict and "img-src 'self' data:;" in strict
    assert "sandbox" in strict and "allow-scripts" not in strict and "allow-forms" not in strict
    assert "https:" in view_csp(True)


# ─── the manager and HTTP boundary ───


class HtmlReader(FakeReader):
    html = (
        '<p>Recap</p><img src="cid:photo"><img src="https://img.example.com/a.png"><script>alert(1)</script>'
    )

    def detail(self, provider_id):
        return {
            "body_text": "Recap",
            "body_html": self.html,
            "attachments": [{"id": "img", "content_id": "photo", "type": "image/png", "size": len(PNG)}],
        }

    def attachment(self, provider_id, aid):
        return {"type": "image/png"}, PNG


@pytest.fixture
def served(tmp_path):
    workbench, readers, first, _ = _workbench(tmp_path)
    reader = readers[first] = HtmlReader([_record("html"), _record("plain")])
    workbench._refresh()
    server = LocalServer(workbench)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        server.reader = reader
        yield server, workbench
    finally:
        server.shutdown()
        server.server_close()
        workbench.close()


def _key(workbench):
    return workbench.snapshot()["messages"][0]["id"]


def test_detail_announces_html_without_carrying_it(served):
    _server, workbench = served
    detail = workbench.detail(_key(workbench))
    assert detail["has_html"] is True and detail["external_images"] == 1
    assert "html" not in detail and "body_html" not in detail
    assert "html" not in json.dumps(workbench.snapshot()["messages"])


def test_view_address_needs_the_session_and_serves_an_inert_document(served):
    server, workbench = served
    key = _key(workbench)
    assert request(server, f"/api/message-view?id={key}")[0] == 401
    status, _headers, body = request(server, f"/api/message-view?id={key}", auth=True)
    assert status == 200
    url = json.loads(body)["url"]
    assert url.startswith("/view/") and len(url) > 40

    status, headers, document = request(server, url)  # the iframe cannot send the bearer header
    assert status == 200 and headers["Content-Type"].startswith("text/html")
    assert headers["Content-Security-Policy"] == view_csp(False)
    text = document.decode()
    assert "<p>Recap</p>" in text and "<script" not in text
    assert "img.example.com" not in text
    image = text.split('<img src="', 1)[1].split('"', 1)[0]
    assert image == url + "/image/img"
    status, headers, raw = request(server, image)
    assert status == 200 and raw == PNG and headers["Content-Type"] == "image/png"
    assert request(server, url + "/image/unrelated")[0] == 400

    status, _headers, body = request(server, f"/api/message-view?id={key}&remote=1", auth=True)
    remote = json.loads(body)["url"]
    status, headers, document = request(server, remote)
    assert "https://img.example.com/a.png" in document.decode()
    assert headers["Content-Security-Policy"] == view_csp(True)


def test_unknown_expired_and_malformed_view_addresses_are_refused(served, monkeypatch):
    server, workbench = served
    assert request(server, "/view/guess")[0] == 404
    assert request(server, "/view/")[0] == 404
    _status, _h, body = request(server, f"/api/message-view?id={_key(workbench)}", auth=True)
    url = json.loads(body)["url"]
    assert request(server, url + "/other/img")[0] == 404
    token = url.split("/")[2]
    key, remote, _expires = server.views[token]
    server.views[token] = (key, remote, 0.0)
    assert request(server, url)[0] == 404
    assert token not in server.views


def test_view_addresses_are_bounded(served):
    server, workbench = served
    key = _key(workbench)
    for _ in range(40):
        server.issue_view(key, False)
    assert len(server.views) == 32


def test_plain_message_has_no_formatted_view(served):
    server, workbench = served
    server.reader.html = ""
    key = workbench.snapshot()["messages"][1]["id"]
    workbench.bodies.pop(key, None)
    assert workbench.detail(key)["has_html"] is False
    assert request(server, f"/api/message-view?id={key}", auth=True)[0] == 400


@pytest.mark.parametrize("tag", ["script", "noscript", "template", "svg", "title", "iframe", "textarea"])
def test_dropped_containers_take_their_text_with_them(tag):
    body, _ = clean(f"<p>keep</p><{tag}>secret code</{tag}><p>after</p>")
    assert body == "<p>keep</p><p>after</p>"


def test_formatted_view_survives_a_mail_action_dropping_the_cached_body(served):
    """Opening a message marks it read, and every mail action drops that message's cached body. The view
    must use the body it just loaded, not look the cache up again (it raised "no formatted version")."""
    from collections import OrderedDict

    _server, workbench = served
    key = _key(workbench)
    workbench.detail(key)

    class DropAfterRead(OrderedDict):
        def get(self, k, default=None):
            value = super().get(k, default)
            self.pop(k, None)  # the mark-read write lands right after this lookup
            return value

    workbench.bodies = DropAfterRead(workbench.bodies)
    document, blocked = workbench.html_view(key, False, "/view/T/image/")
    assert "<p>Recap</p>" in document and blocked == 1
