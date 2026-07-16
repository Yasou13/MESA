"""MESA v0.4.1 — Phase 3B: Cross-Tenant RBAC Leakage Test.

Mathematically proves that the DAO's mandatory ``WHERE agent_id = ?``
predicate prevents cross-tenant data access.  The test inserts data
under agent_A, then exhaustively verifies that agent_B's queries —
via get_memories, search_memory_fts, and search_memory — return
strictly empty result sets.

This is a Zero-Trust proof, not a smoke test: we assert the *absence*
of data, not just the presence of expected data.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import uuid

import pytest

from mesa_storage.dao import MemoryDAO
from mesa_storage.kuzu_provider import KuzuGraphProvider
from mesa_storage.kuzu_setup import initialize_schema as init_kuzu_schema
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.vector_engine import VectorEngine

TEST_DIR = os.path.join(os.path.dirname(__file__), ".test_storage_tmp", "rbac_leak")
VEC8 = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

AGENT_A = "tenant-alpha"
AGENT_B = "tenant-beta"
SENTINEL_ENTITY = "TopSecret_Contract_§42"
SENTINEL_CONTENT = "This clause governs liability under Turkish law TBK m.49"


@pytest.fixture(autouse=True)
def _clean():
    os.makedirs(TEST_DIR, exist_ok=True)
    yield
    shutil.rmtree(TEST_DIR, ignore_errors=True)


@pytest.fixture
def dao_env():
    uid = uuid.uuid4().hex[:8]
    db_path = os.path.join(TEST_DIR, f"rbac_{uid}.db")
    vec_path = os.path.join(TEST_DIR, f"vec_{uid}.lance")
    graph_path = os.path.join(TEST_DIR, f"graph_{uid}.kuzu")
    sql = AsyncEngine(db_path, max_connections=2)
    vec = VectorEngine(vec_path, max_workers=1)
    init_kuzu_schema(graph_path)
    graph_eng = KuzuGraphProvider(db_path=graph_path)
    loop = asyncio.new_event_loop()
    loop.run_until_complete(sql.initialize())
    loop.run_until_complete(initialize_schema(sql))
    loop.run_until_complete(vec.initialize())
    loop.run_until_complete(graph_eng.initialize())
    dao = MemoryDAO(sqlite_engine=sql, vector_engine=vec, graph_provider=graph_eng)
    yield dao, sql, vec, loop
    loop.run_until_complete(sql.close())
    loop.run_until_complete(vec.close())
    loop.run_until_complete(graph_eng.close())
    loop.close()


class TestCrossTenantIsolation:
    """Prove zero cross-tenant leakage across all DAO retrieval surfaces."""

    def _seed_agent_a(self, dao: MemoryDAO, loop: asyncio.AbstractEventLoop) -> str:
        """Insert a sentinel memory node exclusively under agent_A."""
        node_id = loop.run_until_complete(
            dao.insert_memory(
                AGENT_A,
                entity_name=SENTINEL_ENTITY,
                content=SENTINEL_CONTENT,
                embedding=VEC8,
                node_type="ENTITY",
                session_id="session-alpha",
            )
        )
        # Verify the node actually exists for agent_A (control assertion)
        memories = loop.run_until_complete(dao.get_memories(AGENT_A))
        assert any(
            m["entity_name"] == SENTINEL_ENTITY for m in memories
        ), "Control failed: agent_A seed data not found"
        return node_id

    def test_get_memories_isolation(self, dao_env):
        """agent_B.get_memories() must return [] when only agent_A has data."""
        dao, _, _, loop = dao_env
        self._seed_agent_a(dao, loop)

        agent_b_memories = loop.run_until_complete(dao.get_memories(AGENT_B))

        assert agent_b_memories == [], (
            f"RBAC VIOLATION: agent_B retrieved {len(agent_b_memories)} records "
            f"belonging to agent_A"
        )

    def test_fts_search_isolation(self, dao_env):
        """agent_B.search_memory_fts() must not return agent_A's entities."""
        dao, _, _, loop = dao_env
        self._seed_agent_a(dao, loop)

        # Search using the exact sentinel entity name — should be invisible
        agent_b_fts = loop.run_until_complete(
            dao.search_memory_fts(AGENT_B, query=SENTINEL_ENTITY)
        )

        assert agent_b_fts == [], (
            f"RBAC VIOLATION: FTS5 search for agent_B returned "
            f"{len(agent_b_fts)} results from agent_A"
        )

    def test_vector_search_isolation(self, dao_env):
        """agent_B.search_memory() must not return agent_A's vectors."""
        dao, _, _, loop = dao_env
        self._seed_agent_a(dao, loop)

        # Use the exact same embedding that agent_A inserted — should be
        # a perfect cosine match, but the agent_id filter must block it.
        agent_b_vec = loop.run_until_complete(
            dao.search_memory(AGENT_B, query_vector=VEC8, limit=10)
        )

        assert agent_b_vec == [], (
            f"RBAC VIOLATION: vector search for agent_B returned "
            f"{len(agent_b_vec)} results from agent_A"
        )

    def test_cross_tenant_isolation(self, dao_env):
        """Combined proof: all three retrieval surfaces are airtight."""
        dao, _, _, loop = dao_env
        node_id = self._seed_agent_a(dao, loop)

        # 1. Direct memory retrieval
        b_memories = loop.run_until_complete(dao.get_memories(AGENT_B))
        assert b_memories == [], "Leakage via get_memories"

        # 2. FTS5 lexical search
        b_fts = loop.run_until_complete(
            dao.search_memory_fts(AGENT_B, query=SENTINEL_ENTITY)
        )
        assert b_fts == [], "Leakage via search_memory_fts"

        # 3. Vector similarity search
        b_vec = loop.run_until_complete(
            dao.search_memory(AGENT_B, query_vector=VEC8, limit=10)
        )
        assert b_vec == [], "Leakage via search_memory"

        # 4. Edge isolation — if agent_A has edges, agent_B must not see them
        n2 = loop.run_until_complete(
            dao.insert_memory(
                AGENT_A,
                entity_name="RelatedEntity",
                content="related",
                embedding=VEC8,
            )
        )
        loop.run_until_complete(
            dao.insert_edge(
                AGENT_A,
                source_id=node_id,
                target_id=n2,
                relation_type="DAYANIR",
            )
        )
        b_neighbors = loop.run_until_complete(
            dao.get_neighbors(AGENT_B, node_id=node_id)
        )
        assert b_neighbors == [], "Leakage via get_neighbors"
