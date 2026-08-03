"""Host-local single-writer ownership for an embedded MESA storage root."""

from __future__ import annotations

import fcntl
import os
import re
from pathlib import Path
from typing import TextIO

_LOCK_NAME = ".mesa-single-writer.lock"
_OWNER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class StorageWriterLockError(RuntimeError):
    """Raised when exclusive storage-root writer ownership cannot be proven."""


class StorageWriterLock:
    """An exclusive, host-local ownership lease held by an open file descriptor."""

    def __init__(self, handle: TextIO) -> None:
        self._handle = handle

    @classmethod
    def acquire(cls, storage_root: Path, *, owner: str) -> "StorageWriterLock":
        """Acquire ownership before any writable store under ``storage_root`` opens."""
        if not _OWNER_PATTERN.fullmatch(owner):
            raise StorageWriterLockError("storage writer owner is invalid")
        if not storage_root.is_dir():
            raise StorageWriterLockError(
                "storage root must exist before lock acquisition"
            )

        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(storage_root / _LOCK_NAME, flags, 0o600)
        except OSError as exc:
            raise StorageWriterLockError(
                "storage writer lock could not be opened"
            ) from exc

        handle = os.fdopen(descriptor, "r+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as exc:
            handle.close()
            raise StorageWriterLockError(
                "storage root already has an active writer"
            ) from exc

        try:
            handle.seek(0)
            handle.truncate()
            handle.write(f"owner={owner}\npid={os.getpid()}\n")
            handle.flush()
            os.fsync(handle.fileno())
        except OSError as exc:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()
            raise StorageWriterLockError(
                "storage writer ownership could not be recorded"
            ) from exc
        return cls(handle)

    @property
    def released(self) -> bool:
        return self._handle.closed

    def release(self) -> None:
        """Release ownership. Calling this method more than once is safe."""
        if self._handle.closed:
            return
        fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        self._handle.close()

    def __enter__(self) -> "StorageWriterLock":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()
