# MESA v0.3.0 — Phase 1: API Router Test Suite
"""
Tests for the v3 memory API router: insert (fire-and-forget), search
(synchronous retrieval with metrics), and purge (soft-delete ONLY).

Uses FastAPI's TestClient for synchronous endpoint testing with
real AsyncEngine and VectorEngine instances — no mocks.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mesa_api.router import create_memory_router
from mesa_storage.dao import MemoryDAO
from mesa_storage.schemas import (
    get_active_nodes,
    initialize_schema,
    insert_node,
)
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.vector_engine import VectorEngine

TEST_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    ".test_storage_tmp",
    "router",
)

VEC_8D = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]


def _test_embedder(text: str) -> list[float]:
    """Deterministic 8-dim test embedder."""
    seed = sum(ord(c) for c in text) % 256
    return [float(seed) / 256.0] * 8


@pytest.fixture(autouse=True)
def _clean_test_dir():
    os.makedirs(TEST_DIR, exist_ok=True)
    yield
    shutil.rmtree(TEST_DIR, ignore_errors=True)


@pytest.fixture
def engines():
    """Create real engines and return them along with a configured TestClient."""
    test_id = uuid.uuid4().hex[:8]
    db_path = os.path.join(TEST_DIR, f"router_{test_id}.db")
    vec_uri = os.path.join(TEST_DIR, f"vec_{test_id}.lance")

    sqlite_eng = AsyncEngine(db_path, max_connections=4)
    vec_eng = VectorEngine(vec_uri, max_workers=2)

    loop = asyncio.new_event_loop()
    loop.run_until_complete(sqlite_eng.initialize())
    loop.run_until_complete(initialize_schema(sqlite_eng))
    loop.run_until_complete(vec_eng.initialize())

    yield sqlite_eng, vec_eng, loop

    loop.run_until_complete(sqlite_eng.close())
    loop.run_until_complete(vec_eng.close())
    loop.close()


@pytest.fixture
def client(engines):
    """Create a FastAPI TestClient with the router mounted."""
    sqlite_eng, vec_eng, _ = engines

    app = FastAPI()
    router = create_memory_router(
        get_dao=lambda: MemoryDAO(sqlite_engine=sqlite_eng, vector_engine=vec_eng),
        get_embedder=lambda: _test_embedder,
    )
    app.include_router(router)

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def client_no_vector(engines):
    """TestClient without vector engine."""
    sqlite_eng, _, _ = engines

    app = FastAPI()
    router = create_memory_router(
        get_dao=lambda: MemoryDAO(sqlite_engine=sqlite_eng, vector_engine=None),
        get_embedder=lambda: _test_embedder,
    )
    app.include_router(router)

    return TestClient(app, raise_server_exceptions=False)


# ===================================================================
# POST /v3/memory/insert
# ===================================================================


class TestInsertEndpoint:
    def test_insert_returns_202_queued(self, client):
        resp = client.post(
            "/v3/memory/insert",
            json={
                "agent_id": "agent-1",
                "session_id": "sess-001",
                "content": "Test memory content.",
            },
        )
        assert resp.status_code == 202
        body = resp.json()
        assert body["status"] == "queued"
        assert "log_id" in body
        assert isinstance(body["log_id"], int)

    def test_insert_returns_unique_ids(self, client):
        ids = set()
        for _ in range(10):
            resp = client.post(
                "/v3/memory/insert",
                json={
                    "agent_id": "agent-1",
                    "session_id": "sess-001",
                    "content": "Content",
                },
            )
            ids.add(resp.json()["log_id"])
        assert len(ids) == 10

    def test_insert_rejects_empty_content(self, client):
        resp = client.post(
            "/v3/memory/insert",
            json={
                "agent_id": "agent-1",
                "session_id": "sess-001",
                "content": "",
            },
        )
        assert resp.status_code == 422

    def test_insert_rejects_reserved_agent_id(self, client):
        resp = client.post(
            "/v3/memory/insert",
            json={
                "agent_id": "__unset__",
                "session_id": "sess-001",
                "content": "Content",
            },
        )
        assert resp.status_code == 422

    def test_insert_rejects_missing_fields(self, client):
        resp = client.post(
            "/v3/memory/insert",
            json={"agent_id": "agent-1"},
        )
        assert resp.status_code == 422

    def test_insert_with_metadata(self, client):
        resp = client.post(
            "/v3/memory/insert",
            json={
                "agent_id": "agent-1",
                "session_id": "sess-001",
                "content": "Content with metadata",
                "metadata": {"source": "test", "priority": 5},
            },
        )
        assert resp.status_code == 202

    def test_insert_persists_node_in_background(self, client, engines):
        """After insert, the background task writes to SQLite."""
        sqlite_eng, vec_eng, loop = engines

        resp = client.post(
            "/v3/memory/insert",
            json={
                "agent_id": "agent-bg",
                "session_id": "sess-bg",
                "content": "Background insert test",
            },
        )
        assert resp.status_code == 202
        assert resp.json()["log_id"] > 0

        # Background task runs during TestClient scope — verify the node
        nodes = loop.run_until_complete(
            get_active_nodes(sqlite_eng, agent_id="agent-bg")
        )
        found = [n for n in nodes if n["agent_id"] == "agent-bg"]
        assert len(found) >= 1

    def test_insert_without_vector_engine(self, client_no_vector):
        resp = client_no_vector.post(
            "/v3/memory/insert",
            json={
                "agent_id": "agent-1",
                "session_id": "sess-001",
                "content": "No vector engine",
            },
        )
        assert resp.status_code == 202


# ===================================================================
# POST /v3/memory/search
# ===================================================================


class TestSearchEndpoint:
    def test_search_returns_structure(self, client, engines):
        sqlite_eng, _, loop = engines

        # Seed a node for search
        loop.run_until_complete(
            insert_node(
                sqlite_eng,
                node_id=uuid.uuid4().hex,
                entity_name="SearchTestNode",
                agent_id="agent-search",
                session_id="sess-001",
            )
        )

        resp = client.post(
            "/v3/memory/search",
            json={
                "agent_id": "agent-search",
                "session_id": "sess-001",
                "query": "SearchTestNode",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "context" in body
        assert "retrieved_nodes" in body
        assert "metrics" in body
        assert "latency_ms" in body["metrics"]
        assert isinstance(body["metrics"]["latency_ms"], int)

    def test_search_returns_fts_matches(self, client, engines):
        sqlite_eng, _, loop = engines

        loop.run_until_complete(
            insert_node(
                sqlite_eng,
                node_id=uuid.uuid4().hex,
                entity_name="QuantumComputing",
                agent_id="agent-fts",
                session_id="sess-001",
            )
        )

        resp = client.post(
            "/v3/memory/search",
            json={
                "agent_id": "agent-fts",
                "session_id": "sess-001",
                "query": "QuantumComputing",
                "limit": 5,
            },
        )
        body = resp.json()
        assert len(body["retrieved_nodes"]) >= 1
        assert body["retrieved_nodes"][0]["entity_name"] == "QuantumComputing"

    def test_search_empty_results(self, client):
        resp = client.post(
            "/v3/memory/search",
            json={
                "agent_id": "agent-empty",
                "session_id": "sess-001",
                "query": "nonexistent_entity_xyz",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["retrieved_nodes"] == []
        assert body["context"] == ""

    def test_search_rejects_empty_query(self, client):
        resp = client.post(
            "/v3/memory/search",
            json={
                "agent_id": "agent-1",
                "session_id": "sess-001",
                "query": "",
            },
        )
        assert resp.status_code == 422

    def test_search_rejects_limit_above_max(self, client):
        resp = client.post(
            "/v3/memory/search",
            json={
                "agent_id": "agent-1",
                "session_id": "sess-001",
                "query": "test",
                "limit": 51,
            },
        )
        assert resp.status_code == 422

    def test_search_respects_limit(self, client, engines):
        sqlite_eng, _, loop = engines

        for i in range(10):
            loop.run_until_complete(
                insert_node(
                    sqlite_eng,
                    node_id=uuid.uuid4().hex,
                    entity_name=f"LimitNode{i}",
                    agent_id="agent-limit",
                    session_id="sess-001",
                )
            )

        resp = client.post(
            "/v3/memory/search",
            json={
                "agent_id": "agent-limit",
                "session_id": "sess-001",
                "query": "LimitNode*",
                "limit": 3,
            },
        )
        body = resp.json()
        assert len(body["retrieved_nodes"]) <= 3


# ===================================================================
# DELETE /v3/memory/purge
# ===================================================================


class TestPurgeEndpoint:
    def test_purge_agent_scope(self, client, engines):
        sqlite_eng, _, loop = engines

        # Seed nodes
        for i in range(5):
            loop.run_until_complete(
                insert_node(
                    sqlite_eng,
                    node_id=uuid.uuid4().hex,
                    entity_name=f"PurgeNode{i}",
                    agent_id="agent-purge",
                    session_id="sess-001",
                )
            )

        resp = client.request(
            "DELETE",
            "/v3/memory/purge",
            json={
                "agent_id": "agent-purge",
                "scope": "agent",
                "scope_id": "agent-purge",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "purged"
        assert body["deleted_records_count"] >= 5

        # Verify nodes are soft-deleted (not physically removed)
        nodes = loop.run_until_complete(
            get_active_nodes(sqlite_eng, agent_id="agent-purge")
        )
        assert len(nodes) == 0

    def test_purge_session_scope(self, client, engines):
        sqlite_eng, _, loop = engines

        # Seed nodes in two sessions
        for i in range(3):
            loop.run_until_complete(
                insert_node(
                    sqlite_eng,
                    node_id=uuid.uuid4().hex,
                    entity_name=f"SessA_{i}",
                    agent_id="agent-scope",
                    session_id="sess-A",
                )
            )
        for i in range(3):
            loop.run_until_complete(
                insert_node(
                    sqlite_eng,
                    node_id=uuid.uuid4().hex,
                    entity_name=f"SessB_{i}",
                    agent_id="agent-scope",
                    session_id="sess-B",
                )
            )

        resp = client.request(
            "DELETE",
            "/v3/memory/purge",
            json={
                "agent_id": "agent-scope",
                "scope": "session",
                "scope_id": "sess-A",
            },
        )
        body = resp.json()
        assert body["status"] == "purged"
        assert body["deleted_records_count"] >= 3

        # sess-B should still be active
        nodes = loop.run_until_complete(
            get_active_nodes(sqlite_eng, agent_id="agent-scope")
        )
        assert len(nodes) == 3
        assert all(n["session_id"] == "sess-B" for n in nodes)

    def test_purge_does_not_physically_delete(self, client, engines):
        """CRITICAL: Purge must NOT remove rows — only set invalid_at."""
        sqlite_eng, _, loop = engines

        nid = uuid.uuid4().hex
        loop.run_until_complete(
            insert_node(
                sqlite_eng,
                node_id=nid,
                entity_name="NoHardDelete",
                agent_id="agent-safe",
                session_id="sess-001",
            )
        )

        client.request(
            "DELETE",
            "/v3/memory/purge",
            json={
                "agent_id": "agent-safe",
                "scope": "agent",
                "scope_id": "agent-safe",
            },
        )

        # Row should still exist with invalid_at set
        async def _check():
            async with sqlite_eng.connection() as db:
                async with db.execute(
                    "SELECT id, invalid_at FROM nodes WHERE id = ?",
                    (nid,),
                ) as cur:
                    row = await cur.fetchone()
                    return dict(row) if row else None

        result = loop.run_until_complete(_check())
        assert result is not None, "Row was physically deleted — VACUUM leak!"
        assert result["invalid_at"] is not None

    def test_purge_rejects_invalid_scope(self, client):
        resp = client.request(
            "DELETE",
            "/v3/memory/purge",
            json={
                "agent_id": "agent-1",
                "scope": "global",
                "scope_id": "anything",
            },
        )
        assert resp.status_code == 422

    def test_purge_agent_scope_id_mismatch(self, client):
        resp = client.request(
            "DELETE",
            "/v3/memory/purge",
            json={
                "agent_id": "agent-1",
                "scope": "agent",
                "scope_id": "agent-2",
            },
        )
        assert resp.status_code == 422


# ===================================================================
# Router factory
# ===================================================================


class TestRouterFactory:
    def test_custom_prefix(self, engines):
        sqlite_eng, vec_eng, _ = engines
        router = create_memory_router(
            get_dao=lambda: MemoryDAO(sqlite_engine=sqlite_eng, vector_engine=vec_eng),
            prefix="/custom/api",
        )
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        resp = client.post(
            "/custom/api/insert",
            json={
                "agent_id": "agent-1",
                "session_id": "sess-001",
                "content": "Custom prefix test",
            },
        )
        assert resp.status_code == 202

    def test_custom_tags(self, engines):
        sqlite_eng, vec_eng, _ = engines
        router = create_memory_router(
            get_dao=lambda: MemoryDAO(sqlite_engine=sqlite_eng, vector_engine=vec_eng),
            tags=["custom-tag"],
        )
        assert router.tags == ["custom-tag"]
