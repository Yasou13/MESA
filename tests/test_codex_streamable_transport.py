from __future__ import annotations

import httpx
import pytest
from mesa_mcp.gateway.auth import GatewayPrincipal
from mesa_mcp.gateway.codex_transport import CodexStreamableTransport


class _Auth:
    async def authenticate_credential(self, token: str):
        return GatewayPrincipal("codex", "cred", "binding") if token == "ok" else None


class _Operations:
    async def call_tool_for_principal(self, *, principal, tool_name, arguments):
        return {"status": "READY", "client": principal.client_id, "tool": tool_name}


@pytest.mark.asyncio
async def test_streamable_http_authenticates_initialize_and_lists_only_codex_tools():
    transport = CodexStreamableTransport(_Operations(), _Auth())
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    async with transport.run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=transport),
            base_url="http://test",
            headers={**headers, "Authorization": "Bearer ok"},
        ) as client:
            initial = await client.post(
                "/",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "1"},
                    },
                },
            )
            assert initial.status_code == 200
            session_id = initial.headers["mcp-session-id"]
            session_headers = {
                "mcp-session-id": session_id,
                "mcp-protocol-version": "2025-03-26",
            }
            ready = await client.post(
                "/",
                headers=session_headers,
                json={
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                    "params": {},
                },
            )
            assert ready.status_code == 202
            listed = await client.post(
                "/",
                headers=session_headers,
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            )
            assert listed.status_code == 200
            assert "mesa_recall" in listed.text
            assert "mesa_store_memory" not in listed.text
            called = await client.post(
                "/",
                headers=session_headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "mesa_health", "arguments": {}},
                },
            )
            assert called.status_code == 200
            assert '\\"client\\": \\"codex\\"' in called.text


@pytest.mark.asyncio
async def test_streamable_http_rejects_missing_or_invalid_bearer_token():
    transport = CodexStreamableTransport(_Operations(), _Auth())
    async with transport.run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=transport), base_url="http://test"
        ) as client:
            response = await client.post(
                "/",
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            )
    assert response.status_code == 401
