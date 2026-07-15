# MESA v0.3.0 — MemoryDAO Unit Tests (Coverage Gap Filler)
"""
Tests targeting untested DAO paths: agent_id validation, edge operations,
get_neighbors directions, mark_consolidated, health_check, bulk_insert,
get_memories filters, FTS5 error handling, and purge edge cases.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from mesa_storage.dao import MemoryDAO, _assert_valid_agent_id
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.vector_engine import VectorEngine

TEST_DIR = os.path.join(os.path.dirname(__file__), ".test_storage_tmp", "dao")


@pytest.fixture(autouse=True)
def _clean():
    os.makedirs(TEST_DIR, exist_ok=True)
    yield
    shutil.rmtree(TEST_DIR, ignore_errors=True)


@pytest.fixture
def dao_env():
    uid = uuid.uuid4().hex[:8]
    db = os.path.join(TEST_DIR, f"dao_{uid}.db")
    vec = os.path.join(TEST_DIR, f"vec_{uid}.lance")
    sql = AsyncEngine(db, max_connections=2)
    vec_eng = VectorEngine(vec, max_workers=1)
    mock_kuzu = MagicMock()
    mock_kuzu.is_initialized = True
    mock_kuzu.execute_query = AsyncMock(return_value=[])
    mock_kuzu.insert_entity = AsyncMock()
    mock_kuzu.insert_edge = AsyncMock()
    mock_kuzu.get_neighbors = AsyncMock(
        return_value=[{"id": "n2", "name": "TestEntity", "hops": "1"}]
    )
    mock_kuzu.initialize = AsyncMock()
    mock_kuzu.close = AsyncMock()
    graph_eng = mock_kuzu
    loop = asyncio.new_event_loop()
    loop.run_until_complete(sql.initialize())
    loop.run_until_complete(initialize_schema(sql))
    loop.run_until_complete(vec_eng.initialize())
    loop.run_until_complete(graph_eng.initialize())
    dao = MemoryDAO(sqlite_engine=sql, vector_engine=vec_eng, graph_provider=graph_eng)
    yield dao, sql, vec_eng, loop
    loop.run_until_complete(sql.close())
    loop.run_until_complete(vec_eng.close())
    loop.run_until_complete(graph_eng.close())
    loop.close()


VEC8 = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]


# === Agent ID Validation ===
class TestAgentIdValidation:
    def test_empty_raises(self):
        with pytest.raises(ValueError):
            _assert_valid_agent_id("")

    def test_unset_raises(self):
        with pytest.raises(ValueError):
            _assert_valid_agent_id("__unset__")

    def test_system_raises(self):
        with pytest.raises(ValueError):
            _assert_valid_agent_id("__system__")

    def test_valid_passes(self):
        _assert_valid_agent_id("agent-1")  # no exception


# === Insert ===
class TestInsertMemory:
    def test_insert_returns_node_id(self, dao_env):
        dao, _, _, loop = dao_env
        nid = loop.run_until_complete(
            dao.insert_memory("agent-1", entity_name="E1", content="c", embedding=VEC8)
        )
        assert isinstance(nid, str) and len(nid) > 0

    def test_insert_rejects_bad_agent(self, dao_env):
        dao, _, _, loop = dao_env
        with pytest.raises(ValueError):
            loop.run_until_complete(
                dao.insert_memory(
                    "__unset__", entity_name="E", content="c", embedding=VEC8
                )
            )

    def test_insert_auto_generates_id(self, dao_env):
        dao, _, _, loop = dao_env
        nid = loop.run_until_complete(
            dao.insert_memory("agent-1", entity_name="E", content="c", embedding=VEC8)
        )
        assert len(nid) == 36  # UUID format

    def test_insert_custom_id(self, dao_env):
        dao, _, _, loop = dao_env
        nid = loop.run_until_complete(
            dao.insert_memory(
                "agent-1",
                node_id="custom-id",
                entity_name="E",
                content="c",
                embedding=VEC8,
            )
        )
        assert nid == "custom-id"


# === Bulk Insert ===
class TestBulkInsert:
    def test_bulk_empty_returns_zero(self, dao_env):
        dao, _, _, loop = dao_env
        c = loop.run_until_complete(dao.bulk_insert_memory("agent-1", records=[]))
        assert c == 0

    def test_bulk_inserts_multiple(self, dao_env):
        dao, _, _, loop = dao_env
        recs = [
            {"entity_name": f"E{i}", "content": "c", "embedding": VEC8}
            for i in range(5)
        ]
        c = loop.run_until_complete(dao.bulk_insert_memory("agent-1", records=recs))
        assert c == 5

    def test_bulk_rejects_bad_agent(self, dao_env):
        dao, _, _, loop = dao_env
        with pytest.raises(ValueError):
            loop.run_until_complete(
                dao.bulk_insert_memory(
                    "",
                    records=[{"entity_name": "E", "content": "c", "embedding": VEC8}],
                )
            )


# === Get Memories ===
class TestGetMemories:
    def test_empty_agent_returns_empty(self, dao_env):
        dao, _, _, loop = dao_env
        r = loop.run_until_complete(dao.get_memories("agent-new"))
        assert r == []

    def test_filters_unconsolidated_only(self, dao_env):
        dao, _, _, loop = dao_env
        loop.run_until_complete(
            dao.insert_memory("agent-1", entity_name="E1", content="c", embedding=VEC8)
        )
        r = loop.run_until_complete(
            dao.get_memories("agent-1", include_consolidated=False)
        )
        assert len(r) >= 1
        assert all(row["is_consolidated"] == 0 for row in r)

    def test_with_limit(self, dao_env):
        dao, _, _, loop = dao_env
        for i in range(10):
            loop.run_until_complete(
                dao.insert_memory(
                    "agent-1", entity_name=f"E{i}", content="c", embedding=VEC8
                )
            )
        r = loop.run_until_complete(dao.get_memories("agent-1", limit=3))
        assert len(r) == 3

    def test_rejects_bad_agent(self, dao_env):
        dao, _, _, loop = dao_env
        with pytest.raises(ValueError):
            loop.run_until_complete(dao.get_memories("__system__"))


# === Mark Consolidated ===
class TestMarkConsolidated:
    def test_marks_node(self, dao_env):
        dao, _, _, loop = dao_env
        nid = loop.run_until_complete(
            dao.insert_memory("agent-1", entity_name="E1", content="c", embedding=VEC8)
        )
        loop.run_until_complete(dao.mark_consolidated("agent-1", node_id=nid))
        mems = loop.run_until_complete(dao.get_memories("agent-1"))
        found = [m for m in mems if m["id"] == nid]
        assert found[0]["is_consolidated"] == 1

    def test_rejects_bad_agent(self, dao_env):
        dao, _, _, loop = dao_env
        with pytest.raises(ValueError):
            loop.run_until_complete(dao.mark_consolidated("", node_id="x"))


# === Edge Operations ===
class TestEdgeOperations:
    def test_insert_edge_auto_id(self, dao_env):
        dao, _, _, loop = dao_env
        n1 = loop.run_until_complete(
            dao.insert_memory("agent-1", entity_name="A", content="c", embedding=VEC8)
        )
        n2 = loop.run_until_complete(
            dao.insert_memory("agent-1", entity_name="B", content="c", embedding=VEC8)
        )
        eid = loop.run_until_complete(
            dao.insert_edge(
                "agent-1", source_id=n1, target_id=n2, relation_type="RELATED"
            )
        )
        assert isinstance(eid, str) and len(eid) > 0

    def test_insert_edge_custom_id(self, dao_env):
        dao, _, _, loop = dao_env
        n1 = loop.run_until_complete(
            dao.insert_memory("agent-1", entity_name="A", content="c", embedding=VEC8)
        )
        n2 = loop.run_until_complete(
            dao.insert_memory("agent-1", entity_name="B", content="c", embedding=VEC8)
        )
        eid = loop.run_until_complete(
            dao.insert_edge(
                "agent-1",
                source_id=n1,
                target_id=n2,
                relation_type="R",
                edge_id="my-edge",
            )
        )
        assert eid == f"{n1}->{n2}"

    def test_insert_edge_rejects_bad_agent(self, dao_env):
        dao, _, _, loop = dao_env
        with pytest.raises(ValueError):
            loop.run_until_complete(
                dao.insert_edge(
                    "__unset__", source_id="a", target_id="b", relation_type="R"
                )
            )


# === Get Neighbors ===
class TestGetNeighbors:
    def _seed(self, dao, loop):
        n1 = loop.run_until_complete(
            dao.insert_memory("agent-1", entity_name="A", content="c", embedding=VEC8)
        )
        n2 = loop.run_until_complete(
            dao.insert_memory("agent-1", entity_name="B", content="c", embedding=VEC8)
        )
        loop.run_until_complete(
            dao.insert_edge(
                "agent-1", source_id=n1, target_id=n2, relation_type="LINKS"
            )
        )
        return n1, n2

    def test_both_direction(self, dao_env):
        dao, _, _, loop = dao_env
        n1, n2 = self._seed(dao, loop)
        r = loop.run_until_complete(
            dao.get_neighbors("agent-1", node_id=n1, direction="both")
        )
        assert len(r) >= 1

    def test_out_direction(self, dao_env):
        dao, _, _, loop = dao_env
        n1, n2 = self._seed(dao, loop)
        r = loop.run_until_complete(
            dao.get_neighbors("agent-1", node_id=n1, direction="out")
        )
        assert len(r) >= 1

    def test_in_direction(self, dao_env):
        dao, _, _, loop = dao_env
        n1, n2 = self._seed(dao, loop)
        r = loop.run_until_complete(
            dao.get_neighbors("agent-1", node_id=n2, direction="in")
        )
        assert len(r) >= 1

    def test_no_neighbors(self, dao_env):
        dao, _, _, loop = dao_env
        dao.graph_provider.get_neighbors.return_value = []
        nid = loop.run_until_complete(
            dao.insert_memory(
                "agent-1", entity_name="Lone", content="c", embedding=VEC8
            )
        )
        r = loop.run_until_complete(dao.get_neighbors("agent-1", node_id=nid))
        assert r == []

    def test_rejects_bad_agent(self, dao_env):
        dao, _, _, loop = dao_env
        with pytest.raises(ValueError):
            loop.run_until_complete(dao.get_neighbors("", node_id="x"))


# === FTS5 Edge Cases ===
class TestFTS5EdgeCases:
    def test_empty_query_returns_empty(self, dao_env):
        dao, _, _, loop = dao_env
        r = loop.run_until_complete(dao.search_memory_fts("agent-1", query=""))
        assert r == []

    def test_whitespace_query_returns_empty(self, dao_env):
        dao, _, _, loop = dao_env
        r = loop.run_until_complete(dao.search_memory_fts("agent-1", query="   "))
        assert r == []

    def test_rejects_bad_agent(self, dao_env):
        dao, _, _, loop = dao_env
        with pytest.raises(ValueError):
            loop.run_until_complete(dao.search_memory_fts("__system__", query="test"))


# === Purge Edge Cases ===
class TestPurgeEdgeCases:
    def test_purge_empty_agent_returns_zero(self, dao_env):
        dao, _, _, loop = dao_env
        c = loop.run_until_complete(dao.purge_memory("agent-empty"))
        assert c == 0

    def test_purge_session_missing_id_raises(self, dao_env):
        dao, _, _, loop = dao_env
        with pytest.raises(ValueError):
            loop.run_until_complete(
                dao.purge_memory("agent-1", scope="session", session_id=None)
            )

    def test_purge_rejects_bad_agent(self, dao_env):
        dao, _, _, loop = dao_env
        with pytest.raises(ValueError):
            loop.run_until_complete(dao.purge_memory("__unset__"))


# === Health Check ===
class TestHealthCheck:
    def test_returns_both_engines(self, dao_env):
        dao, _, _, loop = dao_env
        h = loop.run_until_complete(dao.health_check())
        assert "sqlite" in h
        assert "vector" in h


# === Properties ===
class TestDAOProperties:
    def test_vector_engine(self, dao_env):
        dao, _, vec, _ = dao_env
        assert dao.vector_engine is vec
