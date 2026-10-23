"""gmail.parse: format=full payload -> EmailRecord. Made-up mail only."""

import base64

from email_town_labeler.gmail.parse import MAX_BODY_CHARS, html_to_text, parse_message
from tests.fakes.fake_service import FakeGmail


def b64(data: bytes, pad: bool = False) -> str:
    s = base64.urlsafe_b64encode(data).decode()
    return s if pad else s.rstrip("=")


def part(mime, data=b"", charset=None, filename="", parts=None):
    ct = mime + (f'; charset="{charset}"' if charset else "")
    p = {
        "mimeType": mime,
        "filename": filename,
        "headers": [{"name": "Content-Type", "value": ct}],
        "body": {"size": len(data), **({"data": b64(data)} if data else {})},
    }
    if parts:
        p["parts"] = parts
    return p


def msg(payload, **kw):
    return {
        "id": "m1",
        "threadId": "t1",
        "labelIds": ["INBOX"],
        "internalDate": "1700000000000",
        "historyId": "42",
        "sizeEstimate": 123,
        "snippet": "snip",
        "payload": payload,
        **kw,
    }


def via_fake(**fields):
    g = FakeGmail()
    mid = g.deliver(**fields)
    return parse_message(g.users().messages().get(userId="me", id=mid, format="full").execute())


def test_multipart_alternative_prefers_plain():
    rec = via_fake(subject="Lunch?", body="Are you free at noon?", html="<p>Are you <b>free</b>?</p>")
    assert rec["body_source"] == "plain"
    assert rec["body_text"].strip() == "Are you free at noon?"
    assert rec["headers"]["subject"] == "Lunch?"
    assert rec["has_calendar"] is False and rec["attachments"] == []
    assert isinstance(rec["internal_date"], int) and rec["history_id"]


def test_html_only_strips_tags_scripts_and_entities():
    html = (
        "<html><head><title>T</title><style>p{color:red}</style></head><body>"
        "<script>var x=1</script><h1>Sale&nbsp;today</h1><p>50% off &amp; more</p>"
        "<div>line<br>break</div></body></html>"
    )
    rec = via_fake(subject="Sale", html=html)
    assert rec["body_source"] == "html"
    t = rec["body_text"]
    assert "Sale today" in t and "50% off & more" in t and "line\nbreak" in t
    assert "color" not in t and "var x" not in t and "<" not in t


def test_html_to_text_collapses_whitespace():
    assert html_to_text("<p>a \t\xa0 b</p>\n\n\n<p>c</p>") == "a b\n\nc"


def test_calendar_invite_and_attachments():
    rec = via_fake(
        subject="Sync",
        body="See invite",
        calendar=True,
        attachments=[("notes.pdf", "application/pdf", b"%PDF-1.4 fake")],
    )
    assert rec["has_calendar"] is True
    names = {a["filename"]: a for a in rec["attachments"]}
    assert set(names) == {"invite.ics", "notes.pdf"}
    assert names["notes.pdf"]["mime"] == "application/pdf" and names["notes.pdf"]["size"] == 13


def test_calendar_detected_by_mime_without_filename():
    rec = parse_message(
        msg(
            part(
                "multipart/mixed",
                parts=[part("text/plain", b"hi"), part("text/calendar", b"BEGIN:VCALENDAR")],
            )
        )
    )
    assert rec["has_calendar"] and rec["attachments"] == []


def test_latin1_and_windows1252_bodies():
    rec = via_fake(subject="Café", body="Déjà vu über", charset="iso-8859-1")
    assert rec["body_text"].strip() == "Déjà vu über"
    assert rec["headers"]["subject"] == "Café"
    rec = parse_message(msg(part("text/plain", "“quoted” – €5".encode("cp1252"), charset="windows-1252")))
    assert rec["body_text"] == "“quoted” – €5"


def test_unknown_charset_falls_back_to_utf8():
    rec = parse_message(msg(part("text/plain", "héllo".encode(), charset="x-made-up-charset")))
    assert rec["body_text"] == "héllo"


def test_bad_bytes_and_bad_base64_do_not_crash():
    rec = parse_message(msg(part("text/plain", b"ok \xff\xfe end", charset="utf-8")))
    assert rec["body_text"].startswith("ok ") and rec["body_text"].endswith(" end")
    bad = {"mimeType": "text/plain", "headers": [], "body": {"data": "!!!not*base64"}}
    assert parse_message(msg(bad))["body_source"] in ("none", "plain")


def test_padded_and_unpadded_base64():
    for pad in (True, False):
        p = {"mimeType": "text/plain", "headers": [], "body": {"data": b64(b"abcd", pad=pad)}}
        assert parse_message(msg(p))["body_text"] == "abcd"


def test_missing_headers_and_fields():
    rec = parse_message({"id": "m9", "payload": {"mimeType": "text/plain", "body": {}}})
    assert rec["id"] == "m9" and rec["headers"] == {} and rec["body_source"] == "none"
    assert rec["label_ids"] == [] and rec["internal_date"] == 0 and rec["thread_id"] == ""
    assert rec["size_estimate"] == 0 and rec["snippet"] == ""
    rec = via_fake(subject=None, from_=None, body="no subject or sender")
    assert "subject" not in rec["headers"] and "from" not in rec["headers"]


def test_header_filtering_lowercase_and_duplicates():
    hs = [
        {"name": "Subject", "value": "S"},
        {"name": "X-Secret-Tracking", "value": "drop"},
        {"name": "List-Unsubscribe", "value": "<mailto:u@example.test>"},
        {"name": "Received", "value": "a"},
        {"name": "References", "value": "<a@x>"},
        {"name": "references", "value": "<b@x>"},
    ]
    rec = parse_message(msg({"mimeType": "text/plain", "headers": hs, "body": {}}))
    assert rec["headers"] == {
        "subject": "S",
        "list-unsubscribe": "<mailto:u@example.test>",
        "references": "<a@x>\n<b@x>",
    }


def test_nested_multipart_and_multiple_plain_parts():
    inner = part(
        "multipart/alternative", parts=[part("text/plain", b"first"), part("text/html", b"<p>x</p>")]
    )
    rec = parse_message(
        msg(part("multipart/mixed", parts=[inner, part("text/plain", b"second"), part("text/plain", b"  ")]))
    )
    assert rec["body_text"] == "first\nsecond"


def test_text_attachment_is_not_body():
    rec = parse_message(
        msg(
            part(
                "multipart/mixed",
                parts=[
                    part("text/html", b"<p>body</p>"),
                    part("text/plain", b"log file", filename="log.txt"),
                ],
            )
        )
    )
    assert rec["body_source"] == "html" and rec["body_text"] == "body"
    assert rec["attachments"][0]["filename"] == "log.txt"


def test_body_capped():
    rec = parse_message(msg(part("text/plain", b"a" * (MAX_BODY_CHARS + 50))))
    assert len(rec["body_text"]) == MAX_BODY_CHARS


def test_raw_rfc822_delivery_round_trip():
    g = FakeGmail()
    raw = (
        b"From: Ann <ann@example.test>\r\nTo: me@example.test\r\nSubject: =?utf-8?q?Caf=C3=A9?=\r\n"
        b"MIME-Version: 1.0\r\nContent-Type: text/plain; charset=utf-8\r\n"
        b"Content-Transfer-Encoding: quoted-printable\r\n\r\nCaf=C3=A9 at 3?\r\n"
    )
    mid = g.deliver(raw)
    rec = parse_message(g.users().messages().get(userId="me", id=mid, format="full").execute())
    assert rec["headers"]["subject"] == "Café" and rec["body_text"].strip() == "Café at 3?"
