from pathlib import Path

import pytest

pytest.importorskip("mcp")

import mcp.types as types

from mesa_mcp.adapter import MesaMCPAdapter
from mesa_mcp.configuration import MCPSettings
from mesa_mcp.errors import MCPError
from mesa_mcp.server import MESA_BASE_URL, _tools, create_mcp_server

pytestmark = pytest.mark.optional_mcp


class FakeMemoryService:
    def __init__(self) -> None:
        self.created: dict | None = None

    async def health(self) -> dict:
        return {"status": "healthy", "components": {"storage": "healthy"}}

    async def create_memory(self, **kwargs: object) -> dict:
        self.created = kwargs
        return {"id": "raw_1", "content": kwargs["content"], "status": "queued"}

    async def search_memories(self, **_kwargs: object) -> list[dict]:
        return [
            {
                "memory_id": "mem_1",
                "content": "Use async services for all repository boundaries.",
                "memory_type": "convention",
                "score": 0.9,
            },
            {
                "memory_id": "mem_2",
                "content": "This second result does not fit the tiny context budget.",
                "memory_type": "architecture",
                "score": 0.8,
            },
        ]

    async def get_memory(self, **kwargs: object) -> dict | None:
        return {"id": kwargs["memory_id"], "content": "Scoped memory"}


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
        "mesa_store_memory",
        "mesa_search_memory",
        "mesa_get_memory",
        "mesa_get_context",
        "mesa_remember",
        "mesa_recall",
        "mesa_improve",
        "mesa_forget",
    }
    assert all("rebuild" not in tool.name for tool in _tools())


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
async def test_store_scopes_actor_namespace_and_normalized_source(
    adapter: MesaMCPAdapter,
) -> None:
    response = await adapter.store_memory(
        {
            "content": "All repository services use async interfaces.",
            "project_id": "mesa",
            "memory_type": "convention",
            "source_file": "mesa_api/router.py",
        }
    )

    assert response["memory"]["id"] == "raw_1"
    assert response["operation"] == "created"


@pytest.mark.asyncio
async def test_store_rejects_secrets_before_the_service(
    adapter: MesaMCPAdapter,
) -> None:
    with pytest.raises(MCPError, match="secret"):
        await adapter.store_memory(
            {
                "content": "api_key=definitely-not-a-real-key",
                "memory_type": "fact",
            }
        )


@pytest.mark.asyncio
async def test_context_packs_results_to_the_requested_budget(
    adapter: MesaMCPAdapter,
) -> None:
    response = await adapter.get_context(
        {"query": "service conventions", "token_budget": 15}
    )

    assert response["usage"] == {
        "estimated_tokens": 13,
        "token_budget": 15,
        "truncated": True,
    }
    assert len(response["context"]["relevant_memories"]) == 1


@pytest.mark.asyncio
async def test_get_memory_delegates_a_project_scoped_lookup(
    adapter: MesaMCPAdapter,
) -> None:
    assert await adapter.get_memory({"memory_id": "raw_1", "project_id": "mesa"}) == {
        "memory": {"id": "raw_1", "content": "Scoped memory"}
    }
