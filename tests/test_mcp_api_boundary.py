import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("mcp")

import mcp.types as types

from mesa_client.client import MesaAPIError
from mesa_mcp.adapter import MesaMCPAdapter
from mesa_mcp.configuration import MCPSettings
from mesa_mcp.errors import MCPError
from mesa_mcp.server import MESA_BASE_URL, _tools, create_mcp_server
from mesa_mcp.v4_service import MesaHttpV4Service

pytestmark = pytest.mark.optional_mcp


class FakeMemoryService:
    async def health(self) -> dict:
        return {"status": "healthy", "components": {"storage": "healthy"}}


class CapabilityOnlyV4Service:
    async def v4_capability(self) -> dict:
        return {
            "api_version": "v4",
            "capabilities": {"durable_rebuild": True},
            "limits": {"rebuild_scope": "storage_root"},
        }


@pytest.fixture()
def adapter(tmp_path: Path) -> MesaMCPAdapter:
    settings = MCPSettings(MESA_WORKSPACE_ROOT=tmp_path)
    return MesaMCPAdapter(FakeMemoryService(), settings)


def test_mcp_default_base_url_has_no_version_suffix() -> None:
    assert MESA_BASE_URL == "http://localhost:8000"


def test_mcp_exposes_only_the_v1_tool_set() -> None:
    assert {tool.name for tool in _tools()} == {
        "mesa_health",
        "mesa_get_context",
        "mesa_remember",
        "mesa_recall",
        "mesa_improve",
        "mesa_forget",
    }
    assert all("rebuild" not in tool.name for tool in _tools())


def test_mcp_v4_tool_identifier_schemas_match_api_bounds() -> None:
    tools = {tool.name: tool.inputSchema for tool in _tools()}

    dataset = tools["mesa_remember"]["properties"]["dataset_id"]
    document = tools["mesa_improve"]["properties"]["document_id"]
    assert dataset == {
        "type": "string",
        "minLength": 1,
        "maxLength": 128,
        "pattern": "^[a-zA-Z0-9._-]+$",
    }
    assert document == {
        "type": "string",
        "minLength": 1,
        "maxLength": 256,
    }


@pytest.mark.asyncio
async def test_mcp_health_exposes_truthful_v4_capability_without_rebuild_tool(
    tmp_path: Path,
) -> None:
    adapter = MesaMCPAdapter(
        FakeMemoryService(),
        MCPSettings(MESA_WORKSPACE_ROOT=tmp_path),
        CapabilityOnlyV4Service(),  # type: ignore[arg-type]
    )

    health = await adapter.health()

    from mesa_mcp import __version__

    assert health["server_version"] == __version__ == "0.9.0"
    assert health["v4_capability"] == {
        "api_version": "v4",
        "capabilities": {"durable_rebuild": True},
        "limits": {"rebuild_scope": "storage_root"},
    }


@pytest.mark.asyncio
async def test_protocol_errors_are_marked_as_mcp_errors(tmp_path: Path) -> None:
    server = create_mcp_server(
        FakeMemoryService(), MCPSettings(MESA_WORKSPACE_ROOT=tmp_path)
    )
    handler = server.request_handlers[types.CallToolRequest]
    result = await handler(
        types.CallToolRequest(
            params=types.CallToolRequestParams(name="unknown_tool", arguments={})
        )
    )
    assert result.root.isError is True


@pytest.mark.asyncio
async def test_context_without_v4_service_raises_unimplemented(
    adapter: MesaMCPAdapter,
) -> None:
    with pytest.raises(MCPError, match="context requires the V4 service"):
        await adapter.get_context(
            {"query": "service conventions", "token_budget": 15}
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "expected_code"),
    [
        (403, "ACCESS_DENIED"),
        (401, "UNAUTHORIZED"),
        (400, "INVALID_ARGUMENT"),
    ],
)
async def test_mcp_session_start_errors_reach_agent_not_internal_error(
    tmp_path: Path, status_code: int, expected_code: str
) -> None:
    settings = MCPSettings(MESA_WORKSPACE_ROOT=tmp_path, use_v4=True)
    v4_svc = MesaHttpV4Service(settings)
    mock_client = AsyncMock()
    mock_client.start_session.side_effect = MesaAPIError(
        status_code, "ERR", f"Error {status_code}"
    )
    v4_svc._http_client = mock_client

    server = create_mcp_server(FakeMemoryService(), settings, v4_svc)
    handler = server.request_handlers[types.CallToolRequest]
    res = await handler(
        types.CallToolRequest(
            params=types.CallToolRequestParams(
                name="mesa_recall",
                arguments={"query": "test query", "dataset_id": "test_ds"},
            )
        )
    )
    payload = json.loads(res.root.content[0].text)
    assert payload["error"]["code"] != "INTERNAL_ERROR"
    if status_code == 401:
        assert payload["error"]["code"] in ("UNAUTHORIZED", "ACCESS_DENIED")
    else:
        assert payload["error"]["code"] == expected_code

