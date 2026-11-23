from __future__ import annotations

import pytest

from mesa_storage.vector_engine import VectorEngine, VectorMetrics

VEC8 = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]


class _FailingMerge:
    def when_matched_update_all(self):
        return self

    def when_not_matched_insert_all(self):
        return self

    def execute(self, _records) -> None:
        raise OSError("simulated merge insert failure")


class _RecordingTable:
    def __init__(self) -> None:
        self.add_calls = 0

    def merge_insert(self, _key: str) -> _FailingMerge:
        return _FailingMerge()

    def add(self, _records) -> None:
        self.add_calls += 1


def _vector_engine_with_table(table: _RecordingTable) -> VectorEngine:
    engine = object.__new__(VectorEngine)
    engine._metrics = VectorMetrics()
    engine._sync_get_or_create_table = lambda _dimension: table
    return engine


def test_single_upsert_rejects_fallback_add_on_merge_failure():
    """Preserve upsert idempotency: a merge failure must propagate."""
    table = _RecordingTable()
    engine = _vector_engine_with_table(table)

    with pytest.raises(OSError):
        engine._sync_upsert("node-1", "agent-wave-002", VEC8, None)

    assert table.add_calls == 0
    assert engine.metrics.upserts == 0


def test_bulk_upsert_rejects_fallback_add_on_merge_failure():
    """A bulk merge failure must not create duplicates via add()."""
    table = _RecordingTable()
    engine = _vector_engine_with_table(table)

    with pytest.raises(OSError):
        engine._sync_bulk_upsert(
            [
                {
                    "node_id": "node-1",
                    "agent_id": "agent-wave-002",
                    "embedding": VEC8,
                }
            ]
        )

    assert table.add_calls == 0
    assert engine.metrics.upserts == 0
