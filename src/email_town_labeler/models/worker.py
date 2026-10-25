"""Run a model in a child process that exits when idle, so the background service gives its memory back.

A large model (town-modernbert: about 3 GB resident) would otherwise stay in memory all day although most
polls find no new mail. Dropping the onnxruntime / torch session in-process is not enough: the allocator
keeps about half of it. A child process gives all of it back when it exits.

`WorkerModel.predict` starts `python -m email_town_labeler.models.worker` on first use, sends it the
manifest, then batches of records, over the child's stdin / stdout (pickle; nothing touches disk or the
network). `release_if_idle()`, called by Engine.run_forever after each poll, ends the child once it has
classified nothing for `idle_seconds`; the next mail starts a new one. The child ignores SIGINT and exits
when its stdin closes, so it never outlives the service.

`load_service_model` picks: the heavy built-in backends in a WorkerModel, everything else (`rules`,
`ollama_choice`, third-party backends) and `[model] keep_loaded_minutes = -1` in-process, as before.
"""

from __future__ import annotations

import logging
import os
import pickle
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .base import Classifier, ModelManifest
from .registry import load_manifest, resolve_loadable

log = logging.getLogger("email_town_labeler.models.worker")

WORKER_BACKENDS = frozenset(
    {"embedding_probe", "onnx_classifier", "hf_sequence_classifier", "onnx_sequence_classifier"}
)
STOP_TIMEOUT = 10.0


class ModelWorkerError(RuntimeError):
    """The model process ended unexpectedly (crashed, killed, out of memory)."""


def uses_worker(man: ModelManifest, keep_loaded_minutes: int) -> bool:
    return keep_loaded_minutes >= 0 and man.backend in WORKER_BACKENDS


def load_service_model(
    ref: str,
    *,
    models_dir: Path,
    device: str = "cpu",
    allow_remote_endpoint: bool = False,
    keep_loaded_minutes: int = 10,
) -> Classifier:
    """The model for `run`: load_model, except that a heavy backend runs in a WorkerModel."""
    man = resolve_loadable(ref, models_dir=models_dir)
    if not uses_worker(man, keep_loaded_minutes):
        return load_manifest(man, device=device, allow_remote_endpoint=allow_remote_endpoint)
    m = WorkerModel(
        man, device=device, allow_remote_endpoint=allow_remote_endpoint, idle_seconds=keep_loaded_minutes * 60
    )
    m.load()  # fail at startup (missing extra, broken package) as an in-process load would
    return m


class WorkerModel:
    """A Classifier whose backend runs in a child process, started on demand and ended when idle."""

    def __init__(
        self,
        manifest: ModelManifest,
        *,
        device: str = "cpu",
        allow_remote_endpoint: bool = False,
        idle_seconds: float = 600.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.manifest = manifest
        self.device, self.allow_remote_endpoint = device, allow_remote_endpoint
        self.idle_seconds, self.clock = float(idle_seconds), clock
        self._proc: subprocess.Popen | None = None
        self._last_used = clock()

    @property
    def loaded(self) -> bool:
        return self._proc is not None

    def load(self) -> None:
        if self._proc is None:
            t = time.perf_counter()
            self._proc = _spawn()
            try:
                self._call(("load", self.manifest, self.device, self.allow_remote_endpoint))
            except BaseException:
                self.release()
                raise
            log.info("model %s loaded in a worker process (%.1fs)", self.manifest.id, time.perf_counter() - t)
        self._last_used = self.clock()

    def predict(self, records: list) -> list:
        records = list(records)
        if not records:
            return []
        self.load()
        try:
            return self._call(("predict", records))
        finally:
            self._last_used = self.clock()

    def release_if_idle(self) -> bool:
        """End the worker if it has classified nothing for idle_seconds. True if it did."""
        if self._proc is None or self.clock() - self._last_used < self.idle_seconds:
            return False
        self.release()
        log.info("model unloaded after %.0f min without mail", self.idle_seconds / 60)
        return True

    def release(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.stdin.close()  # the worker exits on EOF
        except OSError:
            pass
        try:
            proc.wait(timeout=STOP_TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        proc.stdout.close()

    close = release

    def _call(self, msg: tuple) -> Any:
        proc = self._proc
        try:
            pickle.dump(msg, proc.stdin, protocol=pickle.HIGHEST_PROTOCOL)
            proc.stdin.flush()
            status, value = pickle.load(proc.stdout)
        except (EOFError, OSError, pickle.UnpicklingError) as e:
            code = proc.poll()
            self.release()
            raise ModelWorkerError(f"the model process stopped (exit code {code})") from e
        if status == "err":
            raise value
        return value


def _spawn() -> subprocess.Popen:
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    return subprocess.Popen(
        [sys.executable, "-m", "email_town_labeler.models.worker"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        creationflags=flags,
    )


# ------------------------------------------------------------------------------------- the child side


def _portable(e: BaseException) -> BaseException:
    """e if it survives pickling (the parent re-raises it as is), else a RuntimeError with its text."""
    try:
        pickle.loads(pickle.dumps(e))
        return e
    except Exception:  # noqa: BLE001 - any exception type may refuse to round-trip
        return RuntimeError(f"{type(e).__name__}: {e}")


def serve(inp: Any, out: Any) -> int:
    """Answer ("load", manifest, device, allow_remote_endpoint) and ("predict", records) until EOF."""
    model = None
    while True:
        try:
            msg = pickle.load(inp)
        except EOFError:
            return 0
        try:
            if msg[0] == "load":
                _, man, device, allow = msg
                model = load_manifest(man, device=device, allow_remote_endpoint=allow)
                reply: tuple = ("ok", None)
            elif msg[0] == "predict":
                if model is None:
                    raise RuntimeError("predict before load")
                reply = ("ok", model.predict(msg[1]))
            else:
                raise ValueError(f"unknown request {msg[0]!r}")
        except Exception as e:  # noqa: BLE001 - every failure goes back to the parent
            reply = ("err", _portable(e))
        pickle.dump(reply, out, protocol=pickle.HIGHEST_PROTOCOL)
        out.flush()


def main() -> int:
    if hasattr(signal, "SIGINT"):
        signal.signal(signal.SIGINT, signal.SIG_IGN)  # Ctrl-C reaches the service, which ends us
    in_fd, out_fd = os.dup(0), os.dup(1)
    if sys.platform == "win32":
        import msvcrt

        for fd in (in_fd, out_fd):
            msvcrt.setmode(fd, os.O_BINARY)  # pickle bytes, no newline translation
    inp, out = os.fdopen(in_fd, "rb"), os.fdopen(out_fd, "wb")
    try:
        os.dup2(2, 1)  # the protocol owns the pipe: anything a library prints goes to stderr
    except OSError:  # no stderr (pythonw)
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, 1)
        os.close(devnull)
    return serve(inp, out)


if __name__ == "__main__":
    sys.exit(main())
