"""WAVE-001-V async SDK uses the API authentication header contract."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import Depends, FastAPI

from mesa_api.router import create_memory_router
from mesa_api.schemas import MemoryPurgeRequest
from mesa_client.client import AsyncMesaClient
from mesa_memory.api import server
from mesa_memory.security.rbac import AccessControl


@pytest.mark.asyncio
async def test_async_sdk_purge_uses_server_api_key_header(tmp_path):
    policy = AccessControl(policy_path=str(tmp_path / "rbac.db"))
    await policy.initialize()
    await policy.grant_principal_permission("principal-a", "agent-a", "PURGE")
    await policy.grant_access("agent-a", "__any__", "WRITE")
    dao = SimpleNamespace(purge_memory=AsyncMock(return_value=0))
    app = FastAPI()
    app.include_router(
        create_memory_router(get_dao=lambda: dao, get_access_control=lambda: policy),  # type: ignore[return-value]
        dependencies=[Depends(server.get_api_key)],
    )
    previous = (
        server._MESA_API_KEY,
        server._MESA_PRINCIPAL_ID,
        server._MESA_PRINCIPAL_STATUS,
    )
    server._MESA_API_KEY = "isolated-sdk-key"
    server._MESA_PRINCIPAL_ID = "principal-a"
    server._MESA_PRINCIPAL_STATUS = "active"
    client = AsyncMesaClient(
        base_url="http://mesa.test", api_key="isolated-sdk-key", max_retries=0
    )
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://mesa.test",
        headers=client._client.headers,
    )
    try:
        result = await client.purge(
            MemoryPurgeRequest(agent_id="agent-a", scope="agent", scope_id="agent-a")
        )
        assert result.status == "purged"
        assert result.deleted_records_count == 0
        dao.purge_memory.assert_awaited_once()
    finally:
        await client.aclose()
        (
            server._MESA_API_KEY,
            server._MESA_PRINCIPAL_ID,
            server._MESA_PRINCIPAL_STATUS,
        ) = previous
        await policy.close()


@pytest.mark.asyncio
@pytest.mark.optional_mcp
async def test_mcp_http_service_uses_configured_api_key_and_service_boundary(tmp_path, monkeypatch):
    from mesa_mcp import http_service as mcp_http_service
    from mesa_mcp.configuration import MCPSettings
    from mesa_mcp.http_service import MesaHttpMemoryService

    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def _request(self, method, path):
            assert (method, path) == ("GET", "/v3/health")
            return {"status": "healthy"}

    monkeypatch.setattr(mcp_http_service, "AsyncMesaClient", FakeClient)
    settings = MCPSettings(
        MESA_WORKSPACE_ROOT=tmp_path,
        MESA_BASE_URL="http://mesa.test",
        MESA_API_KEY="isolated-mcp-key",
    )
    assert await MesaHttpMemoryService(settings).health() == {"status": "healthy"}
    assert captured == {
        "base_url": "http://mesa.test",
        "api_key": "isolated-mcp-key",
        "timeout": 10.0,
        "max_retries": 2,
    }
