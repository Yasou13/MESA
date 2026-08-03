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
        with pytest.raises(StorageWriterLockError, match="active writer"):
            StorageWriterLock.acquire(tmp_path, owner="worker-only-runtime")
    finally:
        first.release()


def test_storage_root_rejects_a_writer_from_a_second_process(tmp_path) -> None:
    marker = tmp_path / "child-ready"
    child_program = (
        "from pathlib import Path; import sys, time; "
        "from mesa_storage.writer_lock import StorageWriterLock; "
        "lock = StorageWriterLock.acquire(Path(sys.argv[1]), "
        "owner='worker-only-runtime'); "
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
