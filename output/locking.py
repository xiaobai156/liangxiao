from __future__ import annotations

import hashlib
import os
import tempfile
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path
from threading import Lock, RLock


_THREAD_LOCKS: dict[str, RLock] = {}
_REGISTRY_LOCK = Lock()
# Output locks must never live next to the formal success/failure files.  The
# lock directory is derived from the output directory path, so two processes
# writing the same output directory still share one lock.
_LOCK_ROOT = Path(tempfile.gettempdir()) / "liangxiao-v2-output-locks"


def _lock_path_for(directory: Path) -> Path:
    key = hashlib.sha256(
        os.path.normcase(str(directory.resolve())).encode("utf-8")
    ).hexdigest()[:24]
    return _LOCK_ROOT / f"{key}.lock"


@contextmanager
def _directory_lock(directory: Path, timeout: float):
    directory.mkdir(parents=True, exist_ok=True)
    _LOCK_ROOT.mkdir(parents=True, exist_ok=True)
    lock_path = _lock_path_for(directory)
    key = os.path.normcase(str(lock_path.resolve()))
    with _REGISTRY_LOCK:
        thread_lock = _THREAD_LOCKS.setdefault(key, RLock())
    deadline = time.monotonic() + timeout
    if not thread_lock.acquire(timeout=max(0, timeout)):
        raise TimeoutError("输出正在被其他任务写入")
    try:
        with lock_path.open("a+b") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            if os.name == "nt":
                import msvcrt

                def acquire():
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

                def release():
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                def acquire():
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

                def release():
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            while True:
                try:
                    acquire()
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("输出正在被其他进程写入") from exc
                    time.sleep(min(0.05, max(0, deadline - time.monotonic())))
            try:
                yield
            finally:
                release()
    finally:
        thread_lock.release()


@contextmanager
def output_lock(*paths: Path, timeout: float = 10.0):
    """Lock every output directory in a stable order, including rollback."""
    directories = {os.path.normcase(str(path.parent.resolve())) for path in paths}
    deadline = time.monotonic() + timeout
    with ExitStack() as stack:
        for directory in sorted(directories):
            stack.enter_context(_directory_lock(Path(directory), max(0, deadline - time.monotonic())))
        yield
