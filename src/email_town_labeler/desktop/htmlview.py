"""The formatted view of an HTML message: sender HTML reduced to an allowlist, served as its own document.

Three independent layers keep sender HTML inert, so a gap in one is not a hole:
1. `sanitize` keeps only layout/text tags and presentational attributes. Scripts, frames, forms, SVG/MathML,
   comments (Outlook conditional HTML), event handlers and every non-http(s)/mailto link are dropped.
2. The document is served with `VIEW_CSP`: no script, no connection, no form, no font, no frame; images
   only from this server (CID parts) and data: — plus https: only after the reader loads external images.
3. The reading pane embeds it in an iframe whose sandbox omits allow-scripts, allow-forms and
   allow-top-navigation; links carry target=_blank and open in the system browser.

External images are replaced by their alt text until the reader asks for them, because loading one tells
the sender the message was opened.
"""

from __future__ import annotations

import re
from html import escape
from html.parser import HTMLParser
from urllib.parse import quote, unquote, urlsplit

from .images import MAX_IMAGE_BYTES, RASTER_TYPES, remote_image_url

MAX_HTML = 262144
BLANK_PIXEL = "data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw=="

# Tags kept as they are (attributes filtered).
ALLOWED = frozenset(
    [
        "a",
        "abbr",
        "address",
        "b",
        "bdi",
        "bdo",
        "big",
        "blockquote",
        "br",
        "caption",
        "center",
        "cite",
        "code",
        "col",
        "colgroup",
        "dd",
        "del",
        "dfn",
        "div",
        "dl",
        "dt",
        "em",
        "figcaption",
        "figure",
        "font",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "hr",
        "i",
        "img",
        "ins",
        "kbd",
        "li",
        "mark",
        "ol",
        "p",
        "pre",
        "q",
        "s",
        "samp",
        "small",
        "span",
        "strike",
        "strong",
        "sub",
        "sup",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "tr",
        "tt",
        "u",
        "ul",
        "wbr",
    ]
)
VOID = frozenset({"br", "col", "hr", "img", "wbr"})
# Dropped together with everything inside them.
DROP_CONTENT = frozenset(
    [
        "script",
        "noscript",
        "template",
        "iframe",
        "frame",
        "frameset",
        "object",
        "embed",
        "applet",
        "svg",
        "math",
        "title",
        "textarea",
        "select",
        "button",
        "audio",
        "video",
        "canvas",
        "map",
        "xmp",
        "plaintext",
        "noembed",
        "noframes",
    ]
)
# Attributes kept on any allowed tag; everything else (on*, src/href outside their tags, data-*) is dropped.
ATTRS = frozenset(
    [
        "align",
        "valign",
        "width",
        "height",
        "bgcolor",
        "border",
        "cellpadding",
        "cellspacing",
        "colspan",
        "rowspan",
        "dir",
        "lang",
        "title",
        "alt",
        "color",
        "face",
        "size",
        "class",
        "id",
        "style",
        "start",
        "type",
        "nowrap",
        "role",
        "summary",
        "headers",
        "scope",
        "span",
        "frame",
        "rules",
        "hspace",
        "vspace",
    ]
)
LINK_SCHEMES = ("http", "https", "mailto")
UNSAFE_CSS = re.compile(
    r"expression\s*\(|javascript:|vbscript:|-moz-binding|behavior\s*:|@import", re.IGNORECASE
)


def view_csp(remote: bool) -> str:
    images = "'self' data: https:" if remote else "'self' data:"
    return (
        f"default-src 'none'; img-src {images}; style-src 'unsafe-inline'; form-action 'none'; "
        "base-uri 'none'; frame-ancestors 'self'; "
        "sandbox allow-same-origin allow-popups allow-popups-to-escape-sandbox"
    )


def safe_link(value: str) -> str:
    value = (value or "").strip()
    if not value or len(value) > 4096 or any(ord(c) < 32 for c in value):
        return ""
    try:
        scheme = urlsplit(value).scheme.lower()
    except ValueError:
        return ""
    return value if scheme in LINK_SCHEMES else ""


def clean_css(text: str) -> str:
    """Inline/embedded CSS minus the constructs that run code or pull other stylesheets."""
    text = text.replace("<", "").replace("\\", "")  # no markup breakout, no escape-obfuscated keywords
    return "" if UNSAFE_CSS.search(text) else text


def raster_cids(attachments) -> dict[str, str]:
    """Content-ID -> attachment id for inline images the server will serve; ambiguous IDs map to nothing."""
    out: dict[str, str | None] = {}
    for attachment in attachments or []:
        cid = str(attachment.get("content_id") or "").strip().strip("<>")
        if not cid:
            continue
        size = attachment.get("size", 0)
        usable = (
            attachment.get("id")
            and attachment.get("type") in RASTER_TYPES
            and isinstance(size, int)
            and 0 <= size <= MAX_IMAGE_BYTES
        )
        out[cid] = None if cid in out or not usable else str(attachment["id"])
    return {k: v for k, v in out.items() if v}


class _Sanitizer(HTMLParser):
    def __init__(self, cids, image_base, remote):
        super().__init__(convert_charrefs=True)
        self.cids, self.image_base, self.remote = cids, image_base, remote
        self.out: list[str] = []
        self.skip: list[str] = []  # stack of DROP_CONTENT tags we are inside
        self.in_style = False
        self.blocked = 0

    # ─── tags ───
    def handle_starttag(self, tag, attrs):
        self._start(tag, attrs, closed=False)

    def handle_startendtag(self, tag, attrs):
        self._start(tag, attrs, closed=True)

    def _start(self, tag, attrs, closed):
        if self.skip:
            if tag in DROP_CONTENT and not closed:
                self.skip.append(tag)
            return
        if tag in DROP_CONTENT:
            if not closed:
                self.skip.append(tag)
            return
        if tag == "style":
            self.in_style = not closed
            if not closed:
                self.out.append("<style>")
            return
        if tag not in ALLOWED:
            return  # html/head/body/meta/link/base/form/input/...: the tag goes, its text stays
        kept = []
        for name, value in attrs:
            name, value = name.lower(), value or ""
            if name in ATTRS:
                if name == "style":
                    value = clean_css(value)
                    if not value:
                        continue
                kept.append((name, value))
        if tag == "a":
            href = safe_link(dict(attrs).get("href") or "")
            if href:
                kept += [("href", href), ("target", "_blank"), ("rel", "noopener noreferrer")]
        if tag == "img":
            src = self._image(dict(attrs).get("src") or "")
            if src:
                kept.append(("src", src))
            else:
                # A transparent pixel keeps the sender's layout (width/height) without the engine's
                # broken-image frame; the alt text stays for screen readers.
                kept += [("src", BLANK_PIXEL), ("data-blocked", "")]
        rendered = "".join(f' {n}="{escape(v, quote=True)}"' for n, v in kept)
        self.out.append(f"<{tag}{rendered}>")
        if closed and tag not in VOID:
            self.out.append(f"</{tag}>")

    def _image(self, src):
        src = src.strip()
        if src[:4].lower() == "cid:":
            attachment_id = self.cids.get(unquote(src[4:]).strip().strip("<>"))
            return self.image_base + quote(attachment_id, safe="") if attachment_id else ""
        if src[:5].lower() == "data:":
            # Inline raster data only; data:image/svg+xml can carry markup.
            kind = src[5:].split(";", 1)[0].split(",", 1)[0].lower()
            return src if kind in RASTER_TYPES and len(src) <= MAX_IMAGE_BYTES else ""
        url = remote_image_url(src)
        if url and not self.remote:
            self.blocked += 1
            return ""
        return url

    def handle_endtag(self, tag):
        if self.skip:
            if tag == self.skip[-1]:
                self.skip.pop()
            return
        if tag == "style" and self.in_style:
            self.in_style = False
            self.out.append("</style>")
        elif tag in ALLOWED and tag not in VOID:
            self.out.append(f"</{tag}>")

    # ─── text ───
    def handle_data(self, data):
        if self.skip:
            return
        self.out.append(clean_css(data) if self.in_style else escape(data, quote=False))

    # Comments, doctypes, processing instructions and CDATA are dropped by not overriding their handlers.


BASE_CSS = (
    "html{color-scheme:light;background:#fff}"
    "body{margin:0;padding:16px;color:#202124;background:#fff;"
    "font:14px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;"
    "overflow-wrap:anywhere}"
    "img{max-width:100%;height:auto}"
    "img[data-blocked]{background:#f1f3f4}"
    "table{max-width:100%}"
    "pre{white-space:pre-wrap}"
    "a{color:#1a73e8}"
)


def sanitize(html, attachments=(), *, image_base: str, remote: bool) -> tuple[str, int]:
    """Return (a complete, inert HTML document, number of external images left out)."""
    parser = _Sanitizer(raster_cids(attachments), image_base, remote)
    parser.feed(str(html or "")[:MAX_HTML])
    parser.close()
    if parser.in_style:
        parser.out.append("</style>")
    body = "".join(parser.out)
    document = (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta name="referrer" content="no-referrer">'
        f"<style>{BASE_CSS}</style></head><body>{body}</body></html>"
    )
    return document, parser.blocked


def remote_count(html) -> int:
    """External images a message would load: the number on the "Load external images" button."""
    return sanitize(html, image_base="/", remote=False)[1]
