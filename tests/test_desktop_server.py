"""Real loopback HTTP tests with synthetic data; no mailbox credentials or mail."""

import http.client
import json
import threading
from types import SimpleNamespace

import pytest

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
    for asset in ["/", "/app.js", "/style.css", "/logo.svg"]:
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
