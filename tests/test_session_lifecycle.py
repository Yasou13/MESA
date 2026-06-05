import asyncio
import os
import shutil
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mesa_api.router import create_memory_router
from mesa_storage.dao import MemoryDAO
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.vector_engine import VectorEngine

TEST_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    ".test_storage_tmp",
    "session_lifecycle",
)


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
    db_path = os.path.join(TEST_DIR, f"lifecycle_{test_id}.db")
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
    """Patch the RBAC singleton so insert tests get WRITE access."""
    ac_mock = MagicMock()
    ac_mock.check_access = AsyncMock(return_value=True)
    with patch("mesa_api.router._get_access_control", return_value=ac_mock):
        yield ac_mock


@pytest.fixture
def client(engines, _mock_rbac):
    """Create a FastAPI TestClient with the router mounted."""
    sqlite_eng, vec_eng, _ = engines

    app = FastAPI()
    router = create_memory_router(
        get_dao=lambda: MemoryDAO(sqlite_engine=sqlite_eng, vector_engine=vec_eng),
        get_embedder=lambda: _test_embedder,
    )
    app.include_router(router)

    return TestClient(app, raise_server_exceptions=False)


# ===================================================================
# Scenarios
# ===================================================================


class TestSessionLifecycle:
    def test_lifecycle(self, client):
        """Scenario 1: Lifecycle"""
        agent_id = "agent_lifecycle_test"

        # 1. Start a session
        start_resp = client.post(
            "/v3/memory/session/start",
            json={"agent_id": agent_id},
        )
        assert start_resp.status_code == 200
        start_data = start_resp.json()
        assert "session_id" in start_data
        assert start_data["status"] == "started"
        session_id = start_data["session_id"]

        # 2. Insert two memories
        for content in ["Memory one for session", "Memory two for session"]:
            client.post(
                "/v3/memory/insert",
                json={
                    "agent_id": agent_id,
                    "session_id": session_id,
                    "content": content,
                },
            )

        # 3. Retrieve context
        # Since nodes are not generated instantly (it's done in background processing in actual running app),
        # but the insert endpoint creates `raw_logs`. So we should be able to see it in `recent_logs`.
        context_resp = client.get(
            f"/v3/memory/session/{session_id}/context?agent_id={agent_id}"
        )
        assert context_resp.status_code == 200
        context_data = context_resp.json()

        # Assert both memories are returned in recent logs
        recent_logs = context_data.get("recent_logs", [])
        assert len(recent_logs) == 2
        contents = [log["content"] for log in recent_logs]
        assert "Memory one for session" in contents
        assert "Memory two for session" in contents

        # 4. End the session
        end_resp = client.post(
            f"/v3/memory/session/{session_id}/end",
            json={"agent_id": agent_id},
        )
        assert end_resp.status_code == 200
        assert end_resp.json()["status"] == "ended"

    def test_isolation(self, client):
        """Scenario 2: Isolation"""
        agent_id = "agent_isolation_test"

        # 1. Create two separate sessions
        resp_a = client.post("/v3/memory/session/start", json={"agent_id": agent_id})
        session_a = resp_a.json()["session_id"]

        resp_b = client.post("/v3/memory/session/start", json={"agent_id": agent_id})
        session_b = resp_b.json()["session_id"]

        # 2. Insert data into session A
        client.post(
            "/v3/memory/insert",
            json={
                "agent_id": agent_id,
                "session_id": session_a,
                "content": "Secret data for A",
            },
        )

        # 3. Query session B's context
        context_resp = client.get(
            f"/v3/memory/session/{session_b}/context?agent_id={agent_id}"
        )
        assert context_resp.status_code == 200
        context_data = context_resp.json()

        # Assert it returns an EMPTY context, proving cross-session data leakage does not occur
        assert len(context_data.get("recent_logs", [])) == 0
        assert context_data.get("context", "") == ""
