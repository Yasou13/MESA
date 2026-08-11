from unittest.mock import AsyncMock

import pytest

from mesa_client.client import AsyncMesaV4Client, MesaAPIError
from mesa_mcp.adapter import MesaMCPAdapter
from mesa_mcp.configuration import MCPSettings
from mesa_mcp.errors import MCPError
from mesa_mcp.v4_service import MesaHttpV4Service


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


@pytest.mark.asyncio
async def test_sdk_context_forwards_cross_session_query_and_temporal_budget():
    client = AsyncMesaV4Client(
        base_url="http://mesa.invalid", api_key="test_api_key"
    )
    request = AsyncMock(return_value={"canonical_memories": [{"memory_id": "m1"}]})
    client._request = request
    try:
        result = await client.get_context(
            session_id="session_b",
            query="Which database is used?",
            token_budget=321,
            valid_at="2024-01-01T00:00:00Z",
        )
    finally:
        await client.aclose()

    assert result["canonical_memories"] == [{"memory_id": "m1"}]
    request.assert_awaited_once_with(
        "GET",
        "/v4/sessions/session_b/context",
        params={
            "query": "Which database is used?",
            "token_budget": 321,
            "valid_at": "2024-01-01T00:00:00Z",
        },
    )


@pytest.mark.asyncio
async def test_mcp_adapter_preserves_canonical_context_and_write_semantics():
    settings = MCPSettings(
        base_url="http://127.0.0.1:8000",
        api_key="test_api_key",
        default_tenant_id="tenant_conv",
        default_workspace_id="ws_conv",
        default_dataset_id="dataset_conv",
        actor_id="actor_conv",
    )
    legacy = AsyncMock()
    v4 = AsyncMock()
    v4.v4_context.return_value = {
        "context": "Database PostgreSQL",
        "canonical_memories": [{"memory_id": "m1"}],
        "estimated_token_count": 5,
    }
    adapter = MesaMCPAdapter(legacy, settings, v4)

    context = await adapter.get_context(
        {"query": "Which database?", "token_budget": 100}
    )
    assert context["canonical_memories"] == [{"memory_id": "m1"}]
    legacy.search_memories.assert_not_awaited()
    v4.v4_context.assert_awaited_once_with(
        dataset_id=None,
        query="Which database?",
        token_budget=100,
        valid_at=None,
    )

    await adapter.mesa_remember(
        {
            "content": "Database PostgreSQL",
            "metadata": {"memory_type": "fact"},
            "idempotency_key": "remember-key",
        }
    )
    v4.v4_remember.assert_awaited_once_with(
        dataset_id=None,
        content="Database PostgreSQL",
        metadata={"memory_type": "fact"},
        idempotency_key="remember-key",
    )

    await adapter.mesa_improve(
        {
            "document_id": "doc_db",
            "content": "Database PostgreSQL",
            "metadata": {"valid_from": "2024-01-01T00:00:00Z"},
            "supersedes_revision_id": "rev_sqlite",
            "idempotency_key": "improve-key",
        }
    )
    v4.v4_improve.assert_awaited_once_with(
        dataset_id=None,
        document_id="doc_db",
        content="Database PostgreSQL",
        metadata={"valid_from": "2024-01-01T00:00:00Z"},
        supersedes_revision_id="rev_sqlite",
        idempotency_key="improve-key",
    )


@pytest.mark.asyncio
async def test_mcp_improve_discovers_latest_revision_and_is_retry_stable():
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
    client.list_revisions.return_value = {
        "revisions": [
            {
                "revision_id": "rev_sqlite",
                "revision_number": 4,
                "status": "ACTIVE",
            }
        ]
    }
    client.start_session.return_value = {"session_id": "session_conv"}
    client.insert.return_value = {"status": "accepted", "mutation_id": "mut_corr"}
    service._http_client = client

    await service.v4_improve(
        document_id="doc_corr",
        content="Database PostgreSQL",
        idempotency_key="stable-correction",
    )
    call = client.insert.await_args.kwargs
    assert call["supersedes_revision_id"] == "rev_sqlite"
    assert call["revision_number"] == 5
    assert call["revision_id"] == "rev_" + call["chunk_id"].removeprefix("chunk_")
