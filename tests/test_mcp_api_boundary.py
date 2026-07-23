from pathlib import Path

import pytest

pytestmark = pytest.mark.optional_mcp


def test_mcp_default_base_url_has_no_version_suffix():
    from mesa_mcp import server

    assert server.MESA_BASE_URL == "http://localhost:8000"


@pytest.mark.asyncio
async def test_mcp_catalog_tool_uses_v4_sdk(monkeypatch):
    from mesa_mcp import server

    listed = []

    class FakeV4Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def list_documents(self, **kwargs):
            listed.append(kwargs)
            return {"documents": []}

    class FakeV3Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    monkeypatch.setattr(server, "MESA_AGENT_ID", "agent-a")
    monkeypatch.setattr(server, "MESA_TENANT_ID", "tenant-a")
    monkeypatch.setattr(server, "MESA_WORKSPACE_ID", "workspace-a")
    monkeypatch.setattr(server, "AsyncMesaV4Client", lambda **_kwargs: FakeV4Client())
    monkeypatch.setattr(server, "AsyncMesaClient", lambda **_kwargs: FakeV3Client())

    result = await server.call_tool(
        "catalog",
        {"action": "list", "resource": "document", "dataset_id": "dataset-a"},
    )

    assert "documents" in result[0].text
    assert listed == [
        {
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "dataset_id": "dataset-a",
        }
    ]


def test_mcp_stats_never_opens_storage_directly():
    from mesa_mcp import server

    source = Path(server.__file__).read_text(encoding="utf-8")
    assert "mesa_storage.dao" not in source
    assert "AsyncEngine" not in source
    assert 'client._request("GET", "/v3/health")' in source
