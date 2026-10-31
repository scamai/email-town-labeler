"""Launch a real background workbench against an empty temporary home, then stop it."""

import json
import subprocess
import sys
import time

from email_town_labeler.config import Paths
from email_town_labeler.desktop.launcher import running


def test_background_reopen_and_authenticated_stop(tmp_path):
    args = [sys.executable, "-m", "email_town_labeler", "--home", str(tmp_path), "desktop"]
    root = Paths.default(tmp_path).data_dir / "workbench"
    try:
        subprocess.run([*args, "--background", "--no-browser"], check=True, capture_output=True, timeout=20)
        assert running(root)
        first = json.loads((root / "launch.json").read_text())["pid"]
        subprocess.run([*args, "--background", "--no-browser"], check=True, capture_output=True, timeout=10)
        assert json.loads((root / "launch.json").read_text())["pid"] == first
        subprocess.run([*args, "--stop"], check=True, capture_output=True, timeout=10)
        deadline = time.monotonic() + 5
        while running(root) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert running(root) is None
    finally:
        subprocess.run([*args, "--stop"], check=True, capture_output=True, timeout=10)


def test_saved_launch_url_cannot_redirect_to_external_service(tmp_path):
    for url in [
        "https://evil.test/#token=x",
        "http://localhost:1234/#token=x",
        "http://127.0.0.1:bad/#token=x",
    ]:
        (tmp_path / "launch.json").write_text(json.dumps({"url": url}))
        assert running(tmp_path) is None


def test_stale_port_impersonator_never_receives_launch_secret(tmp_path):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    requests = []

    class Impersonator(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            requests.append(self.path)
            body = b'{"session":"attacker","proof":"wrong"}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            requests.append(self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode())
            self.do_GET()

    fake = ThreadingHTTPServer(("127.0.0.1", 0), Impersonator)
    thread = threading.Thread(target=fake.serve_forever, daemon=True)
    thread.start()
    try:
        token = "x" * 43
        (tmp_path / "launch.json").write_text(
            json.dumps({"url": f"http://127.0.0.1:{fake.server_port}/#token={token}"})
        )
        assert running(tmp_path) is None
        assert len(requests) == 1 and requests[0].startswith("/api/proof?challenge=")
        assert token not in requests[0]
    finally:
        fake.shutdown()
        fake.server_close()
        thread.join(2)
