"""WAVE-004 protects repository trace output during isolated worker tests."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path


def test_trace_override_is_lab_contained_and_preserves_protected_file(monkeypatch, tmp_path):
    from mesa_workers.ingestion_worker import _write_cold_path_trace

    protected = Path("cold_path_trace.txt")
    before = sha256(protected.read_bytes()).hexdigest()
    safe_path = Path("/storage/mesa-lab/trace/WAVE-004/test-trace.txt")
    monkeypatch.setenv("MESA_COLD_PATH_TRACE_PATH", str(safe_path))
    _write_cold_path_trace("isolated test trace")
    assert "isolated test trace" in safe_path.read_text()
    assert sha256(protected.read_bytes()).hexdigest() == before


def test_trace_override_rejects_path_outside_lab(monkeypatch, tmp_path):
    from mesa_workers.ingestion_worker import _write_cold_path_trace

    rejected = Path("/tmp") / f"{tmp_path.name}-outside-trace.txt"
    assert not rejected.exists()
    monkeypatch.setenv("MESA_COLD_PATH_TRACE_PATH", str(rejected))
    _write_cold_path_trace("must not be written")
    assert not rejected.exists()


def test_missing_trace_override_never_writes_repository_file(monkeypatch):
    from mesa_workers.ingestion_worker import _write_cold_path_trace

    protected = Path("cold_path_trace.txt")
    before = sha256(protected.read_bytes()).hexdigest()
    monkeypatch.delenv("MESA_COLD_PATH_TRACE_PATH", raising=False)
    _write_cold_path_trace("must stay disabled")
    assert sha256(protected.read_bytes()).hexdigest() == before
