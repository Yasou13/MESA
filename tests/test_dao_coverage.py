# MESA v0.7.1 — DAO Coverage Tests
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
    def test_valid_agent_id_accepted(self):
        _assert_valid_agent_id("agent-alpha")  # should not raise


# ===================================================================
# get_memory_by_id
# ===================================================================


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


