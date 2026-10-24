"""WAVE-001-V async SDK uses the API authentication header contract."""

from __future__ import annotations

import httpx
import pytest


@pytest.mark.asyncio
@pytest.mark.optional_mcp
async def test_mcp_http_service_uses_configured_api_key_and_service_boundary(
    tmp_path, monkeypatch
):
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
            assert (method, path) == ("GET", "/health")
            return {"status": "healthy"}

    monkeypatch.setattr(mcp_http_service, "AsyncMesaClient", FakeClient)
    settings = MCPSettings(
        MESA_WORKSPACE_ROOT=tmp_path,
        MESA_BASE_URL="http://mesa.test",
        MESA_API_KEY="isolated-mcp-key",
    )
    assert await MesaHttpMemoryService(settings).health() == {"status": "healthy"}
    assert captured["base_url"] == "http://mesa.test"
    assert captured["api_key"] == "isolated-mcp-key"
    # The long-lived MCP client uses a bounded connect/read/write/pool budget
    # and lets its gateway-level breaker own retry policy.
    timeout = captured["timeout"]
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.connect == 2.0
    assert timeout.read == timeout.write == 8.0
    assert timeout.pool == 2.0
    assert captured["max_retries"] == 0
