# MESA v0.3.0 — Phase 3: Vector Engine Test Suite
"""
Tests for the disk-backed VectorEngine: lifecycle, upsert, cosine search,
agent-scoped isolation, soft/hard delete, bulk operations, and metrics.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import uuid

import pytest
import pytest_asyncio

from mesa_storage.vector_engine import VectorEngine

TEST_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    ".test_storage_tmp",
    "vector_engine",
)

# Fixed 8-dim embeddings for deterministic cosine tests
VEC_A = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
VEC_B = [0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
VEC_CLOSE_A = [0.95, 0.05, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]


@pytest.fixture(autouse=True)
def _clean_test_dir():
    os.makedirs(TEST_DIR, exist_ok=True)
    yield
    shutil.rmtree(TEST_DIR, ignore_errors=True)


@pytest_asyncio.fixture
async def engine():
    uri = os.path.join(TEST_DIR, f"vec_{uuid.uuid4().hex[:8]}.lance")
    eng = VectorEngine(uri, max_workers=2)
    await eng.initialize()
    yield eng
    await eng.close()


# ===================================================================
# Lifecycle
# ===================================================================


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_initialize_creates_directory(self):
        uri = os.path.join(TEST_DIR, "init_test.lance")
        async with VectorEngine(uri) as eng:
            assert eng.is_initialized
            assert os.path.exists(uri)

    @pytest.mark.asyncio
    async def test_initialize_is_idempotent(self):
        uri = os.path.join(TEST_DIR, "idempotent.lance")
        eng = VectorEngine(uri)
        await eng.initialize()
        await eng.initialize()  # second call is a no-op
        assert eng.is_initialized
        await eng.close()

    @pytest.mark.asyncio
    async def test_operations_before_init_raise(self):
        eng = VectorEngine(os.path.join(TEST_DIR, "noinit.lance"))
        with pytest.raises(RuntimeError, match="not been initialized"):
            await eng.upsert("x", "a", VEC_A)
        with pytest.raises(RuntimeError, match="not been initialized"):
            await eng.search(VEC_A)

    @pytest.mark.asyncio
    async def test_async_context_manager(self):
        uri = os.path.join(TEST_DIR, "ctx.lance")
        async with VectorEngine(uri) as eng:
            assert eng.is_initialized
        assert not eng.is_initialized

    @pytest.mark.asyncio
    async def test_health_check_healthy(self, engine):
        result = await engine.health_check()
        assert result["status"] == "healthy"
        assert result["initialized"] is True

    @pytest.mark.asyncio
    async def test_health_check_not_initialized(self):
        eng = VectorEngine(os.path.join(TEST_DIR, "noinit_health.lance"))
        result = await eng.health_check()
        assert result["status"] == "not_initialized"


# ===================================================================
# Upsert & search
# ===================================================================


class TestUpsertSearch:
    @pytest.mark.asyncio
    async def test_upsert_and_search(self, engine):
        nid = uuid.uuid4().hex
        await engine.upsert(nid, "agent_1", VEC_A)

        results = await engine.search(VEC_A, limit=5)
        assert len(results) >= 1
        assert results[0]["node_id"] == nid
        assert results[0]["_distance"] == pytest.approx(0.0, abs=1e-4)

    @pytest.mark.asyncio
    async def test_cosine_ranking(self, engine):
        nid_a = uuid.uuid4().hex
        nid_b = uuid.uuid4().hex
        await engine.upsert(nid_a, "agent_1", VEC_A)
        await engine.upsert(nid_b, "agent_1", VEC_B)

        results = await engine.search(VEC_CLOSE_A, limit=10)
        # VEC_A should be closer to VEC_CLOSE_A than VEC_B
        assert results[0]["node_id"] == nid_a
        assert results[0]["_distance"] < results[1]["_distance"]

    @pytest.mark.asyncio
    async def test_upsert_updates_existing(self, engine):
        nid = uuid.uuid4().hex
        await engine.upsert(nid, "agent_1", VEC_A, content_hash="hash_v1")
        await engine.upsert(nid, "agent_1", VEC_B, content_hash="hash_v2")

        # Only one record for this node_id
        ids = await engine.get_active_node_ids()
        nid_count = sum(1 for i in ids if i == nid)
        assert nid_count == 1

        # Search should find the updated vector
        results = await engine.search(VEC_B, limit=5)
        match = [r for r in results if r["node_id"] == nid]
        assert len(match) == 1
        assert match[0]["content_hash"] == "hash_v2"

    @pytest.mark.asyncio
    async def test_search_returns_no_embedding(self, engine):
        """Embeddings are stripped from results to save memory."""
        await engine.upsert(uuid.uuid4().hex, "agent_1", VEC_A)
        results = await engine.search(VEC_A, limit=5)
        assert "embedding" not in results[0]

    @pytest.mark.asyncio
    async def test_search_empty_table(self, engine):
        results = await engine.search(VEC_A, limit=10)
        assert results == []


# ===================================================================
# Agent-scoped isolation
# ===================================================================


class TestAgentIsolation:
    @pytest.mark.asyncio
    async def test_search_filtered_by_agent(self, engine):
        nid_a = uuid.uuid4().hex
        nid_b = uuid.uuid4().hex
        await engine.upsert(nid_a, "agent_alpha", VEC_A)
        await engine.upsert(nid_b, "agent_beta", VEC_A)

        results = await engine.search(VEC_A, limit=10, agent_id="agent_alpha")
        node_ids = {r["node_id"] for r in results}
        assert nid_a in node_ids
        assert nid_b not in node_ids

    @pytest.mark.asyncio
    async def test_get_active_ids_filtered_by_agent(self, engine):
        await engine.upsert(uuid.uuid4().hex, "agent_x", VEC_A)
        await engine.upsert(uuid.uuid4().hex, "agent_y", VEC_B)

        ids_x = await engine.get_active_node_ids(agent_id="agent_x")
        ids_y = await engine.get_active_node_ids(agent_id="agent_y")
        assert len(ids_x) == 1
        assert len(ids_y) == 1
        assert ids_x != ids_y


# ===================================================================
# Soft & hard delete
# ===================================================================


class TestDelete:
    @pytest.mark.asyncio
    async def test_soft_delete_hides_from_search(self, engine):
        nid = uuid.uuid4().hex
        await engine.upsert(nid, "agent_1", VEC_A)
        await engine.soft_delete(nid)

        results = await engine.search(VEC_A, limit=10)
        assert not any(r["node_id"] == nid for r in results)

    @pytest.mark.asyncio
    async def test_soft_delete_visible_with_include_expired(self, engine):
        nid = uuid.uuid4().hex
        await engine.upsert(nid, "agent_1", VEC_A)
        await engine.soft_delete(nid)

        results = await engine.search(VEC_A, limit=10, include_expired=True)
        assert any(r["node_id"] == nid for r in results)

    @pytest.mark.asyncio
    async def test_hard_delete_removes_permanently(self, engine):
        nid = uuid.uuid4().hex
        await engine.upsert(nid, "agent_1", VEC_A)
        await engine.hard_delete(nid)

        results = await engine.search(VEC_A, limit=10, include_expired=True)
        assert not any(r["node_id"] == nid for r in results)


# ===================================================================
# Bulk operations
# ===================================================================


class TestBulk:
    @pytest.mark.asyncio
    async def test_bulk_upsert(self, engine):
        records = [
            {
                "node_id": uuid.uuid4().hex,
                "agent_id": "bulk_agent",
                "embedding": VEC_A,
            }
            for _ in range(20)
        ]
        count = await engine.bulk_upsert(records)
        assert count == 20

        ids = await engine.get_active_node_ids()
        assert len(ids) == 20

    @pytest.mark.asyncio
    async def test_bulk_upsert_empty(self, engine):
        count = await engine.bulk_upsert([])
        assert count == 0

    @pytest.mark.asyncio
    async def test_bulk_upsert_mixed_dimensions(self, engine):
        """Records with different dimensions go to separate tables."""
        records = [
            {
                "node_id": uuid.uuid4().hex,
                "agent_id": "agent_1",
                "embedding": [1.0] * 8,
            },
            {
                "node_id": uuid.uuid4().hex,
                "agent_id": "agent_1",
                "embedding": [1.0] * 16,
            },
        ]
        count = await engine.bulk_upsert(records)
        assert count == 2

        counts = await engine.count_records()
        assert len(counts) == 2


# ===================================================================
# Metrics & count
# ===================================================================


class TestMetrics:
    @pytest.mark.asyncio
    async def test_metrics_track_operations(self, engine):
        await engine.upsert(uuid.uuid4().hex, "a", VEC_A)
        await engine.search(VEC_A, limit=5)
        await engine.upsert(uuid.uuid4().hex, "a", VEC_B)

        snap = engine.metrics.snapshot()
        assert snap["upserts"] == 2
        assert snap["searches"] == 1
        assert snap["errors"] == 0
        assert snap["avg_search_time_ms"] > 0

    @pytest.mark.asyncio
    async def test_count_records(self, engine):
        for _ in range(5):
            await engine.upsert(uuid.uuid4().hex, "a", VEC_A)

        counts = await engine.count_records(active_only=True)
        total = sum(counts.values())
        assert total == 5


# ===================================================================
# Concurrency — event loop never blocks
# ===================================================================


class TestConcurrency:
    @pytest.mark.asyncio
    async def test_concurrent_upserts(self, engine):
        async def _worker(idx: int):
            nid = uuid.uuid4().hex
            await engine.upsert(nid, "concurrent_agent", VEC_A)
            return nid

        ids = await asyncio.gather(*[_worker(i) for i in range(16)])
        assert len(ids) == 16
        active = await engine.get_active_node_ids()
        assert len(active) == 16

    @pytest.mark.asyncio
    async def test_concurrent_search_during_writes(self, engine):
        """Searches and writes can happen concurrently without deadlock."""
        nid = uuid.uuid4().hex
        await engine.upsert(nid, "agent_1", VEC_A)

        async def _searcher():
            return await engine.search(VEC_A, limit=5)

        async def _writer(idx: int):
            await engine.upsert(uuid.uuid4().hex, "agent_1", VEC_B)

        results = await asyncio.gather(
            _searcher(),
            *[_writer(i) for i in range(4)],
        )
        # First result is search output
        assert len(results[0]) >= 1
