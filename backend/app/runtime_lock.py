from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy.engine import Engine


@contextmanager
def runtime_lease(engine: Engine) -> Iterator[None]:
    database = engine.url.database
    if engine.dialect.name != "sqlite" or not database or database == ":memory:":
        raise RuntimeError("The local AUV runtime requires a file-backed SQLite database.")
    path = Path(database).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep the lock file: deleting it can allow two processes to lock different inodes.
    with path.with_name(path.name + ".runtime.lock").open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("Another AUV backend already owns this database. Stop it before starting another.") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
