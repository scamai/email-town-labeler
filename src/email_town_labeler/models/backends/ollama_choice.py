"""A local LLM served by Ollama, asked a 7-way lettered single-choice question (stdlib urllib only).

model.json `config`:
    url: "http://localhost:11434/api/generate"   must be loopback (localhost, 127.0.0.0/8, ::1) unless the
                                                  USER opts in (see `check_endpoint`); a package cannot
    model: "qwen3:4b"          Ollama model tag
    timeout: 120, max_body_chars: 2000, fixed_confidence: 0.6, top_logprobs: 10
    think: null | bool         sent only when set (thinking models)
    options: {...}             merged over {"temperature": 0, "num_predict": 4}
Probabilities come from the answer token's top logprobs when Ollama returns them; otherwise the
parsed letter gets `fixed_confidence` and the rest share the remainder.
The prompt and the reply text are never logged or put into exception messages.

The url comes from the model package, and the prompt contains mail text, so a package must not be
able to point it at someone else's server. Non-loopback hosts are refused unless the user sets
`[model] allow_remote_endpoint = true` or EMAIL_TOWN_LABELER_ALLOW_REMOTE_ENDPOINT=1.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import math
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

from ...features import email_text
from ...taxonomy import CLASSES, DESCRIPTIONS, UNKNOWN
from ...types import EmailRecord, Prediction
from ..base import ModelManifest
from ._common import to_predictions

log = logging.getLogger(__name__)
LETTERS = "ABCDEFG"[: len(CLASSES)]
LETTER_CLASS = dict(zip(LETTERS, CLASSES))
DEFAULT_URL = "http://localhost:11434/api/generate"
REMOTE_ENDPOINT_ENV = (
    "EMAIL_TOWN_LABELER_ALLOW_REMOTE_ENDPOINT"  # mirrors email_town_labeler.config.REMOTE_ENDPOINT_ENV
)
Transport = Callable[[str, dict, float], dict]  # (url, payload, timeout) -> response json


class RemoteEndpointError(ValueError):
    """The model package points the backend at a host other than this machine, and the user has not opted in."""


def is_loopback_host(host: str | None) -> bool:
    h = (host or "").strip().rstrip(".").lower()
    if h == "localhost":
        return True
    try:
        return ipaddress.ip_address(h.strip("[]")).is_loopback  # 127.0.0.0/8, ::1
    except ValueError:
        return False  # any other hostname: may resolve anywhere


def check_endpoint(url: str, *, allow_remote: bool = False) -> str:
    """Return `url` if it is http(s) on a loopback host, or the user allowed remote endpoints; else raise."""
    u = urllib.parse.urlparse(str(url))
    if u.scheme not in ("http", "https") or not u.hostname:
        raise RemoteEndpointError(
            f"ollama_choice config.url must be an http:// or https:// URL (got scheme {u.scheme or 'none'!r})"
        )
    if allow_remote or os.environ.get(REMOTE_ENDPOINT_ENV, "").strip() == "1" or is_loopback_host(u.hostname):
        return str(url)
    raise RemoteEndpointError(
        f"refusing to send mail text to {u.hostname!r}: the model package's config.url is not on this machine "
        "(only localhost, 127.0.0.0/8 and ::1 are allowed by default). If you run Ollama on a host you "
        "control, opt in yourself: set `allow_remote_endpoint = true` under [model] in config.toml, or "
        f"{REMOTE_ENDPOINT_ENV}=1"
    )


def build_prompt(rec: EmailRecord, max_body_chars: int = 2000) -> str:
    opts = "\n".join(f"{L}) {c.replace('_', ' ')}: {DESCRIPTIONS[c]}" for L, c in LETTER_CLASS.items())
    return (
        "Classify the email below into exactly one category.\n\n"
        + opts
        + "\n\n<email>\n"
        + email_text(rec, max_body_chars=max_body_chars)
        + "\n</email>\n\n"
        f"Reply with a single letter ({', '.join(LETTERS)}) and nothing else.\nAnswer:"
    )


def http_post(url: str, payload: dict, timeout: float) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST", headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            err = str(json.loads(e.read().decode("utf-8", "replace")).get("error", ""))[:120]
        except Exception:  # noqa: BLE001
            err = ""
        raise RuntimeError(f"Ollama returned HTTP {e.code} {err}".strip()) from None
    except (urllib.error.URLError, OSError) as e:
        reason = getattr(e, "reason", e)
        raise RuntimeError(f"cannot reach Ollama at {url}: {reason} (is `ollama serve` running?)") from None


def _letter(token: str) -> str | None:
    t = token.strip().strip("()[]*.:").upper()
    return t if len(t) == 1 and t in LETTER_CLASS else None


def parse_answer(resp: dict, fixed_confidence: float = 0.6) -> list[float]:
    """Ollama /api/generate response -> probabilities over CLASSES."""
    n = len(CLASSES)
    for entry in resp.get("logprobs") or []:
        tops = entry.get("top_logprobs") or [entry]
        best: dict[str, float] = {}
        for t in tops:
            L = _letter(str(t.get("token", "")))
            if L is not None and "logprob" in t:
                best[L] = max(best.get(L, -math.inf), float(t["logprob"]))
        if best:
            floor = min(best.values()) - 5.0  # unseen letters: well below the least likely seen one
            z = [best.get(L, floor) for L in LETTERS]
            m = max(z)
            e = [math.exp(v - m) for v in z]
            return [v / sum(e) for v in e]
        if str(entry.get("token", "")).strip():
            break  # first real token carried no letter: fall back to text parsing
    text = str(resp.get("response") or "")
    m = re.search(r"\b([A-G])\b", text.strip()[:20].upper())
    L = m.group(1) if m else None
    if L is None:
        lowered = text.lower()
        L = next(
            (Lt for Lt, c in LETTER_CLASS.items() if c.replace("_", " ") in lowered or c in lowered), None
        )
    if L is None:  # unparseable: lean to unknown
        p = [0.5 / (n - 1)] * n
        p[CLASSES.index(UNKNOWN)] = 0.5
        return p
    c = min(max(fixed_confidence, 1.0 / n), 1.0)
    return [c if Lt == L else (1 - c) / (n - 1) for Lt in LETTERS]


class OllamaChoice:
    def __init__(
        self,
        manifest: ModelManifest,
        *,
        transport: Transport | None = None,
        allow_remote_endpoint: bool = False,
    ):
        self.manifest = manifest
        cfg = manifest.config
        if not cfg.get("model"):
            raise ValueError("ollama_choice config.model (an Ollama model tag) is required")
        self.url = check_endpoint(cfg.get("url", DEFAULT_URL), allow_remote=allow_remote_endpoint)
        self.timeout = float(cfg.get("timeout", 120))
        self.transport = transport or http_post

    def _payload(self, rec: EmailRecord) -> dict:
        cfg = self.manifest.config
        p = {
            "model": cfg["model"],
            "prompt": build_prompt(rec, int(cfg.get("max_body_chars", 2000))),
            "stream": False,
            "options": {"temperature": 0, "num_predict": 4, **(cfg.get("options") or {})},
            "logprobs": True,
            "top_logprobs": int(cfg.get("top_logprobs", 10)),
        }
        if cfg.get("think") is not None:
            p["think"] = bool(cfg["think"])
        return p

    def predict(self, records: list[EmailRecord]) -> list[Prediction]:
        fc = float(self.manifest.config.get("fixed_confidence", 0.6))
        rows = []
        for rec in records:
            rows.append(parse_answer(self.transport(self.url, self._payload(rec), self.timeout), fc))
            log.debug("ollama_choice classified id=%s", rec.get("id"))
        return to_predictions(rows, CLASSES) if rows else []


def load(
    manifest: ModelManifest,
    *,
    device: str = "cpu",
    transport: Transport | None = None,
    allow_remote_endpoint: bool = False,
) -> OllamaChoice:
    """`allow_remote_endpoint` is the user's opt-in (config/env), passed by the registry; never read from the package."""
    return OllamaChoice(manifest, transport=transport, allow_remote_endpoint=allow_remote_endpoint)
