"""Host-local single-writer ownership for an embedded MESA storage root."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import TextIO

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

_LOCK_NAME = ".mesa-single-writer.lock"
_OWNER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _lock_nonblocking(fd: int) -> None:
    if sys.platform == "win32":
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(fd: int) -> None:
    if sys.platform == "win32":
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)


class StorageWriterLockError(RuntimeError):
    """Raised when exclusive storage-root writer ownership cannot be proven."""


class StorageWriterLock:
    """An exclusive, host-local ownership lease held by an open file descriptor."""

    def __init__(
        self, handle: TextIO, storage_root: Path, *, _acquired: bool = False
    ) -> None:
        self._handle = handle
        self._storage_root = storage_root
        self._acquired = _acquired

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
            _lock_nonblocking(handle.fileno())
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
            try:
                _unlock(handle.fileno())
            except OSError:
                pass
            handle.close()
            raise StorageWriterLockError(
                "storage writer ownership could not be recorded"
            ) from exc
        return cls(handle, storage_root.resolve(strict=True), _acquired=True)

    @property
    def storage_root(self) -> Path:
        return self._storage_root

    @property
    def released(self) -> bool:
        return self._handle.closed

    def is_held_for(self, storage_root: Path) -> bool:
        """Return whether this handle owns the real lock file for ``storage_root``.

        Root metadata alone is not a capability: callers can construct an object
        around any open file.  Verify the descriptor identity and OS lock
        ownership so consumers can rely on implementation-owned proof.
        """
        if self._handle.closed:
            return False
        try:
            resolved_root = storage_root.resolve(strict=True)
            expected = os.stat(resolved_root / _LOCK_NAME, follow_symlinks=False)
            actual = os.fstat(self._handle.fileno())
        except (OSError, ValueError):
            return False
        if self._storage_root != resolved_root or (
            actual.st_dev,
            actual.st_ino,
        ) != (expected.st_dev, expected.st_ino):
            return False
        if sys.platform == "win32":
            # Win32 byte-range locks reject re-locking even on the owning handle.
            return self._acquired
        try:
            _lock_nonblocking(self._handle.fileno())
        except (BlockingIOError, OSError):
            return False
        return True

    def release(self) -> None:
        """Release ownership. Calling this method more than once is safe."""
        if self._handle.closed:
            return
        try:
            if sys.platform == "win32":
                if self._acquired:
                    self._handle.flush()
                    _unlock(self._handle.fileno())
            else:
                _unlock(self._handle.fileno())
        finally:
            self._acquired = False
            self._handle.close()

    def __enter__(self) -> "StorageWriterLock":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()
