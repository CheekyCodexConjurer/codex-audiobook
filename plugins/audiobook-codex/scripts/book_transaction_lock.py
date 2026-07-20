from __future__ import annotations

import hashlib
import os
from pathlib import Path
import time


LOCK_RELATIVE_PATH = Path("metadata") / "work" / "book-transaction.lock"


def book_lock_path(book_root: Path) -> Path:
    return book_root.resolve() / LOCK_RELATIVE_PATH


class BookTransactionLock:
    def __init__(
        self,
        book_root: Path,
        *,
        poll_interval_seconds: float = 0.05,
        timeout_seconds: float | None = None,
    ) -> None:
        self.book_root = book_root.resolve()
        self.path = book_lock_path(self.book_root)
        self.poll_interval_seconds = poll_interval_seconds
        self.timeout_seconds = timeout_seconds
        self._acquired = False
        self._handle = None

    def _try_lock(self) -> bool:
        assert self._handle is not None
        self._handle.seek(0)
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                return False
        else:
            import fcntl

            try:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (BlockingIOError, OSError):
                return False
        return True

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a+b")
        if self.path.stat().st_size == 0:
            self._handle.write(b"\0")
            self._handle.flush()
        deadline = (
            time.monotonic() + self.timeout_seconds
            if self.timeout_seconds is not None
            else None
        )
        while True:
            if self._try_lock():
                self._acquired = True
                return
            if deadline is not None and time.monotonic() >= deadline:
                self._handle.close()
                self._handle = None
                raise RuntimeError(f"Timed out waiting for book transaction lock: {self.path}")
            time.sleep(self.poll_interval_seconds)

    def _unlock(self) -> None:
        assert self._handle is not None
        self._handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)

    def release(self) -> None:
        if not self._acquired:
            if self._handle is not None:
                self._handle.close()
                self._handle = None
            return
        try:
            self._unlock()
        finally:
            self._acquired = False
            if self._handle is not None:
                self._handle.close()
                self._handle = None

    def __enter__(self) -> "BookTransactionLock":
        self.acquire()
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.release()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_generation(paths: list[Path]) -> tuple[tuple[str, str | None], ...]:
    generations: list[tuple[str, str | None]] = []
    for path in sorted({candidate.resolve() for candidate in paths}):
        if not path.exists():
            generations.append((path.as_posix(), None))
        elif path.is_file():
            generations.append((path.as_posix(), sha256_file(path)))
        else:
            generations.append((path.as_posix(), "<non-file>"))
    return tuple(generations)
