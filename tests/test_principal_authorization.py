from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mesa_api.router import create_memory_router
from mesa_memory.security.rbac import AccessControl


def test_session_start_denies_unmapped_principal_without_self_grant():
    """A request agent_id is a target, never an authorization grant."""
    access_control = MagicMock()
    access_control.check_principal_permission = AsyncMock(return_value=False)
    access_control.grant_access = AsyncMock()

    app = FastAPI()

    @app.middleware("http")
    async def attach_unmapped_principal(request, call_next):
        request.state.principal = SimpleNamespace(
            principal_id="principal-a",
            principal_type="USER",
            status="active",
        )
        return await call_next(request)

    app.include_router(
        create_memory_router(
            get_dao=lambda: MagicMock(),  # type: ignore[return-value]
            get_embedder=lambda: [0.0] * 8,  # type: ignore[return-value]
            get_access_control=lambda: access_control,  # type: ignore[return-value]
        )
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post("/v3/memory/session/start", json={"agent_id": "agent-b"})

    assert response.status_code == 403
    access_control.check_principal_permission.assert_awaited_once_with(
        "principal-a", "agent-b", "SESSION_CREATE"
    )
    access_control.grant_access.assert_not_awaited()


@pytest.mark.asyncio
async def test_principal_permission_requires_explicit_server_mapping(tmp_path):
    access_control = AccessControl(policy_path=str(tmp_path / "rbac.db"))
    await access_control.initialize()

    assert (
        await access_control.check_principal_permission(
            "principal-a", "agent-b", "SESSION_CREATE"
        )
        is False
    )

    await access_control.grant_principal_permission(
        "principal-a", "agent-b", "SESSION_CREATE"
    )

    assert (
        await access_control.check_principal_permission(
            "principal-a", "agent-b", "SESSION_CREATE"
        )
        is True
    )


def test_session_start_allows_mapped_active_principal():
    access_control = MagicMock()
    access_control.check_principal_permission = AsyncMock(return_value=True)
    access_control.grant_access = AsyncMock()
    access_control.grant_principal_session_access = AsyncMock()

    app = FastAPI()

    @app.middleware("http")
    async def attach_mapped_principal(request, call_next):
        request.state.principal = SimpleNamespace(
            principal_id="principal-a",
            principal_type="USER",
            status="active",
        )
        return await call_next(request)

    app.include_router(
        create_memory_router(
            get_dao=lambda: MagicMock(),  # type: ignore[return-value]
            get_embedder=lambda: [0.0] * 8,  # type: ignore[return-value]
            get_access_control=lambda: access_control,  # type: ignore[return-value]
        )
    )
    response = TestClient(app, raise_server_exceptions=False).post(
        "/v3/memory/session/start", json={"agent_id": "agent-a"}
    )

    assert response.status_code == 200
    access_control.check_principal_permission.assert_awaited_once_with(
        "principal-a", "agent-a", "SESSION_CREATE"
    )
    access_control.grant_access.assert_awaited_once()
    access_control.grant_principal_session_access.assert_awaited_once()


def test_session_start_rejects_inactive_principal():
    access_control = MagicMock()
    access_control.check_principal_permission = AsyncMock()
    access_control.grant_access = AsyncMock()

    app = FastAPI()

    @app.middleware("http")
    async def attach_inactive_principal(request, call_next):
        request.state.principal = SimpleNamespace(
            principal_id="principal-disabled",
            principal_type="USER",
            status="disabled",
        )
        return await call_next(request)

    app.include_router(
        create_memory_router(
            get_dao=lambda: MagicMock(),  # type: ignore[return-value]
            get_embedder=lambda: [0.0] * 8,  # type: ignore[return-value]
            get_access_control=lambda: access_control,  # type: ignore[return-value]
        )
    )
    response = TestClient(app, raise_server_exceptions=False).post(
        "/v3/memory/session/start", json={"agent_id": "agent-a"}
    )

    assert response.status_code == 401
    access_control.check_principal_permission.assert_not_awaited()
    access_control.grant_access.assert_not_awaited()


@pytest.mark.asyncio
async def test_read_only_principal_mapping_cannot_create_session(tmp_path):
    access_control = AccessControl(policy_path=str(tmp_path / "rbac.db"))
    await access_control.initialize()
    await access_control.grant_principal_permission("principal-a", "agent-a", "READ")

    assert (
        await access_control.check_principal_permission(
            "principal-a", "agent-a", "SESSION_CREATE"
        )
        is False
    )
