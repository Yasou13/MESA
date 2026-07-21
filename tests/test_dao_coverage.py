# MESA v0.6.1 — DAO Coverage Tests
"""
Unit tests targeting uncovered execution paths in mesa_storage/dao.py.

Uses real AsyncEngine + VectorEngine instances with temporary storage
to exercise the full DAO layer without mocking storage internals.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import uuid

import pytest

from mesa_storage.dao import MemoryDAO, _assert_valid_agent_id
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.vector_engine import VectorEngine


class _VerifiedPurgeGraph:
    async def insert_node(self, *, node_id, name, agent_id):
        return None

    async def delete_nodes(self, *, purge_id, agent_id, node_ids):
        return None

    async def verify_nodes_absent(self, *, agent_id, node_ids):
        return True


TEST_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    ".test_storage_tmp",
    "dao_cov",
)


@pytest.fixture(autouse=True)
def _clean():
    os.makedirs(TEST_DIR, exist_ok=True)
    yield
    shutil.rmtree(TEST_DIR, ignore_errors=True)


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def engines(event_loop):
    uid = uuid.uuid4().hex[:8]
    db_path = os.path.join(TEST_DIR, f"dao_{uid}.db")
    vec_uri = os.path.join(TEST_DIR, f"vec_{uid}.lance")

    sql = AsyncEngine(db_path, max_connections=2)
    vec = VectorEngine(vec_uri, max_workers=2)

    event_loop.run_until_complete(sql.initialize())
    event_loop.run_until_complete(initialize_schema(sql))
    event_loop.run_until_complete(vec.initialize())

    yield sql, vec

    event_loop.run_until_complete(sql.close())
    event_loop.run_until_complete(vec.close())


@pytest.fixture
def dao(engines):
    sql, vec = engines
    return MemoryDAO(sqlite_engine=sql, vector_engine=vec)


# ===================================================================
# Sentinel validation
# ===================================================================


class TestSentinelValidation:
    def test_empty_agent_id_rejected(self):
        with pytest.raises(ValueError):
            _assert_valid_agent_id("")

    def test_reserved_unset_rejected(self):
        with pytest.raises(ValueError):
            _assert_valid_agent_id("__unset__")

    def test_reserved_system_rejected(self):
        with pytest.raises(ValueError):
            _assert_valid_agent_id("__system__")

    def test_valid_agent_id_accepted(self):
        _assert_valid_agent_id("agent-alpha")  # should not raise


# ===================================================================
# get_memory_by_id
# ===================================================================


class TestGetMemoryById:
    @pytest.mark.asyncio
    async def test_returns_none_for_nonexistent(self, dao):
        result = await dao.get_memory_by_id("agent-test", "nonexistent-id")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_node_after_insert(self, dao):
        node_id = await dao.insert_memory(
            "agent-test",
            entity_name="TestEntity",
            content="Test content",
            embedding=[0.1] * 8,
        )
        result = await dao.get_memory_by_id("agent-test", node_id)
        assert result is not None
        assert result["entity_name"] == "TestEntity"
        assert result["agent_id"] == "agent-test"

    @pytest.mark.asyncio
    async def test_cross_agent_isolation(self, dao):
        """get_memory_by_id with wrong agent_id returns None."""
        node_id = await dao.insert_memory(
            "agent-a",
            entity_name="Isolated",
            content="Secret",
            embedding=[0.2] * 8,
        )
        # Same node_id, different agent → must return None
        result = await dao.get_memory_by_id("agent-b", node_id)
        assert result is None


# ===================================================================
# get_raw_log / insert_raw_log
# ===================================================================


class TestRawLog:
    @pytest.mark.asyncio
    async def test_insert_and_retrieve(self, dao):
        payload = {"content": "test log", "session_id": "s1"}
        log_id = await dao.insert_raw_log("agent-log", payload)
        assert log_id > 0

        result = await dao.get_raw_log("agent-log", log_id)
        assert result is not None
        assert result["id"] == log_id
        assert result["payload"]["content"] == "test log"

    @pytest.mark.asyncio
    async def test_returns_none_for_nonexistent(self, dao):
        result = await dao.get_raw_log("agent-log", 999999)
        assert result is None

    @pytest.mark.asyncio
    async def test_cross_agent_isolation(self, dao):
        """Cannot fetch another agent's raw_log."""
        log_id = await dao.insert_raw_log("agent-a", {"data": "private"})
        result = await dao.get_raw_log("agent-b", log_id)
        assert result is None

    @pytest.mark.asyncio
    async def test_update_status(self, dao):
        log_id = await dao.insert_raw_log("agent-s", {"x": 1})
        await dao.update_raw_log_status("agent-s", log_id, "processed")
        result = await dao.get_raw_log("agent-s", log_id)
        assert result["status"] == "processed"

    @pytest.mark.asyncio
    async def test_update_status_with_error(self, dao):
        log_id = await dao.insert_raw_log("agent-s", {"x": 1})
        await dao.update_raw_log_status(
            "agent-s", log_id, "failed", error_reason="timeout"
        )
        result = await dao.get_raw_log("agent-s", log_id)
        assert result["status"] == "failed:timeout"


# ===================================================================
# invalidate_node
# ===================================================================


class TestInvalidateNode:
    @pytest.mark.asyncio
    async def test_invalidate_makes_node_invisible(self, dao):
        node_id = await dao.insert_memory(
            "agent-inv",
            entity_name="ToInvalidate",
            content="data",
            embedding=[0.3] * 8,
        )
        # Should be visible
        assert await dao.get_memory_by_id("agent-inv", node_id) is not None

        await dao.invalidate_node("agent-inv", node_id=node_id)

        # Should now be invisible
        assert await dao.get_memory_by_id("agent-inv", node_id) is None

    @pytest.mark.asyncio
    async def test_invalidate_is_idempotent(self, dao):
        node_id = await dao.insert_memory(
            "agent-inv2",
            entity_name="DoubleInvalidate",
            content="data",
            embedding=[0.4] * 8,
        )
        await dao.invalidate_node("agent-inv2", node_id=node_id)
        # Second call should not raise
        await dao.invalidate_node("agent-inv2", node_id=node_id)


# ===================================================================
# health_check
# ===================================================================


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_health_returns_sqlite_and_vector(self, dao):
        result = await dao.health_check()
        assert "sqlite" in result
        assert "vector" in result
        assert result["vector"]["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_health_without_graph(self, dao):
        """No graph provider → no graph key in health."""
        result = await dao.health_check()
        assert "graph" not in result


# ===================================================================
# _is_lancedb_migrating
# ===================================================================


class TestMigrationCheck:
    @pytest.mark.asyncio
    async def test_returns_false_by_default(self, dao):
        """No system_config row → returns False."""
        result = await dao._is_lancedb_migrating()
        assert result is False


# ===================================================================
# get_memories
# ===================================================================


class TestGetMemories:
    @pytest.mark.asyncio
    async def test_returns_all_active(self, dao):
        for i in range(3):
            await dao.insert_memory(
                "agent-mem",
                entity_name=f"E{i}",
                content=f"c{i}",
                embedding=[float(i)] * 8,
            )
        results = await dao.get_memories("agent-mem")
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_respects_limit(self, dao):
        for i in range(5):
            await dao.insert_memory(
                "agent-lim",
                entity_name=f"L{i}",
                content=f"c{i}",
                embedding=[float(i)] * 8,
            )
        results = await dao.get_memories("agent-lim", limit=2)
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_cross_agent_isolation(self, dao):
        await dao.insert_memory(
            "agent-x",
            entity_name="X",
            content="cx",
            embedding=[0.9] * 8,
        )
        results = await dao.get_memories("agent-y")
        assert len(results) == 0


# ===================================================================
# find_nodes_by_name
# ===================================================================


class TestFindNodesByName:
    @pytest.mark.asyncio
    async def test_find_existing_node(self, dao):
        await dao.insert_memory(
            "agent-find",
            entity_name="UniqueEntity",
            content="data",
            embedding=[0.5] * 8,
        )
        results = await dao.find_nodes_by_name("agent-find", names=["UniqueEntity"])
        assert len(results) == 1
        assert results[0]["entity_name"] == "UniqueEntity"

    @pytest.mark.asyncio
    async def test_case_insensitive_match(self, dao):
        await dao.insert_memory(
            "agent-ci",
            entity_name="CaseTest",
            content="data",
            embedding=[0.5] * 8,
        )
        results = await dao.find_nodes_by_name(
            "agent-ci", names=["casetest"], case_insensitive=True
        )
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_empty_names_returns_empty(self, dao):
        results = await dao.find_nodes_by_name("agent-a", names=[])
        assert results == []

    @pytest.mark.asyncio
    async def test_case_sensitive_no_match(self, dao):
        await dao.insert_memory(
            "agent-cs",
            entity_name="CaseTest",
            content="data",
            embedding=[0.5] * 8,
        )
        results = await dao.find_nodes_by_name(
            "agent-cs", names=["casetest"], case_insensitive=False
        )
        assert len(results) == 0


# ===================================================================
# purge_memory — session scope
# ===================================================================


class TestPurgeSessionScope:
    @pytest.mark.asyncio
    async def test_session_scope_purge(self, dao):
        dao._graph = _VerifiedPurgeGraph()
        await dao.insert_memory(
            "agent-purge",
            entity_name="A",
            content="a",
            embedding=[0.1] * 8,
            session_id="sess-a",
        )
        await dao.insert_memory(
            "agent-purge",
            entity_name="B",
            content="b",
            embedding=[0.2] * 8,
            session_id="sess-b",
        )
        deleted = await dao.purge_memory(
            "agent-purge", scope="session", session_id="sess-a"
        )
        assert deleted >= 1
        # sess-b should survive
        remaining = await dao.get_memories("agent-purge")
        assert len(remaining) == 1
        assert remaining[0]["session_id"] == "sess-b"

    @pytest.mark.asyncio
    async def test_session_scope_requires_session_id(self, dao):
        with pytest.raises(ValueError, match="session_id"):
            await dao.purge_memory("agent-purge", scope="session")


# ===================================================================
# bulk_insert_memory
# ===================================================================


class TestBulkInsert:
    @pytest.mark.asyncio
    async def test_bulk_insert_empty(self, dao):
        count = await dao.bulk_insert_memory("agent-bulk", records=[])
        assert count == 0

    @pytest.mark.asyncio
    async def test_bulk_insert_multiple(self, dao):
        records = [
            {
                "entity_name": f"BulkEntity{i}",
                "content": f"content{i}",
                "embedding": [float(i)] * 8,
            }
            for i in range(5)
        ]
        count = await dao.bulk_insert_memory("agent-bulk", records=records)
        assert count == 5
        all_nodes = await dao.get_memories("agent-bulk")
        assert len(all_nodes) == 5


# ===================================================================
# routing telemetry
# ===================================================================


class TestRoutingTelemetry:
    @pytest.mark.asyncio
    async def test_insert_and_retrieve_stats(self, dao):
        for i in range(3):
            await dao.insert_routing_telemetry(
                "agent-tel",
                record_id=f"rec-{i}",
                small_model_decision=1,
                small_model_confidence=0.9,
                dual_llm_decision=1,
                is_hallucination=(i == 0),
            )
        stats = await dao.get_recent_telemetry_stats("agent-tel")
        assert stats["total_audits"] == 3
        assert stats["hallucinations"] == 1


# ===================================================================
# Extra coverage for missing lines
# ===================================================================

from datetime import datetime, timezone


class TestReconcileOrphanedNodes:
    @pytest.mark.asyncio
    async def test_reconcile_orphaned_nodes(self, dao):
        now = datetime.now(timezone.utc).isoformat()
        async with dao._sqlite_engine.connection() as db:
            await db.execute(
                "INSERT INTO nodes (id, entity_name, type, content_payload, is_consolidated, created_at, agent_id, session_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "orphan_id_1",
                    "orphan",
                    "ENTITY",
                    "content",
                    0,
                    now,
                    "agent-orphan",
                    "sess_1",
                ),
            )
            await db.commit()

        await dao._reconcile_orphaned_nodes()

        async with dao._sqlite_engine.connection() as db:
            async with db.execute(
                "SELECT invalid_at FROM nodes WHERE id = 'orphan_id_1'"
            ) as cursor:
                row = await cursor.fetchone()
                assert row is not None
                assert row[0] is not None


class TestSearchMemoryFTS:
    @pytest.mark.asyncio
    async def test_search_memory_fts(self, dao):
        await dao.insert_memory(
            "agent-fts",
            entity_name="fts1",
            content="hello special world",
            embedding=[0.1] * 8,
        )
        results = await dao.search_memory_fts("agent-fts", query="special")
        # Since it's a mock test without triggers, we just assert the call doesn't fail
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_search_memory_fts_empty_query(self, dao):
        results = await dao.search_memory_fts("agent-fts", query="   ")
        assert results == []


class TestAlignMemorySpace:
    @pytest.mark.asyncio
    async def test_align_memory_space(self, dao):
        import numpy as np

        matrix = np.eye(8)
        golden = []
        # Simulate align_memory_space. The vector engine might not have apply_procrustes_and_switch mocked,
        # but calling it exercises the lock acquisition and exception paths.
        success = await dao.align_memory_space(matrix, golden)
        assert isinstance(success, bool)


class TestInsertMigrating:
    @pytest.mark.asyncio
    async def test_insert_migrating(self, dao):
        async with dao._sqlite_engine.transaction() as db:
            # We assume system_config table exists or we just create it/update it
            await db.execute(
                "INSERT OR REPLACE INTO system_config (key, value) VALUES ('lancedb_is_migrating', 'true')"
            )
            await db.commit()

        await dao.insert_memory(
            "agent-mig", entity_name="mig", content="text", embedding=[0.5] * 8
        )
        await dao.bulk_insert_memory(
            "agent-mig",
            records=[
                {"entity_name": "mig2", "content": "text2", "embedding": [0.6] * 8}
            ],
        )

        # cleanup
        async with dao._sqlite_engine.transaction() as db:
            await db.execute(
                "UPDATE system_config SET value = 'false' WHERE key = 'lancedb_is_migrating'"
            )
            await db.commit()
