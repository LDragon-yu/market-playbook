"""OS-owned lock: automatically released if the bot process crashes."""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Iterator


def acquire_worker_lock(path: Path) -> BinaryIO | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        if os.name == "nt":
            import msvcrt
            if path.stat().st_size == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle
    except OSError:
        handle.close()
        return None


@contextmanager
def job_lock(path: Path) -> Iterator[None]:
    """Prevent a cron run and a manual run (or two API calls) from overlapping."""
    handle = acquire_worker_lock(path)
    if handle is None:
        raise RuntimeError("另一个盘后/盘前任务正在运行，请等待其结束后再试。")
    try:
        yield
    finally:
        handle.close()
