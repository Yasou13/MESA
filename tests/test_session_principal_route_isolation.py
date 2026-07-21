"""WAVE-001-V real FastAPI authentication and principal/session isolation."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from mesa_api.router import create_memory_router
from mesa_memory.api import server
from mesa_memory.security.rbac import AccessControl


@pytest.mark.asyncio
async def test_authenticated_session_routes_bind_principals_server_side(tmp_path):
    """Foreign and client-forged agent/session access is denied before DAO I/O."""
    policy = AccessControl(policy_path=str(tmp_path / "rbac.db"))
    await policy.initialize()
    await policy.grant_principal_permission("principal-a", "agent-a", "SESSION_CREATE")
    await policy.grant_principal_permission("principal-b", "agent-b", "SESSION_CREATE")
    dao = SimpleNamespace(
        get_recent_logs=AsyncMock(return_value=[]),
        purge_memory=AsyncMock(return_value=0),
    )
    app = FastAPI()
    app.include_router(
        create_memory_router(get_dao=lambda: dao, get_access_control=lambda: policy),  # type: ignore[return-value]
        dependencies=[Depends(server.get_api_key)],
    )

    previous = (
        server._MESA_API_KEY,
        server._MESA_PRINCIPAL_ID,
        server._MESA_PRINCIPAL_TYPE,
        server._MESA_PRINCIPAL_STATUS,
    )

    def principal(principal_id: str, status: str = "active") -> None:
        server._MESA_API_KEY = "wave001-route-key"
        server._MESA_PRINCIPAL_ID = principal_id
        server._MESA_PRINCIPAL_TYPE = "USER"
        server._MESA_PRINCIPAL_STATUS = status

    headers = {"X-API-Key": "wave001-route-key"}
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            assert (
                client.get(
                    "/v3/memory/session/missing/context?agent_id=agent-a"
                ).status_code
                == 401
            )
            assert (
                client.get(
                    "/v3/memory/session/missing/context?agent_id=agent-a",
                    headers={"X-API-Key": "invalid-key"},
                ).status_code
                == 401
            )
            principal("principal-read")
            assert (
                client.post(
                    "/v3/memory/session/start",
                    headers=headers,
                    json={"agent_id": "agent-a"},
                ).status_code
                == 403
            )
            principal("principal-unmapped")
            assert (
                client.post(
                    "/v3/memory/session/start",
                    headers=headers,
                    json={"agent_id": "agent-a"},
                ).status_code
                == 403
            )
            principal("principal-a", status="inactive")
            assert (
                client.post(
                    "/v3/memory/session/start",
                    headers=headers,
                    json={"agent_id": "agent-a"},
                ).status_code
                == 401
            )
            principal("principal-a")
            started_a = client.post(
                "/v3/memory/session/start",
                headers=headers,
                json={"agent_id": "agent-a"},
            )
            assert started_a.status_code == 200
            session_a = started_a.json()["session_id"]
            reopened = AccessControl(policy_path=policy.policy_path)
            await reopened.initialize()
            assert await reopened.check_principal_session_access(
                "principal-a", "agent-a", session_a, "WRITE"
            )
            await reopened.close()

            principal("principal-b")
            started_b = client.post(
                "/v3/memory/session/start",
                headers=headers,
                json={"agent_id": "agent-b"},
            )
            assert started_b.status_code == 200
            session_b = started_b.json()["session_id"]

            principal("principal-a")
            assert (
                client.get(
                    f"/v3/memory/session/{session_a}/context?agent_id=agent-a",
                    headers=headers,
                ).status_code
                == 200
            )
            foreign_context = client.get(
                f"/v3/memory/session/{session_b}/context?agent_id=agent-a",
                headers=headers,
            )
            assert foreign_context.status_code == 403
            assert foreign_context.json()["detail"] == "Session access denied"
            forged_agent = client.get(
                f"/v3/memory/session/{session_a}/context?agent_id=agent-b",
                headers=headers,
            )
            assert forged_agent.status_code == 403
            assert forged_agent.json()["detail"] == "Session access denied"
            foreign_end = client.post(
                f"/v3/memory/session/{session_b}/end",
                headers=headers,
                json={"agent_id": "agent-a"},
            )
            assert foreign_end.status_code == 403
            assert foreign_end.json()["detail"] == "Session access denied"

            await policy.grant_principal_permission("principal-a", "agent-a", "PURGE")
            own_purge = client.request(
                "DELETE",
                "/v3/memory/purge",
                headers=headers,
                json={"agent_id": "agent-a", "scope": "session", "scope_id": session_a},
            )
            assert own_purge.status_code == 200
            foreign_purge = client.request(
                "DELETE",
                "/v3/memory/purge",
                headers=headers,
                json={"agent_id": "agent-a", "scope": "session", "scope_id": session_b},
            )
            assert foreign_purge.status_code == 403
            assert foreign_purge.json()["detail"] == "Session access denied"

            await policy.grant_principal_session_access(
                "principal-read", "agent-a", session_a, "READ"
            )
            principal("principal-read")
            assert (
                client.get(
                    f"/v3/memory/session/{session_a}/context?agent_id=agent-a",
                    headers=headers,
                ).status_code
                == 200
            )
            assert (
                client.post(
                    f"/v3/memory/session/{session_a}/end",
                    headers=headers,
                    json={"agent_id": "agent-a"},
                ).status_code
                == 403
            )

            principal("principal-a", status="inactive")
            assert (
                client.get(
                    f"/v3/memory/session/{session_a}/context?agent_id=agent-a",
                    headers=headers,
                ).status_code
                == 401
            )
            principal("principal-unmapped")
            assert (
                client.get(
                    f"/v3/memory/session/{session_a}/context?agent_id=agent-a",
                    headers=headers,
                ).status_code
                == 403
            )
    finally:
        (
            server._MESA_API_KEY,
            server._MESA_PRINCIPAL_ID,
            server._MESA_PRINCIPAL_TYPE,
            server._MESA_PRINCIPAL_STATUS,
        ) = previous
        await policy.close()
