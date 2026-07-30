from __future__ import annotations

import fcntl

import pytest
from mesa_memory.config import RuntimeProfileError
from mesa_memory.worker_runtime import _acquire_writer_lock


def test_worker_runtime_rejects_a_second_writer_for_one_storage_root(tmp_path) -> None:
    first = _acquire_writer_lock(tmp_path)
    try:
        with pytest.raises(RuntimeProfileError, match="single-writer"):
            _acquire_writer_lock(tmp_path)
    finally:
        fcntl.flock(first.fileno(), fcntl.LOCK_UN)
        first.close()
