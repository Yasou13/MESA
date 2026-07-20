from __future__ import annotations

import contextlib

import pytest

from mesa_storage.dao import MemoryDAO
from mesa_storage.vector_engine import VectorEngine, VectorMetrics

VEC8 = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]


class GraphWriteRejected(RuntimeError):
    """Deterministic stand-in for a Kuzu write failure."""


class _RecordingDB:
    def __init__(self) -> None:
        self.execute_calls = []
        self.commit_calls = 0

    async def execute(self, *params, **_kwargs):
        self.execute_calls.append(params)
        return None

    async def commit(self) -> None:
        self.commit_calls += 1


class _RecordingSql:
    def __init__(self) -> None:
        self.transaction_calls = 0
        self.db = _RecordingDB()

    @contextlib.asynccontextmanager
    async def transaction(self):
        self.transaction_calls += 1
        yield self.db


class _TrackingVector:
    def __init__(self) -> None:
        self.active_node_ids = set()
        self.soft_delete_calls = []

    async def search(self, *_args, **_kwargs):
        return []

    async def upsert(self, node_id: str, agent_id: str, **_kwargs) -> None:
        self.active_node_ids.add((node_id, agent_id))

    async def soft_delete(self, node_id: str, agent_id: str) -> None:
        self.soft_delete_calls.append((node_id, agent_id))
        self.active_node_ids.discard((node_id, agent_id))


class _FailingGraph:
    async def insert_node(
        self, *, node_id: str, name: str, agent_id: str
    ) -> None:
        raise GraphWriteRejected("simulated Kuzu write failure")


@pytest.mark.asyncio
async def test_insert_fails_closed_and_compensates_vector_when_graph_rejects():
    """A triple-write must not expose a vector when Kuzu rejects its node."""
    sql = _RecordingSql()
    vector = _TrackingVector()
    dao = MemoryDAO(
        sqlite_engine=sql,
        vector_engine=vector,
        graph_provider=_FailingGraph(),
    )

    with pytest.raises(GraphWriteRejected):
        await dao.insert_memory(
            "agent-wave-002",
            node_id="node-graph-failure",
            entity_name="Failure case",
            content="graph must not be optional",
            embedding=VEC8,
        )

    assert vector.soft_delete_calls == [
        ("node-graph-failure", "agent-wave-002")
    ]
    assert ("node-graph-failure", "agent-wave-002") not in vector.active_node_ids
    assert sql.transaction_calls == 0


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
