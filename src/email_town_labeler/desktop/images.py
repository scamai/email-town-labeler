"""Bounded image references for the plaintext reader; never render sender HTML."""

from html.parser import HTMLParser
from ipaddress import ip_address
from urllib.parse import unquote, urlsplit

RASTER_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp", "image/avif", "image/bmp"})
MAX_IMAGES = 50
MAX_IMAGE_BYTES = 8 * 1024 * 1024


def remote_image_url(value):
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 33 for c in value):
        return ""
    try:
        url = urlsplit(value)
        host = (url.hostname or "").lower().rstrip(".")
        if url.scheme != "https" or not host or url.username or url.password or url.port not in (None, 443):
            return ""
        if "\\" in value or "%" in host or "." not in host or host.endswith((".localhost", ".local")):
            return ""
        # Browsers accept legacy numeric IPv4 spellings (127.1, 0x7f.1, 0177.0.0.1).
        if host.rsplit(".", 1)[-1].isdigit():
            try:
                if not ip_address(host).is_global:
                    return ""
            except ValueError:
                return ""
        try:
            if not ip_address(host).is_global:
                return ""
        except ValueError:
            pass
        return value
    except ValueError:
        return ""


class _Images(HTMLParser):
    def __init__(self, attachments):
        super().__init__(convert_charrefs=True)
        self.images, self.seen = [], set()
        self.cids = {}
        for attachment in attachments:
            cid = str(attachment.get("content_id") or "").strip().strip("<>")
            if cid:
                # An ambiguous Content-ID must not pick an arbitrary attachment.
                self.cids[cid] = None if cid in self.cids else attachment

    def handle_starttag(self, tag, attrs):
        if tag != "img" or len(self.images) >= MAX_IMAGES:
            return
        attrs = dict(attrs)
        src = (attrs.get("src") or "").strip()
        if not src or src in self.seen:
            return
        image = {"alt": (attrs.get("alt") or "")[:256]}
        if src[:4].lower() == "cid:":
            attachment = self.cids.get(unquote(src[4:]).strip().strip("<>"))
            if not attachment or not attachment.get("id") or attachment.get("type") not in RASTER_TYPES:
                return
            size = attachment.get("size", 0)
            if not isinstance(size, int) or not 0 <= size <= MAX_IMAGE_BYTES:
                return
            image.update(attachment_id=attachment["id"], type=attachment["type"])
        elif url := remote_image_url(src):
            image["url"] = url
        else:
            return
        self.seen.add(src)
        self.images.append(image)


def body_images(html, attachments):
    parser = _Images(attachments)
    parser.feed(str(html or "")[:262144])
    parser.close()
    return parser.images


def valid_image_bytes(kind, raw):
    """Reject active document formats and mislabeled downloads before giving them an image MIME type."""
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        return False
    return {
        "image/png": raw.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/jpeg": raw.startswith(b"\xff\xd8\xff"),
        "image/gif": raw.startswith((b"GIF87a", b"GIF89a")),
        "image/webp": raw[:4] == b"RIFF" and raw[8:12] == b"WEBP",
        "image/avif": raw[4:8] == b"ftyp" and raw[8:12] in (b"avif", b"avis"),
        "image/bmp": raw.startswith(b"BM"),
    }.get(kind, False)
