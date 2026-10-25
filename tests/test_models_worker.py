"""models/worker.py: the model in a child process, ended when idle. Uses the `rules` backend (no weights)."""

from __future__ import annotations

import io
import pickle

import pytest

from email_town_labeler.models import registry
from email_town_labeler.models.backends._common import MissingExtraError
from email_town_labeler.models.backends.rules import MANIFEST as RULES
from email_town_labeler.models.base import ModelManifest
from email_town_labeler.models.worker import (
    ModelWorkerError,
    WorkerModel,
    load_service_model,
    serve,
    uses_worker,
)
from tests._model_fixtures import rec


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


RECS = [
    rec(0, subject="Your order has shipped", sender="orders@shop.example"),
    rec(1, subject="Can you sign by Friday?", sender="alex@corp.example"),
    rec(2, labels=("INBOX", "CATEGORY_PROMOTIONS"), list_unsubscribe="<mailto:u@deals.example>"),
]


@pytest.fixture
def worker():
    m = WorkerModel(RULES, idle_seconds=60, clock=Clock())
    yield m
    m.release()


def test_predicts_like_the_model_in_process(worker):
    assert worker.predict([]) == [] and not worker.loaded  # nothing to classify: no process
    assert worker.predict(RECS) == registry.load_manifest(RULES).predict(RECS)
    assert worker.loaded and worker.manifest.id == "rules@1"


def test_ends_when_idle_and_restarts_on_the_next_mail(worker):
    worker.predict(RECS[:1])
    worker.clock.t = 59
    assert not worker.release_if_idle() and worker.loaded
    worker.clock.t = 60
    assert worker.release_if_idle() and not worker.loaded
    assert not worker.release_if_idle()  # already gone
    assert worker.predict(RECS) == registry.load_manifest(RULES).predict(RECS)


def test_load_errors_reach_the_caller_and_leave_no_process():
    m = WorkerModel(ModelManifest(name="x", version="1", backend="no_such_backend"))
    with pytest.raises(registry.UnknownModelError, match="no_such_backend"):
        m.predict(RECS)
    assert not m.loaded


def test_a_crashed_worker_fails_one_poll_then_restarts(worker):
    worker.predict(RECS[:1])
    worker._proc.kill()
    worker._proc.wait()
    with pytest.raises(ModelWorkerError, match="stopped"):
        worker.predict(RECS[:1])
    assert not worker.loaded
    assert len(worker.predict(RECS)) == 3


def _exchange(*msgs):
    inp, out = io.BytesIO(b"".join(pickle.dumps(m) for m in msgs)), io.BytesIO()
    assert serve(inp, out) == 0  # EOF ends it
    out.seek(0)
    replies = []
    while out.tell() < len(out.getvalue()):
        replies.append(pickle.load(out))
    return replies


def test_serve_protocol():
    early, load, pred, bad = _exchange(
        ("predict", RECS), ("load", RULES, "cpu", False), ("predict", RECS), ("nope",)
    )
    assert early[0] == "err" and "before load" in str(early[1])
    assert load == ("ok", None)
    assert pred == ("ok", registry.load_manifest(RULES).predict(RECS))
    assert bad[0] == "err" and isinstance(bad[1], ValueError)


def test_errors_survive_the_process_boundary():
    e = pickle.loads(pickle.dumps(MissingExtraError("onnxruntime", "onnx", "town-modernbert")))
    assert (e.module, e.extra, e.backend) == ("onnxruntime", "onnx", "town-modernbert")
    assert "email-town-labeler[onnx]" in str(e)

    class Unpicklable(Exception):
        def __init__(self, a, b):
            super().__init__(f"{a} {b}")

    ((_, reply),) = _exchange(("load", ModelManifest(name="x", version="1", backend="boom"), "cpu", False))
    assert isinstance(reply, registry.UnknownModelError)
    from email_town_labeler.models.worker import _portable

    p = _portable(Unpicklable(1, 2))
    assert type(p) is RuntimeError and "Unpicklable: 1 2" in str(p)


def test_which_models_get_a_worker(tmp_path):
    onnx = ModelManifest(name="big", version="1", backend="onnx_sequence_classifier")
    assert uses_worker(onnx, 10) and uses_worker(onnx, 0)
    assert not uses_worker(onnx, -1)  # keep_loaded_minutes = -1: in-process, as before
    for backend in ("rules", "ollama_choice", "some_plugin"):
        assert not uses_worker(ModelManifest(name="m", version="1", backend=backend), 10)
    m = load_service_model("rules", models_dir=tmp_path)
    assert not isinstance(m, WorkerModel) and m.manifest.id == "rules@1"


def test_engine_labels_through_a_worker_and_frees_it_when_idle(tmp_path):
    from email_town_labeler.config import Config
    from email_town_labeler.engine import Engine
    from email_town_labeler.state import Store
    from tests.fakes.fake_client import FakeGmailClient, FakeLabelMap

    client, cfg, clock = FakeGmailClient(), Config(), Clock()
    model = WorkerModel(RULES, idle_seconds=600, clock=clock)
    store = Store(tmp_path / "s.sqlite")
    eng = Engine(client, FakeLabelMap(client, cfg.label_prefix, cfg.display_names()), model, store, cfg)
    try:
        eng.sync_once()  # bootstrap
        ids = [client.add_message(cls="orders"), client.add_message(cls="travel")]
        c = eng.sync_once()
        assert c["labeled"] + c["recorded"] == 2 and all(store.decision(i) for i in ids)
        assert model.loaded
        eng._release_idle_model()
        assert model.loaded  # used just now
        clock.t = 600
        eng._release_idle_model()
        assert not model.loaded
    finally:
        model.release()
