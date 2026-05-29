"""Cross-platform advisory file locks via portalocker (path-based on Windows)."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import portalocker

DEFAULT_TIMEOUT = 30


def lock_path_for(target: Path) -> Path:
    """Sidecar lock file path for a data file."""
    return target.with_name(f"{target.name}.lock")


@contextmanager
def exclusive_lock(target: Path, *, timeout: float = DEFAULT_TIMEOUT):
    """Exclusive lock coordinating readers/writers of ``target``."""
    path = lock_path_for(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    with portalocker.Lock(str(path), mode="a+", timeout=timeout):
        yield


@contextmanager
def locked(file_obj):
    """Lock an open file by path (prefer ``exclusive_lock`` for new code)."""
    name = getattr(file_obj, "name", None)
    if not name:
        portalocker.lock(file_obj, portalocker.LOCK_EX)
        try:
            yield
        finally:
            portalocker.unlock(file_obj)
        return
    with exclusive_lock(Path(name)):
        yield
