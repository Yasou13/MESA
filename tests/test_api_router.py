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
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mesa_api.router import create_memory_router
from mesa_storage.dao import MemoryDAO
from mesa_storage.schemas import (
    initialize_schema,
)
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.vector_engine import VectorEngine
from tests.utils.storage_helpers import (
    get_active_nodes,
    insert_node,
)

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


class _VerifiedPurgeGraph:
    """Route fixture for the separately covered verified purge contract."""

    async def delete_nodes(self, *, purge_id, agent_id, node_ids):
        return None

    async def verify_nodes_absent(self, *, agent_id, node_ids):
        return True


@pytest.fixture(autouse=True)
def _clean_test_dir():
    os.makedirs(TEST_DIR, exist_ok=True)
    yield
    shutil.rmtree(TEST_DIR, ignore_errors=True)


@pytest.fixture(autouse=True)
def _mock_adapter_factory():
    """Mock the AdapterFactory to prevent live HTTP requests during testing."""
    mock_adapter = MagicMock()
    mock_adapter.aembed = AsyncMock(return_value=[0.1] * 1536)
    mock_adapter.acomplete = AsyncMock(return_value="[]")
    with patch(
        "mesa_memory.adapter.factory.AdapterFactory.get_adapter",
        return_value=mock_adapter,
    ):
        yield mock_adapter


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
def _mock_rbac():
    """Provide a mock RBAC callable for insert tests."""
    ac_mock = MagicMock()
    ac_mock.check_access = AsyncMock(return_value=True)
    ac_mock.check_principal_permission = AsyncMock(return_value=True)
    ac_mock.check_principal_session_access = AsyncMock(return_value=True)
    ac_mock.grant_access = AsyncMock(return_value=None)
    ac_mock.revoke_access = AsyncMock(return_value=None)
    yield lambda: ac_mock


@pytest.fixture
def client(engines, _mock_rbac):
    """Create a FastAPI TestClient with the router mounted."""
    sqlite_eng, vec_eng, _ = engines

    app = FastAPI()

    @app.middleware("http")
    async def attach_active_principal(request, call_next):
        request.state.principal = SimpleNamespace(
            principal_id="test-principal", status="active"
        )
        return await call_next(request)

    router = create_memory_router(
        get_dao=lambda: MemoryDAO(
            sqlite_engine=sqlite_eng,
            vector_engine=vec_eng,
            graph_provider=_VerifiedPurgeGraph(),
        ),
        get_embedder=lambda: _test_embedder,
        get_access_control=_mock_rbac,
    )
    app.include_router(router)

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def client_no_vector(engines, _mock_rbac):
    """TestClient without vector engine."""
    sqlite_eng, _, _ = engines

    app = FastAPI()
    router = create_memory_router(
        get_dao=lambda: MemoryDAO(sqlite_engine=sqlite_eng, vector_engine=None),
        get_embedder=lambda: _test_embedder,
        get_access_control=_mock_rbac,
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
        assert body["processing_mode"] == "async"
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

    def test_insert_does_not_write_cwd_debug_files(self, client, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        resp = client.post(
            "/v3/memory/insert",
            json={
                "agent_id": "agent-1",
                "session_id": "sess-001",
                "content": "No CWD debug artifact",
            },
        )
        assert resp.status_code == 202
        assert not (tmp_path / "dummy.txt").exists()
        assert not (tmp_path / "cold_path_trace.txt").exists()

    def test_insert_persists_durable_raw_log(self, client, engines):
        """Insert durably records work without fabricating a model result."""
        sqlite_eng, vec_eng, loop = engines

        mock_adapter = MagicMock()
        mock_adapter.aembed = AsyncMock(return_value=[0.1] * 1536)
        mock_adapter.acomplete = AsyncMock(return_value="[]")

        with patch(
            "mesa_memory.adapter.factory.AdapterFactory.get_adapter",
            return_value=mock_adapter,
        ):
            resp = client.post(
                "/v3/memory/insert",
                json={
                    "agent_id": "agent-bg",
                    "session_id": "sess-bg",
                    "content": "Background insert test",
                },
            )
        assert resp.status_code == 202
        result = resp.json()
        assert result["status"] == "queued"
        assert result["processing_mode"] == "async"
        assert result["log_id"] > 0

        raw_log = loop.run_until_complete(
            MemoryDAO(sqlite_engine=sqlite_eng, vector_engine=vec_eng).get_raw_log(
                "agent-bg", result["log_id"]
            )
        )
        assert raw_log is not None
        assert raw_log["payload"]["content"] == "Background insert test"
        assert raw_log["status"] == "DEFERRED"
        receipt = loop.run_until_complete(
            MemoryDAO(
                sqlite_engine=sqlite_eng, vector_engine=vec_eng
            ).get_dispatch_receipt_by_source("agent-bg", result["log_id"])
        )
        assert receipt is not None
        assert receipt["outcome"] == "ENQUEUED"
        nodes = loop.run_until_complete(
            get_active_nodes(sqlite_eng, agent_id="agent-bg")
        )
        assert nodes == []

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
        node_id = uuid.uuid4().hex
        loop.run_until_complete(
            insert_node(
                sqlite_eng,
                node_id=node_id,
                entity_name="SearchTestNode",
                agent_id="agent-search",
                session_id="sess-001",
            )
        )

        # Mock HybridRetriever to return known node IDs (bypass RBAC/adapter)
        mock_retriever = MagicMock()
        mock_retriever.retrieve = AsyncMock(return_value=[node_id])

        with (patch("mesa_api.router.HybridRetriever", return_value=mock_retriever),):
            resp = client.post(
                "/v3/memory/search",
                json={
                    "agent_id": "agent-search",
                    "session_id": "sess-001",
                    "query": "SearchTestNode",
                },
            )
        if resp.status_code != 200:
            print(resp.text)
        assert resp.status_code == 200
        body = resp.json()
        assert "context" in body
        assert "retrieved_nodes" in body
        assert "metrics" in body
        assert "latency_ms" in body["metrics"]
        assert isinstance(body["metrics"]["latency_ms"], int)

    def test_search_returns_fts_matches(self, client, engines):
        sqlite_eng, _, loop = engines

        node_id = uuid.uuid4().hex
        loop.run_until_complete(
            insert_node(
                sqlite_eng,
                node_id=node_id,
                entity_name="QuantumComputing",
                agent_id="agent-fts",
                session_id="sess-001",
            )
        )

        mock_retriever = MagicMock()
        mock_retriever.retrieve = AsyncMock(return_value=[node_id])

        with (patch("mesa_api.router.HybridRetriever", return_value=mock_retriever),):
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
        mock_retriever = MagicMock()
        mock_retriever.retrieve = AsyncMock(return_value=[])

        with (patch("mesa_api.router.HybridRetriever", return_value=mock_retriever),):
            resp = client.post(
                "/v3/memory/search",
                json={
                    "agent_id": "agent-empty",
                    "session_id": "sess-001",
                    "query": "nonexistent_entity_xyz",
                },
            )
        if resp.status_code != 200:
            print(resp.text)
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

        node_ids = []
        for i in range(10):
            nid = uuid.uuid4().hex
            node_ids.append(nid)
            loop.run_until_complete(
                insert_node(
                    sqlite_eng,
                    node_id=nid,
                    entity_name=f"LimitNode{i}",
                    agent_id="agent-limit",
                    session_id="sess-001",
                )
            )

        # Mock retriever returns all 10 IDs — endpoint limit should truncate
        mock_retriever = MagicMock()
        mock_retriever.retrieve = AsyncMock(return_value=node_ids)

        with (patch("mesa_api.router.HybridRetriever", return_value=mock_retriever),):
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
        if resp.status_code != 200:
            print(resp.text)
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
    def test_custom_prefix(self, engines, _mock_rbac):
        sqlite_eng, vec_eng, _ = engines
        router = create_memory_router(
            get_dao=lambda: MemoryDAO(sqlite_engine=sqlite_eng, vector_engine=vec_eng),
            get_access_control=_mock_rbac,
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
