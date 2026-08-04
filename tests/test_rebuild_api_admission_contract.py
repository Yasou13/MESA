"""Storage-root rebuild admission applies to every public API generation."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import Depends, FastAPI, Request

from mesa_api.router import create_memory_router


@pytest.mark.asyncio
async def test_v3_rebuild_maintenance_gates_mutations_but_keeps_reads_open() -> None:
    dao = MagicMock()
    dao.rebuild_admission.is_pending = AsyncMock(return_value=True)
    dao.get_recent_logs = AsyncMock(return_value=[])
    dao.admit_raw_log = AsyncMock(return_value={"log_id": 1, "deduplicated": False})
    dao.purge_memory = AsyncMock(return_value=0)
    dao.request_session_finalization = AsyncMock(return_value={"state": "COMPLETED"})

    access = MagicMock()
    access.check_principal_permission = AsyncMock(return_value=True)
    access.check_principal_session_access = AsyncMock(return_value=True)
    access.check_access = AsyncMock(return_value=True)
    access.grant_access = AsyncMock()
    access.grant_principal_session_access = AsyncMock()

    async def attach_principal(request: Request) -> None:
        request.state.principal = SimpleNamespace(
            principal_id="principal-a", status="active"
        )

    async def get_dao():  # type: ignore[no-untyped-def]
        return dao

    app = FastAPI(dependencies=[Depends(attach_principal)])
    app.include_router(
        create_memory_router(
            get_dao=get_dao,
            get_access_control=lambda: access,  # type: ignore[arg-type]
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        responses = [
            await client.post(
                "/v3/memory/insert",
                json={
                    "agent_id": "agent-a",
                    "session_id": "session-a",
                    "content": "blocked during rebuild",
                },
            ),
            await client.request(
                "DELETE",
                "/v3/memory/purge",
                json={
                    "agent_id": "agent-a",
                    "scope": "agent",
                    "scope_id": "agent-a",
                },
            ),
            await client.post("/v3/memory/session/start", json={"agent_id": "agent-a"}),
            await client.post(
                "/v3/memory/session/session-a/end", json={"agent_id": "agent-a"}
            ),
        ]
        readable = await client.get(
            "/v3/memory/session/session-a/context", params={"agent_id": "agent-a"}
        )

    for response in responses:
        assert response.status_code == 503
        assert response.json() == {"detail": "maintenance_pending"}
        assert response.headers["Retry-After"] == "5"
    assert readable.status_code == 200
    dao.admit_raw_log.assert_not_awaited()
    dao.purge_memory.assert_not_awaited()
    dao.request_session_finalization.assert_not_awaited()
    access.grant_access.assert_not_awaited()
