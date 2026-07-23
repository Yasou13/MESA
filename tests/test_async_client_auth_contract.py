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
async def test_mcp_forget_memory_uses_configured_agent_and_async_sdk_auth(tmp_path):
    from mesa_mcp import server as mcp_server

    previous_mcp = (
        mcp_server.MESA_AGENT_ID,
        mcp_server.MESA_TENANT_ID,
        mcp_server.MESA_WORKSPACE_ID,
        mcp_server.MESA_API_KEY,
        mcp_server.AsyncMesaV4Client,
    )
    purge = AsyncMock(return_value={"state": "PURGE_PENDING"})

    class FakeV4Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        purge_document = purge

    mcp_server.MESA_AGENT_ID = "agent-a"
    mcp_server.MESA_TENANT_ID = "tenant-a"
    mcp_server.MESA_WORKSPACE_ID = "workspace-a"
    mcp_server.MESA_API_KEY = "isolated-mcp-key"
    mcp_server.AsyncMesaV4Client = lambda **_kwargs: FakeV4Client()
    try:
        result = await mcp_server.call_tool(
            "forget_memory",
            {"dataset_id": "dataset-a", "document_id": "document-a"},
        )
        assert "Document purge accepted" in result[0].text
        purge.assert_awaited_once_with(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            dataset_id="dataset-a",
            document_id="document-a",
        )
    finally:
        (
            mcp_server.MESA_AGENT_ID,
            mcp_server.MESA_TENANT_ID,
            mcp_server.MESA_WORKSPACE_ID,
            mcp_server.MESA_API_KEY,
            mcp_server.AsyncMesaV4Client,
        ) = previous_mcp
