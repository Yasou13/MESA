"""WAVE-004 protects repository trace output during isolated worker tests."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path


def test_trace_override_is_lab_contained_and_preserves_protected_file(
    monkeypatch, tmp_path
):
    import mesa_workers.ingestion_worker as worker

    protected = tmp_path / "cold_path_trace.txt"
    protected.write_text("protected fixture content\n", encoding="utf-8")
    before = sha256(protected.read_bytes()).hexdigest()
    lab_root = tmp_path / "mesa-lab"
    safe_path = lab_root / "trace" / "WAVE-004" / "test-trace.txt"
    monkeypatch.setattr(worker, "_TRACE_ROOT", lab_root.resolve())
    monkeypatch.setenv("MESA_COLD_PATH_TRACE_PATH", str(safe_path))
    worker._write_cold_path_trace("isolated test trace")
    assert "isolated test trace" in safe_path.read_text()
    assert sha256(protected.read_bytes()).hexdigest() == before


def test_trace_override_rejects_path_outside_lab(monkeypatch, tmp_path):
    import mesa_workers.ingestion_worker as worker

    lab_root = tmp_path / "mesa-lab"
    rejected = Path("/tmp") / f"{tmp_path.name}-outside-trace.txt"
    assert not rejected.exists()
    monkeypatch.setattr(worker, "_TRACE_ROOT", lab_root.resolve())
    monkeypatch.setenv("MESA_COLD_PATH_TRACE_PATH", str(rejected))
    worker._write_cold_path_trace("must not be written")
    assert not rejected.exists()


def test_missing_trace_override_never_writes_repository_file(monkeypatch, tmp_path):
    import mesa_workers.ingestion_worker as worker

    protected = tmp_path / "cold_path_trace.txt"
    protected.write_text("protected fixture content\n", encoding="utf-8")
    before = sha256(protected.read_bytes()).hexdigest()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(worker, "_TRACE_ROOT", (tmp_path / "mesa-lab").resolve())
    monkeypatch.delenv("MESA_COLD_PATH_TRACE_PATH", raising=False)
    worker._write_cold_path_trace("must stay disabled")
    assert sha256(protected.read_bytes()).hexdigest() == before
