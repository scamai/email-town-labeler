import http.server
import json
import logging
import threading

import pytest

from email_town_labeler.models.backends import ollama_choice as oc
from email_town_labeler.models.base import ModelManifest
from email_town_labeler.taxonomy import CLASSES
from tests._model_fixtures import rec

SECRET = "zebra-quartz-7731"  # made-up marker; must never appear in logs or errors


def man(**cfg):
    return ModelManifest(name="llm", version="1", backend="ollama_choice", config={"model": "tiny:1b", **cfg})


def test_prompt_lists_all_letters():
    p = oc.build_prompt(rec(subject=SECRET))
    for L, c in zip("ABCDEFG", CLASSES):
        assert f"{L}) {c.replace('_', ' ')}:" in p
    assert SECRET in p and p.rstrip().endswith("Answer:")


def test_logprobs_path():
    resp = {
        "response": "D",
        "logprobs": [
            {
                "token": "D",
                "logprob": -0.1,
                "top_logprobs": [
                    {"token": "D", "logprob": -0.1},
                    {"token": " E", "logprob": -2.5},
                    {"token": "d", "logprob": -3.0},
                    {"token": "Hello", "logprob": -4.0},
                ],
            }
        ],
    }
    probs = oc.parse_answer(resp)
    assert abs(sum(probs) - 1) < 1e-9 and CLASSES[max(range(7), key=probs.__getitem__)] == "orders"
    assert probs[4] > probs[0] > 0  # E seen, A unseen (floored)


def test_text_fallback_and_unparseable():
    p = oc.parse_answer({"response": " (B) newsletters"}, fixed_confidence=0.7)
    assert p[1] == pytest.approx(0.7) and sum(p) == pytest.approx(1)
    p = oc.parse_answer({"response": "I think travel"})
    assert CLASSES[p.index(max(p))] == "travel"
    p = oc.parse_answer({"response": "???"})
    assert CLASSES[p.index(max(p))] == "unknown"


def test_predict_with_stub_transport_never_logs_prompt(caplog):
    sent = []

    def transport(url, payload, timeout):
        sent.append(payload)
        return {"response": "A"}

    caplog.set_level(logging.DEBUG)
    m = oc.load(man(think=False), transport=transport)
    preds = m.predict([rec(1, subject=SECRET), rec(2)])
    assert [p.cls for p in preds] == ["action_needed"] * 2 and preds[0].prob == pytest.approx(0.6)
    assert sent[0]["model"] == "tiny:1b" and sent[0]["stream"] is False and sent[0]["think"] is False
    assert sent[0]["options"]["temperature"] == 0 and SECRET in sent[0]["prompt"]
    assert SECRET not in caplog.text and "m1" in caplog.text


@pytest.fixture
def fake_ollama():
    calls = []

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append((self.path, body))
            if body["model"] == "missing":
                out, code = {"error": "model 'missing' not found"}, 404
            else:
                out, code = (
                    {
                        "response": "F",
                        "logprobs": [
                            {
                                "token": "F",
                                "logprob": -0.05,
                                "top_logprobs": [{"token": "F", "logprob": -0.05}],
                            }
                        ],
                    },
                    200,
                )
            data = json.dumps(out).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/api/generate", calls
    srv.shutdown()


def test_urllib_against_fake_server(fake_ollama):
    url, calls = fake_ollama
    (p,) = oc.load(man(url=url)).predict([rec(subject=SECRET)])
    assert p.cls == "updates" and p.prob > 0.9 and calls[0][0] == "/api/generate"
    with pytest.raises(RuntimeError, match="HTTP 404") as ei:
        oc.load(man(url=url, model="missing")).predict([rec(subject=SECRET)])
    assert SECRET not in str(ei.value)


def test_unreachable_is_clear():
    m = oc.load(man(url="http://127.0.0.1:9/api/generate", timeout=2))
    with pytest.raises(RuntimeError, match="cannot reach Ollama") as ei:
        m.predict([rec(subject=SECRET)])
    assert SECRET not in str(ei.value)


def test_requires_model_tag():
    with pytest.raises(ValueError, match="config.model"):
        oc.load(ModelManifest(name="x", version="1", backend="ollama_choice"))


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:11434/api/generate",
        "http://127.0.0.1:11434/api/generate",
        "http://127.8.9.10:1/x",
        "http://[::1]:11434/api/generate",
        "https://LOCALHOST./api/generate",
    ],
)
def test_loopback_endpoints_are_allowed(url, monkeypatch):
    monkeypatch.delenv(oc.REMOTE_ENDPOINT_ENV, raising=False)
    assert oc.load(man(url=url), transport=lambda *a: {"response": "A"}).url == url


@pytest.mark.parametrize(
    "url",
    [
        "http://attacker.example:11434/api/generate",
        "https://10.0.0.5/api/generate",
        "http://127.0.0.1.attacker.example/x",
        "http://[::ffff:8.8.8.8]/x",
        "http://0.0.0.0:11434/api/generate",
        "file:///etc/passwd",
        "ftp://localhost/x",
    ],
)
def test_package_cannot_point_ollama_off_this_machine(url, monkeypatch):
    """Blocker: model.json `url` is package-supplied; a remote host would receive the mail text."""
    monkeypatch.delenv(oc.REMOTE_ENDPOINT_ENV, raising=False)
    sent = []
    pkg = man(url=url, allow_remote_endpoint=True)  # the package asking for it is ignored
    with pytest.raises(oc.RemoteEndpointError) as ei:
        oc.load(pkg, transport=lambda *a: sent.append(a) or {"response": "A"}).predict([rec(subject=SECRET)])
    assert not sent and SECRET not in str(ei.value)
    if url.startswith("http"):
        assert "allow_remote_endpoint" in str(ei.value) and oc.REMOTE_ENDPOINT_ENV in str(ei.value)


def test_user_opt_in_allows_remote_endpoint(monkeypatch):
    url = "http://gpu-box.example:11434/api/generate"
    monkeypatch.delenv(oc.REMOTE_ENDPOINT_ENV, raising=False)
    assert oc.load(man(url=url), allow_remote_endpoint=True).url == url
    monkeypatch.setenv(oc.REMOTE_ENDPOINT_ENV, "1")
    assert oc.load(man(url=url)).url == url
    monkeypatch.setenv(oc.REMOTE_ENDPOINT_ENV, "0")
    with pytest.raises(oc.RemoteEndpointError):
        oc.load(man(url=url))


def test_opt_in_is_plumbed_from_config_through_load_model(tmp_path, monkeypatch):
    from email_town_labeler import cli
    from email_town_labeler.config import Config, Paths, config_from_dict

    monkeypatch.delenv(oc.REMOTE_ENDPOINT_ENV, raising=False)
    pkg = tmp_path / "llm"
    pkg.mkdir()
    man(url="http://gpu-box.example:11434/api/generate").save(pkg)
    paths = Paths(tmp_path / "c", tmp_path / "d")
    with pytest.raises(oc.RemoteEndpointError):
        cli.make_model(Config(model_ref=str(pkg)), paths)
    cfg = config_from_dict({"model": {"ref": str(pkg), "allow_remote_endpoint": True}})
    assert cli.make_model(cfg, paths).url.startswith("http://gpu-box.example")
    monkeypatch.setenv(oc.REMOTE_ENDPOINT_ENV, "1")
    assert Config(model_ref=str(pkg)).remote_endpoint_allowed()
    assert cli.make_model(Config(model_ref=str(pkg)), paths).url.startswith("http://gpu-box.example")
