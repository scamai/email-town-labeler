"""Authenticated loopback-only HTTP surface for the on-device workbench."""

from __future__ import annotations

import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from socketserver import TCPServer
from urllib.parse import parse_qs, quote, urlsplit

from ..gmail.auth import _write_private
from ..lock import InstanceLock
from .mail import filename
from .manager import Workbench
from .providers import ProviderError
from .setup import save_google_client, setup_info

MAX_BODY = 65536
SLOW_GET_MS = 500  # GETs are logged only when slower than this; /api/state is a long poll and never logged
log = logging.getLogger("email_town_labeler.desktop.http")


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, manager: Workbench, port=0):
        self.manager = manager
        self.secret = secrets.token_urlsafe(32)
        self.session = secrets.token_urlsafe(32)
        super().__init__(("127.0.0.1", port), Handler)
        self.origin = f"http://127.0.0.1:{self.server_port}"
        self.host = f"127.0.0.1:{self.server_port}"

    def server_bind(self):
        # HTTPServer resolves a hostname here. This numeric loopback listener needs no DNS,
        # and an unavailable system resolver must not delay the launch handshake.
        TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]

    @property
    def launch_url(self):
        return self.origin + "/#token=" + self.secret


class Handler(BaseHTTPRequestHandler):
    server: LocalServer
    server_version = "EmailTownLocal"

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, *_):
        pass  # Never put message IDs, credentials or request bodies in HTTP logs.

    def send_response(self, code, message=None):
        self._status = code
        super().send_response(code, message)

    def handle_one_request(self):
        """Time each API request into desktop.log: method, path (never the query), status and ms."""
        self._status, started = None, time.perf_counter()
        super().handle_one_request()
        command, raw = getattr(self, "command", None), getattr(self, "path", "") or ""
        path = urlsplit(raw).path
        if not command or not path.startswith("/api/") or path == "/api/state":
            return
        ms = (time.perf_counter() - started) * 1000
        if command == "POST" or ms >= SLOW_GET_MS:
            log.info("%s %s %s %.0f ms", command, path, self._status, ms)

    def _response(self, status, body, *, content_type="application/json; charset=utf-8", download=None):
        if isinstance(body, dict):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if download:
            self.send_header(
                "Content-Disposition", "attachment; filename*=UTF-8''" + quote(download, safe="")
            )
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: https:; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
        )
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _boundary(self, *, write=False):
        # Host check blocks DNS rebinding; origin + custom header block cross-site writes.
        if self.headers.get("Host") != self.server.host:
            self._response(403, {"error": "Only the local workbench may access this server"})
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin != self.server.origin:
            self._response(403, {"error": "Request origin is not trusted"})
            return False
        if write and (origin != self.server.origin or self.headers.get("X-Email-Town") != "local"):
            self._response(403, {"error": "Use the workbench to perform this action"})
            return False
        return True

    def _authorized(self):
        value = self.headers.get("Authorization", "")
        expected = "Bearer " + self.server.session
        if not hmac.compare_digest(value.encode(errors="replace"), expected.encode()):
            self._response(401, {"error": "Session expired. Reopen Email Town from the local launcher"})
            return False
        return True

    def do_GET(self):
        if not self._boundary():
            return
        parsed = urlsplit(self.path)
        assets = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
            "/mail.js": ("mail.js", "text/javascript; charset=utf-8"),
            "/images.js": ("images.js", "text/javascript; charset=utf-8"),
            "/shortcuts.js": ("shortcuts.js", "text/javascript; charset=utf-8"),
            "/selection.js": ("selection.js", "text/javascript; charset=utf-8"),
            "/rowactions.js": ("rowactions.js", "text/javascript; charset=utf-8"),
            "/reader.js": ("reader.js", "text/javascript; charset=utf-8"),
            "/style.css": ("style.css", "text/css; charset=utf-8"),
            "/reader.css": ("reader.css", "text/css; charset=utf-8"),
            "/logo.svg": ("logo.svg", "image/svg+xml"),
        }
        if parsed.path in assets:
            name, kind = assets[parsed.path]
            self._response(
                200, files("email_town_labeler.desktop").joinpath("web", name).read_bytes(), content_type=kind
            )
            return
        if parsed.path == "/api/proof":
            challenge = parse_qs(parsed.query).get("challenge", [""])[0]
            if not re.fullmatch(r"[a-f0-9]{64}", challenge):
                self._response(400, {"error": "Invalid request"})
                return
            proof = hmac.digest(
                self.server.secret.encode(), ("EmailTown:" + challenge).encode(), "sha256"
            ).hex()
            self._response(200, {"proof": proof})
            return
        if not self._authorized():
            return
        try:
            if parsed.path == "/api/state":
                # ?wait=<etag> is a long-poll: it answers when the state changes, so mail shows up
                # as soon as it is read instead of at the next fixed poll.
                wait = parse_qs(parsed.query).get("wait", [""])[0]
                manager = self.server.manager
                self._response(
                    200,
                    manager.wait_snapshot(wait)
                    if re.fullmatch(r"[a-f0-9]{32}", wait)
                    else manager.snapshot(),
                )
            elif parsed.path == "/api/mail-job":
                self._response(
                    200, self.server.manager.mail_store.receipt(parse_qs(parsed.query).get("id", [""])[0])
                )
            elif parsed.path == "/api/setup":
                self._response(200, setup_info(self.server.manager.paths))
            elif parsed.path == "/api/drafts":
                self._response(200, self.server.manager.mail_store.drafts())
            elif parsed.path == "/api/draft":
                self._response(
                    200, self.server.manager.mail_store.draft(parse_qs(parsed.query).get("id", [""])[0])
                )
            elif parsed.path == "/api/image":
                params = parse_qs(parsed.query)
                kind, raw = self.server.manager.image(
                    params.get("id", [""])[0], params.get("attachment_id", [""])[0]
                )
                self._response(200, raw, content_type=kind)
            elif parsed.path == "/api/attachment":
                params = parse_qs(parsed.query)
                meta, raw = self.server.manager.attachment(
                    params.get("id", [""])[0], params.get("attachment_id", [""])[0]
                )
                try:
                    name = filename(meta.get("name", "attachment"))
                except ValueError:
                    name = "attachment"
                self._response(200, raw, content_type="application/octet-stream", download=name)
            elif parsed.path == "/api/message":
                key = parse_qs(parsed.query).get("id", [""])[0]
                self._response(200, self.server.manager.detail(key))
            else:
                self._response(404, {"error": "Page not found"})
        except (ValueError, ProviderError) as e:
            self._response(400, {"error": str(e)[:240]})
        except Exception:  # noqa: BLE001 - prevent provider exception details from reaching HTTP clients
            self._response(503, {"error": "Mail is temporarily unavailable. Try again shortly"})

    def do_POST(self):
        if not self._boundary(write=True):
            return
        path = urlsplit(self.path).path
        if path != "/api/session" and not self._authorized():
            return
        if self.headers.get("Content-Type", "").split(";")[
            0
        ].strip() != "application/json" or self.headers.get("Transfer-Encoding"):
            self._response(415, {"error": "Only JSON requests are accepted"})
            return
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            length = -1
        max_body = 29 * 1024 * 1024 if path in ("/api/send", "/api/draft") else MAX_BODY
        if not 0 <= length <= max_body:
            self._response(413, {"error": "Request is too large or invalid"})
            return
        try:
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise TypeError
        except (ValueError, TypeError, UnicodeDecodeError):
            self._response(400, {"error": "Invalid request format"})
            return
        if path == "/api/session":
            token = data.get("token")
            if not isinstance(token, str) or not hmac.compare_digest(
                token.encode(errors="replace"), self.server.secret.encode()
            ):
                self._response(401, {"error": "Launch link expired. Restart the workbench"})
                return
            self._response(200, {"ok": True, "session": self.server.session})
            return
        manager = self.server.manager
        try:
            if path == "/api/shutdown":
                self._response(200, {"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            if path in ("/api/refresh", "/api/load-more"):
                manager.refresh(more=path == "/api/load-more")
                result = {"ok": True}
            elif path == "/api/folder":
                result = manager.change_folder(data.get("folder"))
            elif path == "/api/mail-action":
                result = manager.mail_action(data)
            elif path == "/api/send":
                result = manager.send_mail(data)
            elif path == "/api/draft":
                manager.accounts.get(data.get("account_id", ""))
                result = manager.mail_store.save_draft(data)
            elif path == "/api/draft-delete":
                result = manager.mail_store.delete_draft(data.get("id", ""))
            elif path == "/api/prefetch":
                result = manager.prefetch(data.get("ids"))
            elif path == "/api/mark-read":
                result = manager.mark_read(data.get("id", ""), data.get("read", True))
            elif path == "/api/action":
                result = manager.action(data)
            elif path == "/api/undo":
                result = manager.undo(data.get("token", ""))
            elif path == "/api/account":
                result = manager.connect(data)
            elif path == "/api/google-client":
                result = save_google_client(manager.paths, data.get("client"))
            elif path == "/api/account-action":
                result = manager.enable(data.get("id", ""), data.get("enabled"))
            else:
                self._response(404, {"error": "Action not found"})
                return
            self._response(200, result)
        except (ValueError, ProviderError) as e:
            self._response(409 if path == "/api/undo" else 400, {"error": str(e)[:240]})
        except Exception:  # noqa: BLE001 - do not serialize remote/authentication exceptions
            self._response(503, {"error": "Action did not complete. Refresh and try again"})


def run(ctx):
    if getattr(ctx.args, "background", False) or getattr(ctx.args, "stop", False):
        from .launcher import launch

        return launch(ctx)
    port = ctx.args.port
    if not 0 <= port <= 65535:
        raise ValueError("port must be between 0 and 65535")
    root = ctx.paths.data_dir.absolute() / "workbench"
    with InstanceLock(root):
        manager = Workbench(root, ctx.paths)
        server = LocalServer(manager, port)
        _write_private(root / "launch.json", json.dumps({"url": server.launch_url, "pid": os.getpid()}))
        ctx.out(f"Email Town local workbench: {server.origin}")
        ctx.out(f"Private launch link saved at {root / 'launch.json'}. Press Ctrl+C to stop.")
        if not ctx.args.no_browser:
            webbrowser.open(server.launch_url)
        manager.start()
        try:
            server.serve_forever(poll_interval=0.2)
        except KeyboardInterrupt:
            pass
        finally:
            manager.close()
            server.server_close()
    return 0
