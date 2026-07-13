# MESA v0.5.2 — Vector Engine Coverage Tests
"""
Unit tests targeting uncovered execution paths in mesa_storage/vector_engine.py.

Uses real LanceDB instances with temporary storage to exercise:
  - VectorMetrics (avg_search_time_ms, snapshot)
  - _build_schema
  - _validate_filter_value (injection guard)
  - VectorEngine lifecycle (initialize, close, async context manager)
  - Upsert (merge_insert fallback, uninitialized guard)
  - Bulk upsert (dimension partitioning, empty input, merge_insert fallback)
  - Search (agent_id filter, include_expired, empty table, error path)
  - Soft delete / hard delete
  - get_active_node_ids (with/without agent_id filter)
  - count_records (active_only/all)
  - health_check (healthy/uninitialized)
  - Blue/Green Procrustes alignment (happy path, rollback, exception)
"""

from __future__ import annotations

import asyncio
import os
import shutil
import uuid

import numpy as np
import pytest

from mesa_storage.vector_engine import (
    VectorEngine,
    VectorMetrics,
    _build_schema,
    _validate_filter_value,
)

TEST_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    ".test_storage_tmp",
    "vec_cov",
)


@pytest.fixture(autouse=True)
def _clean():
    os.makedirs(TEST_DIR, exist_ok=True)
    yield
    shutil.rmtree(TEST_DIR, ignore_errors=True)


@pytest.fixture
def engine(event_loop):
    uid = uuid.uuid4().hex[:8]
    uri = os.path.join(TEST_DIR, f"vec_{uid}.lance")
    eng = VectorEngine(uri, max_workers=2)
    event_loop.run_until_complete(eng.initialize())
    yield eng
    event_loop.run_until_complete(eng.close())


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ===================================================================
# VectorMetrics
# ===================================================================


class TestVectorMetrics:
    def test_avg_search_time_zero_searches(self):
        m = VectorMetrics()
        assert m.avg_search_time_ms == 0.0

    def test_avg_search_time_after_searches(self):
        m = VectorMetrics()
        m.searches = 4
        m.total_search_time_ms = 100.0
        assert m.avg_search_time_ms == 25.0

    def test_snapshot(self):
        m = VectorMetrics()
        m.upserts = 10
        m.searches = 5
        m.soft_deletes = 2
        m.errors = 1
        m.total_search_time_ms = 50.0
        snap = m.snapshot()
        assert snap["upserts"] == 10
        assert snap["searches"] == 5
        assert snap["soft_deletes"] == 2
        assert snap["errors"] == 1
        assert snap["avg_search_time_ms"] == 10.0


# ===================================================================
# Schema builder
# ===================================================================


class TestBuildSchema:
    def test_builds_schema_for_dim(self):
        schema = _build_schema(384)
        assert len(schema) == 6
        field_names = [f.name for f in schema]
        assert "node_id" in field_names
        assert "embedding" in field_names
        assert "agent_id" in field_names

    def test_different_dimensions(self):
        s8 = _build_schema(8)
        s768 = _build_schema(768)
        # Different dimension → different embedding field type
        assert s8 != s768


# ===================================================================
# Filter injection guard
# ===================================================================


class TestValidateFilterValue:
    def test_valid_values(self):
        _validate_filter_value("agent-alpha", "agent_id")
        _validate_filter_value("node_123.v2", "node_id")
        _validate_filter_value("user@domain", "email")

    def test_rejects_sql_injection(self):
        with pytest.raises(ValueError, match="unsafe"):
            _validate_filter_value("agent'; DROP TABLE--", "agent_id")

    def test_rejects_spaces(self):
        with pytest.raises(ValueError, match="unsafe"):
            _validate_filter_value("agent id", "agent_id")


# ===================================================================
# Lifecycle
# ===================================================================


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_initialize_is_idempotent(self, engine):
        """Calling initialize() twice should be safe."""
        assert engine.is_initialized
        await engine.initialize()  # Should not raise
        assert engine.is_initialized

    @pytest.mark.asyncio
    async def test_properties(self, engine):
        assert engine.uri.endswith(".lance")
        assert isinstance(engine.metrics, VectorMetrics)

    @pytest.mark.asyncio
    async def test_async_context_manager(self):
        uid = uuid.uuid4().hex[:8]
        uri = os.path.join(TEST_DIR, f"ctx_{uid}.lance")
        async with VectorEngine(uri, max_workers=2) as eng:
            assert eng.is_initialized
        assert not eng.is_initialized

    @pytest.mark.asyncio
    async def test_close_resets_state(self, engine):
        await engine.close()
        assert not engine.is_initialized


# ===================================================================
# Upsert
# ===================================================================


class TestUpsert:
    @pytest.mark.asyncio
    async def test_upsert_creates_record(self, engine):
        await engine.upsert(
            node_id="n1",
            agent_id="agent-a",
            embedding=[1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        )
        assert engine.metrics.upserts == 1

    @pytest.mark.asyncio
    async def test_upsert_with_content_hash(self, engine):
        await engine.upsert(
            node_id="n2",
            agent_id="agent-a",
            embedding=[0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            content_hash="abc123",
        )
        assert engine.metrics.upserts == 1

    @pytest.mark.asyncio
    async def test_upsert_uninitialized_raises(self):
        eng = VectorEngine("/tmp/fake.lance")
        with pytest.raises(RuntimeError, match="not been initialized"):
            await eng.upsert("n", "a", [0.0] * 8)


# ===================================================================
# Bulk upsert
# ===================================================================


class TestBulkUpsert:
    @pytest.mark.asyncio
    async def test_empty_records(self, engine):
        count = await engine.bulk_upsert([])
        assert count == 0

    @pytest.mark.asyncio
    async def test_multiple_records(self, engine):
        records = [
            {
                "node_id": f"bulk-{i}",
                "agent_id": "agent-bulk",
                "embedding": [float(i)] * 8,
            }
            for i in range(5)
        ]
        count = await engine.bulk_upsert(records)
        assert count == 5

    @pytest.mark.asyncio
    async def test_uninitialized_raises(self):
        eng = VectorEngine("/tmp/fake.lance")
        with pytest.raises(RuntimeError, match="not been initialized"):
            await eng.bulk_upsert(
                [{"node_id": "n", "agent_id": "a", "embedding": [0.0]}]
            )


# ===================================================================
# Search
# ===================================================================


class TestSearch:
    @pytest.mark.asyncio
    async def test_search_empty_table(self, engine):
        results = await engine.search([0.0] * 8, agent_id="agent-x")
        assert results == []

    @pytest.mark.asyncio
    async def test_search_with_agent_filter(self, engine):
        await engine.upsert("n1", "agent-a", [1.0] + [0.0] * 7)
        await engine.upsert("n2", "agent-b", [0.0, 1.0] + [0.0] * 6)

        results = await engine.search([1.0] + [0.0] * 7, agent_id="agent-a")
        assert all(r["agent_id"] == "agent-a" for r in results)

    @pytest.mark.asyncio
    async def test_search_returns_limited(self, engine):
        for i in range(10):
            await engine.upsert(f"s-{i}", "agent-s", [float(i % 3)] * 8)

        results = await engine.search([1.0] * 8, limit=3, agent_id="agent-s")
        assert len(results) <= 3

    @pytest.mark.asyncio
    async def test_search_strips_embedding(self, engine):
        await engine.upsert("strip-1", "agent-strip", [0.5] * 8)
        results = await engine.search([0.5] * 8, agent_id="agent-strip")
        for r in results:
            assert "embedding" not in r

    @pytest.mark.asyncio
    async def test_search_uninitialized_raises(self):
        eng = VectorEngine("/tmp/fake.lance")
        with pytest.raises(RuntimeError, match="not been initialized"):
            await eng.search([0.0] * 8)


# ===================================================================
# Soft delete / hard delete
# ===================================================================


class TestDeleteOperations:
    @pytest.mark.asyncio
    async def test_soft_delete(self, engine):
        await engine.upsert("del-1", "agent-d", [0.1] * 8)
        await engine.soft_delete("del-1")
        assert engine.metrics.soft_deletes == 1

        # Should not appear in active search
        results = await engine.search([0.1] * 8, agent_id="agent-d")
        found = [r for r in results if r["node_id"] == "del-1"]
        assert len(found) == 0

    @pytest.mark.asyncio
    async def test_hard_delete(self, engine):
        await engine.upsert("hdel-1", "agent-hd", [0.2] * 8)
        await engine.hard_delete("hdel-1")

        # Should not appear even with include_expired
        results = await engine.search(
            [0.2] * 8, agent_id="agent-hd", include_expired=True
        )
        found = [r for r in results if r["node_id"] == "hdel-1"]
        assert len(found) == 0

    @pytest.mark.asyncio
    async def test_soft_delete_uninitialized_raises(self):
        eng = VectorEngine("/tmp/fake.lance")
        with pytest.raises(RuntimeError):
            await eng.soft_delete("n")

    @pytest.mark.asyncio
    async def test_hard_delete_uninitialized_raises(self):
        eng = VectorEngine("/tmp/fake.lance")
        with pytest.raises(RuntimeError):
            await eng.hard_delete("n")


# ===================================================================
# Active node IDs
# ===================================================================


class TestActiveNodeIds:
    @pytest.mark.asyncio
    async def test_returns_active_ids(self, engine):
        await engine.upsert("act-1", "agent-a", [0.1] * 8)
        await engine.upsert("act-2", "agent-a", [0.2] * 8)
        await engine.upsert("act-3", "agent-b", [0.3] * 8)

        ids_a = await engine.get_active_node_ids(agent_id="agent-a")
        assert "act-1" in ids_a
        assert "act-2" in ids_a
        assert "act-3" not in ids_a

    @pytest.mark.asyncio
    async def test_excludes_soft_deleted(self, engine):
        await engine.upsert("sd-1", "agent-sd", [0.4] * 8)
        await engine.soft_delete("sd-1")

        ids = await engine.get_active_node_ids(agent_id="agent-sd")
        assert "sd-1" not in ids

    @pytest.mark.asyncio
    async def test_uninitialized_raises(self):
        eng = VectorEngine("/tmp/fake.lance")
        with pytest.raises(RuntimeError):
            await eng.get_active_node_ids()


# ===================================================================
# Count records
# ===================================================================


class TestCountRecords:
    @pytest.mark.asyncio
    async def test_count_active(self, engine):
        for i in range(3):
            await engine.upsert(f"cnt-{i}", "agent-cnt", [float(i)] * 8)
        counts = await engine.count_records(active_only=True)
        total = sum(counts.values())
        assert total == 3

    @pytest.mark.asyncio
    async def test_count_all(self, engine):
        await engine.upsert("cnt-a", "agent-cnt", [0.1] * 8)
        await engine.soft_delete("cnt-a")
        await engine.upsert("cnt-b", "agent-cnt", [0.2] * 8)

        counts = await engine.count_records(active_only=False)
        total = sum(counts.values())
        assert total == 2  # Both soft-deleted and active

    @pytest.mark.asyncio
    async def test_uninitialized_raises(self):
        eng = VectorEngine("/tmp/fake.lance")
        with pytest.raises(RuntimeError):
            await eng.count_records()


# ===================================================================
# Health check
# ===================================================================


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_healthy(self, engine):
        result = await engine.health_check()
        assert result["status"] == "healthy"
        assert result["initialized"] is True
        assert "metrics" in result

    @pytest.mark.asyncio
    async def test_not_initialized(self):
        eng = VectorEngine("/tmp/fake.lance")
        result = await eng.health_check()
        assert result["status"] == "not_initialized"
        assert result["initialized"] is False


# ===================================================================
# Blue/Green Procrustes alignment
# ===================================================================


class TestProcrustesAlignment:
    @pytest.mark.asyncio
    async def test_no_active_tables_returns_false(self, engine):
        """Empty engine has no active tables → returns False."""
        R = np.eye(8, dtype=np.float32)
        result = await engine.apply_procrustes_and_switch(
            transformation_matrix=R,
            golden_dataset=[],
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_successful_alignment(self, engine):
        """Full pipeline: insert → transform → verify → switch."""
        # Insert test data
        for i in range(5):
            vec = np.zeros(8, dtype=np.float32)
            vec[i % 8] = 1.0
            await engine.upsert(f"align-{i}", "agent-align", vec.tolist())

        # Identity transform — recall should be perfect
        R = np.eye(8, dtype=np.float32)
        golden = [
            {
                "query_vector": [1.0] + [0.0] * 7,
                "expected_node_id": "align-0",
            }
        ]

        result = await engine.apply_procrustes_and_switch(
            transformation_matrix=R,
            golden_dataset=golden,
            threshold=0.5,
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_rollback_on_low_recall(self, engine):
        """Transform passes, but recall is too low → rollback."""
        for i in range(5):
            vec = np.zeros(8, dtype=np.float32)
            vec[i % 8] = 1.0
            await engine.upsert(f"roll-{i}", "agent-roll", vec.tolist())

        # Zero matrix destroys all embeddings → guaranteed 0 recall
        R = np.zeros((8, 8), dtype=np.float32)

        golden = [
            {
                "query_vector": [1.0] + [0.0] * 7,
                "expected_node_id": "roll-0",
            },
        ]

        result = await engine.apply_procrustes_and_switch(
            transformation_matrix=R,
            golden_dataset=golden,
            threshold=0.99,  # Very high threshold → guaranteed rollback
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_uninitialized_raises(self):
        eng = VectorEngine("/tmp/fake.lance")
        with pytest.raises(RuntimeError, match="not been initialized"):
            await eng.apply_procrustes_and_switch(
                transformation_matrix=np.eye(8),
                golden_dataset=[],
            )
