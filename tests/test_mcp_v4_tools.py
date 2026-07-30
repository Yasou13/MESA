from typing import Any

import pytest
from mesa_mcp.adapter import MesaMCPAdapter
from mesa_mcp.configuration import MCPSettings
from mesa_mcp.errors import MCPError
from mesa_mcp.service import MemoryServiceProtocol, V4MemoryServiceProtocol


class MockMemoryService(MemoryServiceProtocol):
    async def health(self) -> dict[str, Any]:
        return {"status": "ok"}

    async def create_memory(self, **kwargs) -> dict:
        return {}

    async def search_memories(self, **kwargs) -> list:
        return []

    async def get_memory(self, **kwargs) -> dict | None:
        return None


class MockV4Service(V4MemoryServiceProtocol):
    async def v4_remember(self, **kwargs) -> dict[str, Any]:
        return {"mutation_id": "mut-1", "pipeline_run_id": "run-1"}

    async def v4_recall(self, **kwargs) -> list[dict[str, Any]]:
        return [{"document_id": "doc-1", "content": "test"}]

    async def v4_improve(self, **kwargs) -> dict[str, Any]:
        return {"mutation_id": "mut-2"}

    async def v4_forget(self, **kwargs) -> dict[str, Any]:
        return {"status": "purged"}


@pytest.mark.asyncio
async def test_v4_mcp_tools():
    settings = MCPSettings()
    adapter = MesaMCPAdapter(MockMemoryService(), settings, MockV4Service())

    # Test mesa_remember
    res = await adapter.mesa_remember(
        {"content": "new memory", "dataset_id": "test-dataset"}
    )
    assert res["mutation_id"] == "mut-1"

    # Test mesa_recall
    res2 = await adapter.mesa_recall(
        {"query": "test query", "dataset_id": "test-dataset"}
    )
    assert res2["total"] == 1
    assert res2["results"][0]["document_id"] == "doc-1"

    # Test mesa_improve
    res3 = await adapter.mesa_improve(
        {"document_id": "doc-1", "content": "updated", "dataset_id": "test-dataset"}
    )
    assert res3["mutation_id"] == "mut-2"

    # Test mesa_forget
    res4 = await adapter.mesa_forget(
        {"document_id": "doc-1", "dataset_id": "test-dataset"}
    )
    assert res4["status"] == "purged"


@pytest.mark.asyncio
async def test_v4_mcp_write_rejects_secret_and_nested_metadata() -> None:
    adapter = MesaMCPAdapter(MockMemoryService(), MCPSettings(), MockV4Service())
    with pytest.raises(MCPError, match="secret"):
        await adapter.mesa_remember(
            {"content": "api_key=do-not-store-this-token", "dataset_id": "test"}
        )
    with pytest.raises(MCPError, match="nesting"):
        await adapter.mesa_remember(
            {
                "content": "safe",
                "dataset_id": "test",
                "metadata": {"a": {"b": {"c": "too deep"}}},
            }
        )
