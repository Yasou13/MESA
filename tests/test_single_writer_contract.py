from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from mesa_memory.api.server import _acquire_runtime_writer_lock
from mesa_memory.config import RuntimeProfile, RuntimeProfileConfig
from mesa_storage.writer_lock import StorageWriterLock, StorageWriterLockError

ROOT = Path(__file__).parents[1]


def test_storage_root_rejects_a_second_writer(tmp_path) -> None:
    first = StorageWriterLock.acquire(tmp_path, owner="combined-runtime")
    try:
        assert first.is_held_for(tmp_path)
        with pytest.raises(StorageWriterLockError, match="active writer"):
            StorageWriterLock.acquire(tmp_path, owner="combined-runtime")
    finally:
        first.release()

    assert first.released
    assert not first.is_held_for(tmp_path)
    # Releasing allows a subsequent writer to acquire the same root.
    with StorageWriterLock.acquire(tmp_path, owner="combined-runtime") as second:
        assert second.is_held_for(tmp_path)


def test_forged_writer_lock_is_rejected(tmp_path) -> None:
    lock_file = tmp_path / ".mesa-single-writer.lock"
    lock_file.write_text("owner=other\npid=1\n", encoding="utf-8")
    with StorageWriterLock.acquire(tmp_path, owner="real-writer") as real_lock:
        assert real_lock.is_held_for(tmp_path)
        with lock_file.open("r+", encoding="utf-8") as forged_handle:
            forged = StorageWriterLock(forged_handle, tmp_path.resolve())
            assert not forged.is_held_for(tmp_path)
            forged.release()
        assert real_lock.is_held_for(tmp_path)


def test_windows_msvcrt_locking_branch_enforces_exclusive_writer(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Exercise the Windows msvcrt.locking code path on every platform."""
    import mesa_storage.writer_lock as lock_module

    locked_fds: set[int] = set()
    ops: list[tuple[str, int]] = []

    class FakeMsvcrt:
        LK_NBLCK = 2
        LK_UNLCK = 0

        @staticmethod
        def locking(fd: int, mode: int, nbytes: int) -> None:
            assert nbytes == 1
            if mode == FakeMsvcrt.LK_NBLCK:
                if locked_fds:
                    raise PermissionError("already locked")
                locked_fds.add(fd)
                ops.append(("lock", fd))
            elif mode == FakeMsvcrt.LK_UNLCK:
                if fd not in locked_fds:
                    raise PermissionError("not locked")
                locked_fds.remove(fd)
                ops.append(("unlock", fd))
            else:
                raise AssertionError(f"unexpected mode: {mode}")

    monkeypatch.setattr(lock_module.sys, "platform", "win32")
    monkeypatch.setattr(lock_module, "msvcrt", FakeMsvcrt, raising=False)

    first = StorageWriterLock.acquire(tmp_path, owner="win-writer")
    try:
        assert first.is_held_for(tmp_path)
        with pytest.raises(StorageWriterLockError, match="active writer"):
            StorageWriterLock.acquire(tmp_path, owner="win-writer")

        # Forged object without acquire() must not report held or unlock on release.
        with (tmp_path / ".mesa-single-writer.lock").open("r+", encoding="utf-8") as fh:
            forged = StorageWriterLock(fh, tmp_path.resolve())
            assert not forged.is_held_for(tmp_path)
            forged.release()
        assert len(locked_fds) == 1
    finally:
        first.release()

    assert not locked_fds
    assert [op[0] for op in ops] == ["lock", "unlock"]
    # Second release is a safe no-op.
    first.release()


def test_storage_root_rejects_a_writer_from_a_second_process(tmp_path) -> None:
    marker = tmp_path / "child-ready"
    child_program = (
        "from pathlib import Path; import sys, time; "
        "from mesa_storage.writer_lock import StorageWriterLock; "
        "lock = StorageWriterLock.acquire(Path(sys.argv[1]), "
        "owner='combined-runtime'); "
        "Path(sys.argv[2]).touch(); time.sleep(30)"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", child_program, str(tmp_path), str(marker)],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + 20
        while not marker.exists() and process.poll() is None:
            if time.monotonic() >= deadline:
                break
            time.sleep(0.05)
        assert marker.exists(), f"child writer did not become ready: {process.poll()}"
        with pytest.raises(StorageWriterLockError, match="active writer"):
            StorageWriterLock.acquire(tmp_path, owner="combined-runtime")
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=10)


def test_combined_runtime_uses_the_shared_storage_writer_lock(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    runtime = RuntimeProfileConfig(
        profile=RuntimeProfile.COMBINED,
        storage_root=tmp_path,
        load_dotenv=False,
        dotenv_path=None,
        model_enabled=False,
        external_provider_enabled=False,
        api_enabled=True,
        worker_enabled=True,
        require_worker_readiness=False,
    )
    acquired: list[tuple[object, str]] = []
    expected = object()

    def acquire(storage_root, *, owner):  # type: ignore[no-untyped-def]
        acquired.append((storage_root, owner))
        return expected

    monkeypatch.setattr(StorageWriterLock, "acquire", acquire)

    assert _acquire_runtime_writer_lock(runtime) is expected
    assert acquired == [(tmp_path, "combined-runtime")]
