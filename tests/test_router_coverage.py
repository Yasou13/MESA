# MESA v0.6.1 — Router Coverage Tests
"""
Unit tests targeting uncovered paths in mesa_api/router.py.

Covers:
  - _noop_embedder (line 92)
  - GET /v3/memory/status/{log_id} (lines 230-241)
  - POST /v3/session/start
  - GET /v3/session/{session_id}/context
  - POST /v3/session/{session_id}/end
  - Search error handling (PermissionError → 403, Exception → 500)
  - Purge error handling (Exception → 500)
"""

from __future__ import annotations

import asyncio
import os
import shutil
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mesa_api.router import _noop_embedder, create_memory_router
from mesa_storage.dao import MemoryDAO
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.vector_engine import VectorEngine

TEST_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    ".test_storage_tmp",
    "router_cov",
)


@pytest.fixture(autouse=True)
def _clean():
    os.makedirs(TEST_DIR, exist_ok=True)
    yield
    shutil.rmtree(TEST_DIR, ignore_errors=True)


@pytest.fixture
def engines():
    uid = uuid.uuid4().hex[:8]
    db_path = os.path.join(TEST_DIR, f"rcov_{uid}.db")
    vec_uri = os.path.join(TEST_DIR, f"rvec_{uid}.lance")

    sql = AsyncEngine(db_path, max_connections=2)
    vec = VectorEngine(vec_uri, max_workers=2)

    loop = asyncio.new_event_loop()
    loop.run_until_complete(sql.initialize())
    loop.run_until_complete(initialize_schema(sql))
    loop.run_until_complete(vec.initialize())

    yield sql, vec, loop

    loop.run_until_complete(sql.close())
    loop.run_until_complete(vec.close())
    loop.close()


@pytest.fixture
def _mock_rbac():
    """Provide a mock RBAC callable for insert tests."""
    ac_mock = MagicMock()
    ac_mock.check_access = AsyncMock(return_value=True)
    ac_mock.grant_access = AsyncMock(return_value=None)
    ac_mock.revoke_access = AsyncMock(return_value=None)
    yield lambda: ac_mock


@pytest.fixture
def client(engines, _mock_rbac):
    sql, vec, _ = engines
    app = FastAPI()
    router = create_memory_router(
        get_dao=lambda: MemoryDAO(sqlite_engine=sql, vector_engine=vec),
        get_access_control=_mock_rbac,
    )
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=True)


# ===================================================================
# _noop_embedder
# ===================================================================


class TestNoopEmbedder:
    def test_returns_8d_zero_vector(self):
        result = _noop_embedder("any text")
        assert len(result) == 8
        assert all(v == 0.0 for v in result)

    def test_type_is_list_of_float(self):
        result = _noop_embedder("test")
        assert isinstance(result, list)
        assert all(isinstance(v, float) for v in result)


# ===================================================================
# GET /v3/memory/status/{log_id}
# ===================================================================


class TestStatusEndpoint:
    def test_status_returns_404_for_nonexistent(self, client):
        resp = client.get("/v3/memory/status/999999", params={"agent_id": "agent-x"})
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"]

    def test_status_returns_result_for_existing(self, client, engines):
        sql, _, loop = engines
        dao = MemoryDAO(sqlite_engine=sql, vector_engine=engines[1])

        log_id = loop.run_until_complete(
            dao.insert_raw_log("agent-status", {"content": "hello"})
        )

        resp = client.get(
            f"/v3/memory/status/{log_id}", params={"agent_id": "agent-status"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["log_id"] == log_id
        assert "status" in body


# ===================================================================
# POST /v3/session/start
# ===================================================================


class TestSessionStart:
    def test_start_returns_session_id(self, client):
        resp = client.post(
            "/v3/memory/session/start",
            json={"agent_id": "agent-sess"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["agent_id"] == "agent-sess"
        assert body["session_id"].startswith("sess_")


# ===================================================================
# GET /v3/session/{session_id}/context
# ===================================================================


class TestSessionContext:
    def test_context_returns_empty_for_new_session(self, client):
        import uuid

        agent_id = f"agent-ctx-{uuid.uuid4().hex[:6]}"
        resp = client.get(
            "/v3/memory/session/test-session/context",
            params={"agent_id": agent_id},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["session_id"] == "test-session"
        assert body["agent_id"] == agent_id
        assert isinstance(body["recent_logs"], list)


# ===================================================================
# POST /v3/session/{session_id}/end
# ===================================================================


class TestSessionEnd:
    def test_end_session_returns_status(self, client):
        import uuid

        agent_id = f"agent-end-{uuid.uuid4().hex[:6]}"
        resp = client.post(
            "/v3/memory/session/test-session/end",
            json={"agent_id": agent_id},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ended"
        assert body["session_id"] == "test-session"


# ===================================================================
# Search error paths
# ===================================================================


class TestSearchErrors:
    def test_permission_error_returns_403(self, client):
        mock_retriever = MagicMock()
        mock_retriever.retrieve = AsyncMock(
            side_effect=PermissionError("Access denied")
        )

        with (patch("mesa_api.router.HybridRetriever", return_value=mock_retriever),):
            resp = client.post(
                "/v3/memory/search",
                json={
                    "agent_id": "agent-err",
                    "session_id": "s1",
                    "query": "test",
                },
            )
        assert resp.status_code == 403

    def test_general_exception_returns_500(self, client):
        mock_retriever = MagicMock()
        mock_retriever.retrieve = AsyncMock(side_effect=RuntimeError("DB down"))

        with (patch("mesa_api.router.HybridRetriever", return_value=mock_retriever),):
            resp = client.post(
                "/v3/memory/search",
                json={
                    "agent_id": "agent-err",
                    "session_id": "s1",
                    "query": "test",
                },
            )
        assert resp.status_code == 500

    def test_search_dict_result_handling(self, client, engines):
        """Test search when retriever returns dict instead of list."""
        sql, vec, loop = engines
        node_id = uuid.uuid4().hex

        agent_id = f"agent-dict-{uuid.uuid4().hex[:6]}"
        loop.run_until_complete(
            MemoryDAO(sqlite_engine=sql, vector_engine=vec).insert_memory(
                agent_id,
                entity_name="DictNode",
                content="data",
                embedding=[0.1] * 8,
                node_id=node_id,
            )
        )

        mock_retriever = MagicMock()
        mock_retriever.retrieve = AsyncMock(return_value={"cmb_ids": [node_id]})

        with (patch("mesa_api.router.HybridRetriever", return_value=mock_retriever),):
            resp = client.post(
                "/v3/memory/search",
                json={
                    "agent_id": agent_id,
                    "session_id": "s1",
                    "query": "test",
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["retrieved_nodes"]) == 1

    def test_search_hydration_missing_node(self, client):
        """Retriever returns a node_id that doesn't exist in SQLite."""
        mock_retriever = MagicMock()
        mock_retriever.retrieve = AsyncMock(return_value=["phantom-id-xyz"])

        import uuid

        agent_id = f"agent-phantom-{uuid.uuid4().hex[:6]}"
        with (patch("mesa_api.router.HybridRetriever", return_value=mock_retriever),):
            resp = client.post(
                "/v3/memory/search",
                json={
                    "agent_id": agent_id,
                    "session_id": "s1",
                    "query": "test",
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        # Node is returned with minimal metadata (hydration fallback)
        assert body["retrieved_nodes"][0]["node_id"] == "phantom-id-xyz"
        assert body["retrieved_nodes"][0]["source"] == "hybrid"

    def test_search_timeout_returns_504(self, client):
        mock_retriever = MagicMock()
        mock_retriever.retrieve = AsyncMock(side_effect=asyncio.TimeoutError)

        import uuid

        agent_id = f"agent-time-{uuid.uuid4().hex[:6]}"
        with (patch("mesa_api.router.HybridRetriever", return_value=mock_retriever),):
            resp = client.post(
                "/v3/memory/search",
                json={
                    "agent_id": agent_id,
                    "session_id": "s1",
                    "query": "test",
                },
            )
        assert resp.status_code == 504


class TestPurgeErrors:
    def test_purge_permission_error_returns_403(self, client):
        ac_mock = MagicMock()
        ac_mock.check_access = AsyncMock(return_value=False)
        app = FastAPI()
        router = create_memory_router(
            get_dao=lambda: MagicMock(),
            get_access_control=lambda: ac_mock,
        )
        app.include_router(router)
        cli = TestClient(app, raise_server_exceptions=False)

        resp = cli.request(
            "DELETE",
            "/v3/memory/purge",
            json={"agent_id": "agent-err", "scope": "session", "scope_id": "s1"},
        )
        assert resp.status_code == 403

    def test_purge_exception_returns_500(self, client):
        ac_mock = MagicMock()
        ac_mock.check_access = AsyncMock(return_value=True)
        dao_mock = MagicMock()
        dao_mock.purge_memory = AsyncMock(side_effect=RuntimeError("DAO Error"))

        app = FastAPI()
        router = create_memory_router(
            get_dao=lambda: dao_mock,
            get_access_control=lambda: ac_mock,
        )
        app.include_router(router)
        cli = TestClient(app, raise_server_exceptions=False)

        resp = cli.request(
            "DELETE",
            "/v3/memory/purge",
            json={"agent_id": "agent-err", "scope": "session", "scope_id": "s1"},
        )
        assert resp.status_code == 500


class TestSessionContextErrors:
    def test_context_permission_error_returns_403(self, client):
        ac_mock = MagicMock()
        ac_mock.check_access = AsyncMock(return_value=False)
        app = FastAPI()
        router = create_memory_router(
            get_dao=lambda: MagicMock(),
            get_access_control=lambda: ac_mock,
        )
        app.include_router(router)
        cli = TestClient(app, raise_server_exceptions=False)

        resp = cli.get(
            "/v3/memory/session/s1/context", params={"agent_id": "agent-err"}
        )
        assert resp.status_code == 403

    def test_context_exception_returns_500(self, client):
        ac_mock = MagicMock()
        ac_mock.check_access = AsyncMock(return_value=True)
        dao_mock = MagicMock()
        dao_mock.get_recent_logs = AsyncMock(side_effect=RuntimeError("DAO Error"))

        app = FastAPI()
        router = create_memory_router(
            get_dao=lambda: dao_mock,
            get_access_control=lambda: ac_mock,
        )
        app.include_router(router)
        cli = TestClient(app, raise_server_exceptions=False)

        resp = cli.get(
            "/v3/memory/session/s1/context", params={"agent_id": "agent-err"}
        )
        assert resp.status_code == 500


class TestSessionEndErrors:
    def test_end_permission_error_returns_403(self, client):
        ac_mock = MagicMock()
        ac_mock.check_access = AsyncMock(return_value=False)
        app = FastAPI()
        router = create_memory_router(
            get_dao=lambda: MagicMock(),
            get_access_control=lambda: ac_mock,
        )
        app.include_router(router)
        cli = TestClient(app, raise_server_exceptions=False)

        resp = cli.post("/v3/memory/session/s1/end", json={"agent_id": "agent-err"})
        assert resp.status_code == 403

    def test_end_exception_returns_500(self, client):
        ac_mock = MagicMock()
        ac_mock.check_access = AsyncMock(side_effect=RuntimeError("Unexpected error"))
        app = FastAPI()
        router = create_memory_router(
            get_dao=lambda: MagicMock(),
            get_access_control=lambda: ac_mock,
        )
        app.include_router(router)
        cli = TestClient(app, raise_server_exceptions=False)

        resp = cli.post("/v3/memory/session/s1/end", json={"agent_id": "agent-err"})
        assert resp.status_code == 500


class TestInsertErrors:
    def test_insert_permission_error_returns_403(self, client):
        ac_mock = MagicMock()
        ac_mock.check_access = AsyncMock(return_value=False)
        app = FastAPI()
        router = create_memory_router(
            get_dao=lambda: MagicMock(),
            get_access_control=lambda: ac_mock,
        )
        app.include_router(router)
        cli = TestClient(app, raise_server_exceptions=False)

        resp = cli.post(
            "/v3/memory/insert",
            json={"agent_id": "agent-err", "session_id": "s1", "content": "text"},
        )
        assert resp.status_code == 403
