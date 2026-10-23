"""One email-town-labeler at a time per data directory: an OS file lock on `data_dir/email-town-labeler.lock`.

`run`, `once`, `backfill` and `undo` hold it, so two processes never poll, label or undo the same
mailbox state at once. The OS drops the lock when the process exits, even on a crash or kill, so a
stale lock file is harmless. `msvcrt.locking` on Windows, `fcntl.flock` everywhere else.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import TracebackType
from typing import IO, TYPE_CHECKING

if TYPE_CHECKING:  # typing.Self is 3.11+
    from typing_extensions import Self

LOCK_NAME = "email-town-labeler.lock"


class AlreadyRunning(RuntimeError):
    """Another email-town-labeler process holds the lock for this data directory."""


def _try_lock(f: IO[bytes]) -> bool:
    if sys.platform == "win32":
        import msvcrt

        f.seek(0)
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    import fcntl

    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(f: IO[bytes]) -> None:
    if sys.platform == "win32":
        import msvcrt

        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


class InstanceLock:
    """Non-blocking exclusive lock; `with InstanceLock(data_dir):` raises AlreadyRunning if taken."""

    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / LOCK_NAME
        self._f: IO[bytes] | None = None

    def acquire(self) -> InstanceLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        f = self.path.open("a+b")
        if not _try_lock(f):
            f.close()
            raise AlreadyRunning(
                f"another email-town-labeler is already running for {self.path.parent} (lock: {self.path}). "
                "Wait for it to finish, or stop it first (`email-town-labeler service uninstall`, or Ctrl-C in its terminal)."
            )
        try:  # informational only: which process holds it
            f.seek(0)
            f.truncate()
            f.write(f"{os.getpid()}\n".encode())
            f.flush()
        except OSError:
            pass
        self._f = f
        return self

    def release(self) -> None:
        f, self._f = self._f, None
        if f is None:
            return
        try:
            _unlock(f)
        except OSError:
            pass
        f.close()

    def __enter__(self) -> Self:
        self.acquire()
        return self

    def __exit__(
        self, et: type[BaseException] | None, ev: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.release()
