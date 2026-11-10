"""Real loopback HTTP tests with synthetic data; no mailbox credentials or mail."""

import http.client
import json
import logging
import re
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from email_town_labeler.desktop import server as server_module
from email_town_labeler.desktop.server import MAX_BODY, LocalServer


@pytest.fixture
def server():
    manager = SimpleNamespace(snapshot=lambda: {"messages": [{"subject": "fixture only"}]})
    server = LocalServer(manager)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(2)


def request(server, path="/api/state", *, method="GET", data=None, auth=False, headers=None):
    h = {}
    if method == "POST":
        h.update({"Origin": server.origin, "X-Email-Town": "local", "Content-Type": "application/json"})
    if auth:
        h["Authorization"] = "Bearer " + server.session
    h.update(headers or {})
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    try:
        conn.request(method, path, body=json.dumps(data) if data is not None else None, headers=h)
        response = conn.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        conn.close()


def test_bootstrap_is_bearer_only_no_port_leaking_cookie(server):
    status, headers, body = request(server, "/api/session", method="POST", data={"token": server.secret})
    assert status == 200
    assert json.loads(body)["session"] == server.session
    assert "Set-Cookie" not in headers
    assert headers["Cache-Control"] == "no-store"
    assert request(server)[0] == 401
    assert request(server, headers={"Cookie": "email_town_session=" + server.session})[0] == 401
    assert request(server, auth=True)[0] == 200
    assert server.session.encode() not in request(server, auth=True)[2]
    for token in [None, "wrong", "中文", "\ud800"]:
        assert request(server, "/api/session", method="POST", data={"token": token})[0] == 401


def test_host_origin_and_custom_header_boundaries(server):
    assert request(server, auth=True, headers={"Host": "attacker.invalid"})[0] == 403
    assert request(server, auth=True, headers={"Origin": "http://127.0.0.1:1"})[0] == 403
    assert (
        request(
            server, "/api/session", method="POST", data={"token": server.secret}, headers={"X-Email-Town": ""}
        )[0]
        == 403
    )
    assert (
        request(
            server, "/api/session", method="POST", data={"token": server.secret}, headers={"Origin": "null"}
        )[0]
        == 403
    )


def test_assets_and_request_limits(server):
    for asset in ["/", "/app.js", "/style.css", "/reader.css", "/reader.js", "/logo.svg"]:
        status, headers, body = request(server, asset)
        assert status == 200 and body
        assert server.secret.encode() not in body and server.session.encode() not in body
        assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert request(server, "/../accounts.json", auth=True)[0] == 404
    assert request(server, "/api/session", method="POST", data=[])[0] == 400
    assert (
        request(server, "/api/session", method="POST", data={}, headers={"Content-Type": "text/plain"})[0]
        == 415
    )
    assert request(server, "/api/session", method="POST", data={"token": "x" * MAX_BODY})[0] == 413


def test_loopback_http_serves_without_reverse_dns(monkeypatch):
    def unavailable_dns(_host):
        raise TimeoutError("system hostname resolver unavailable")

    monkeypatch.setattr("socket.getfqdn", unavailable_dns)
    manager = SimpleNamespace(snapshot=lambda: {"messages": []})
    local = LocalServer(manager)
    thread = threading.Thread(target=local.serve_forever, daemon=True)
    thread.start()
    try:
        # Observe authenticated HTTP across the listener boundary with DNS unavailable.
        status, _, body = request(local, auth=True)
        assert status == 200
        assert json.loads(body) == {"messages": []}
    finally:
        local.shutdown()
        local.server_close()
        thread.join(2)


def test_state_wait_param_long_polls_only_with_a_wellformed_etag(server):
    calls = []
    server.manager.wait_snapshot = lambda etag: calls.append(etag) or {"unchanged": True, "etag": etag}
    etag = "ab" * 16
    status, _, body = request(server, "/api/state?wait=" + etag, auth=True)
    assert status == 200 and json.loads(body) == {"unchanged": True, "etag": etag} and calls == [etag]
    for odd in ("", "zz", "AB" * 16, etag + "0"):
        status, _, body = request(server, "/api/state?wait=" + odd, auth=True)
        assert status == 200 and json.loads(body) == {"messages": [{"subject": "fixture only"}]}
    assert calls == [etag]
    assert request(server, "/api/state?wait=" + etag)[0] == 401  # the long-poll is behind the same session


def test_mark_read_route_passes_id_and_flag(server):
    calls = []
    server.manager.mark_read = lambda key, read: calls.append((key, read)) or {"ok": True, "changed": True}
    status, _, body = request(server, "/api/mark-read", method="POST", data={"id": "k1"}, auth=True)
    assert status == 200 and json.loads(body) == {"ok": True, "changed": True}
    request(server, "/api/mark-read", method="POST", data={"id": "k2", "read": False}, auth=True)
    assert calls == [("k1", True), ("k2", False)]


def test_page_markup_has_no_inline_styles():
    """The CSP is style-src 'self': a style attribute or <style> in the page would be refused silently."""
    web = Path(server_module.__file__).parent / "web"
    page = (web / "index.html").read_text(encoding="utf-8")
    assert not re.search(r"<style\b|\sstyle\s*=", page), "inline style in index.html"
    for script in web.glob("*.js"):
        text = script.read_text(encoding="utf-8")
        assert 'setAttribute("style"' not in text, f"inline style set in {script.name}"


def test_api_requests_are_timed_in_the_log_without_ids_or_queries(server, caplog):
    """desktop.log must say how long each action took, so a slow click can be diagnosed afterwards."""
    server.manager.mark_read = lambda key, read: {"ok": True, "changed": True}
    with caplog.at_level(logging.INFO, logger="email_town_labeler.desktop.http"):
        request(server, "/api/mark-read", method="POST", data={"id": "secret-message-id"}, auth=True)
        request(server, "/api/state?wait=1", auth=True)  # the long poll is never logged
        deadline = time.monotonic() + 2
        while not caplog.records and time.monotonic() < deadline:
            time.sleep(0.01)  # the line is written after the response is flushed
    lines = [r.getMessage() for r in caplog.records]
    assert len(lines) == 1 and re.fullmatch(r"POST /api/mark-read 200 \d+ ms", lines[0]), lines
    assert "secret-message-id" not in lines[0]
