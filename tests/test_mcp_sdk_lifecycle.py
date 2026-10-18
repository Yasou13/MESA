"""Exercise the SDK's patched session limits through MESA's authenticated transport."""

from __future__ import annotations

from functools import partial

import anyio
import httpx
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from mesa_mcp.gateway import codex_transport
from mesa_mcp.gateway.auth import GatewayPrincipal


class _Auth:
    async def authenticate_credential(self, token: str):
        if token in {"first", "other"}:
            return GatewayPrincipal(token, f"credential-{token}", f"binding-{token}")
        return None


class _Operations:
    async def call_tool_for_principal(self, *, principal, tool_name, arguments):
        return {"status": "READY", "client": principal.client_id}


def _transport(monkeypatch, *, idle_timeout=30.0):
    monkeypatch.setattr(
        codex_transport,
        "StreamableHTTPSessionManager",
        partial(
            StreamableHTTPSessionManager,
            max_sessions=1,
            session_idle_timeout=idle_timeout,
            json_response=True,
        ),
    )
    return codex_transport.CodexStreamableTransport(_Operations(), _Auth())


def _client(transport):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=transport),
        base_url="http://test",
        headers={
            "Authorization": "Bearer first",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        },
    )


async def _initialize(client):
    return await client.post(
        "/",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "mesa-sdk-contract", "version": "1"},
            },
        },
    )


@pytest.mark.asyncio
async def test_session_capacity_is_released_on_authenticated_delete(monkeypatch):
    transport = _transport(monkeypatch)
    async with transport.run(), _client(transport) as client:
        first = await _initialize(client)
        assert first.status_code == 200
        session = {"mcp-session-id": first.headers["mcp-session-id"]}
        assert (await _initialize(client)).status_code == 503
        denied = await client.delete(
            "/", headers={**session, "Authorization": "Bearer other"}
        )
        assert denied.status_code == 404
        assert (await _initialize(client)).status_code == 503
        assert (await client.delete("/", headers=session)).status_code == 200
        assert (await _initialize(client)).status_code == 200


@pytest.mark.asyncio
async def test_idle_session_expires_and_client_can_initialize_again(monkeypatch):
    transport = _transport(monkeypatch, idle_timeout=0.05)
    async with transport.run(), _client(transport) as client:
        first = await _initialize(client)
        assert first.status_code == 200
        session = {"mcp-session-id": first.headers["mcp-session-id"]}
        await anyio.sleep(0.2)
        expired = await client.post(
            "/",
            headers=session,
            json={"jsonrpc": "2.0", "id": 2, "method": "ping"},
        )
        assert expired.status_code == 404
        replacement = await _initialize(client)
        assert replacement.status_code == 200
        assert replacement.headers["mcp-session-id"] != session["mcp-session-id"]


@pytest.mark.asyncio
async def test_v1_client_keeps_three_stream_values_and_mesa_tool_registration(monkeypatch):
    transport = _transport(monkeypatch)
    async with transport.run(), _client(transport) as client:
        async with streamable_http_client("http://test/", http_client=client) as streams:
            read, write, get_session_id = streams
            async with ClientSession(read, write) as session:
                await session.initialize()
                assert get_session_id()
                tools = await session.list_tools()
                assert "mesa_recall" in {tool.name for tool in tools.tools}
                assert "mesa_store_memory" not in {tool.name for tool in tools.tools}
