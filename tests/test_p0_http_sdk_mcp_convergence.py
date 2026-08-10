import pytest
from unittest.mock import AsyncMock
from mesa_client.client import AsyncMesaV4Client, MesaAPIError
from mesa_mcp.v4_service import MesaHttpV4Service
from mesa_mcp.configuration import MCPSettings
from mesa_mcp.errors import MCPError

@pytest.mark.asyncio
async def test_sdk_mcp_convergence_and_error_mapping():
    """Verify that SDK and MCP adapters convert errors and enforce identical V4 API contracts."""
    settings = MCPSettings(
        base_url="http://127.0.0.1:8000",
        api_key="test_api_key",
        default_tenant_id="tenant_conv",
        default_workspace_id="ws_conv",
        default_dataset_id="dataset_conv",
        actor_id="actor_conv",
    )

    mcp_service = MesaHttpV4Service(settings)

    # 1. Verify AsyncMesaV4Client error propagation
    mock_client = AsyncMock()
    mock_client.capability.side_effect = MesaAPIError(401, "UNAUTHORIZED", "Unauthorized API Key")
    mcp_service._http_client = mock_client

    with pytest.raises(MCPError) as exc_info:
        await mcp_service.v4_capability()

    assert exc_info.value.code == "ACCESS_DENIED"
    assert "denied access" in exc_info.value.message

    # 2. Verify invalid argument error mapping
    mock_client.capability.side_effect = MesaAPIError(400, "INVALID_ARGUMENT", "payload size exceeds limit")
    with pytest.raises(MCPError) as exc_info:
        await mcp_service.v4_capability()

    assert exc_info.value.code == "INVALID_ARGUMENT"


@pytest.mark.asyncio
async def test_mcp_improve_admits_searchable_canonical_correction():
    settings = MCPSettings(
        base_url="http://127.0.0.1:8000",
        api_key="test_api_key",
        default_tenant_id="tenant_conv",
        default_workspace_id="ws_conv",
        default_dataset_id="dataset_conv",
        actor_id="actor_conv",
    )
    service = MesaHttpV4Service(settings)
    client = AsyncMock()
    client.start_session.return_value = {"session_id": "session_conv"}
    client.insert.return_value = {"status": "accepted", "mutation_id": "mut_corr"}
    service._http_client = client

    result = await service.v4_improve(
        document_id="doc_corr",
        content="Database PostgreSQL",
        revision_id="rev_corr",
        chunk_id="chunk_corr",
        supersedes_revision_id="rev_old",
        idempotency_key="corr-key",
    )

    assert result["mutation_id"] == "mut_corr"
    client.insert.assert_awaited_once_with(
        session_id="session_conv",
        dataset_id="dataset_conv",
        document_id="doc_corr",
        revision_id="rev_corr",
        chunk_id="chunk_corr",
        title="Correction doc_corr",
        source_ref="mcp_correction",
        content="Database PostgreSQL",
        evidence_span="",
        revision_number=2,
        metadata={},
        idempotency_key="corr-key",
        supersedes_revision_id="rev_old",
    )
