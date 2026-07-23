"""Python SDK routing contract for the breaking V4 surface."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from mesa_client.client import AsyncMesaV4Client, MesaV4Client


def test_sync_v4_insert_sends_server_scoped_provenance(monkeypatch) -> None:
    request = MagicMock(return_value={"mutation_id": "mutation-a"})
    monkeypatch.setattr(MesaV4Client, "_request", request)
    client = MesaV4Client(base_url="http://mesa.invalid", api_key="test-key")
    try:
        result = client.insert(
            session_id="session-a",
            dataset_id="dataset-a",
            document_id="document-a",
            revision_id="revision-a",
            chunk_id="chunk-a",
            title="Kanun",
            source_ref="source-a",
            content="exact content",
            evidence_span="0:13",
        )
    finally:
        client.close()

    assert result == {"mutation_id": "mutation-a"}
    request.assert_called_once_with(
        "POST",
        "/v4/memory/insert",
        json={
            "session_id": "session-a",
            "dataset_id": "dataset-a",
            "document_id": "document-a",
            "revision_id": "revision-a",
            "chunk_id": "chunk-a",
            "title": "Kanun",
            "source_ref": "source-a",
            "content": "exact content",
            "evidence_span": "0:13",
            "revision_number": 1,
            "chunk_ordinal": 0,
            "supersedes_revision_id": None,
            "metadata": {},
        },
    )


@pytest.mark.asyncio
async def test_async_v4_revision_uses_same_catalog_contract(monkeypatch) -> None:
    request = AsyncMock(return_value={"revision_id": "revision-2"})
    monkeypatch.setattr(AsyncMesaV4Client, "_request", request)
    client = AsyncMesaV4Client(base_url="http://mesa.invalid", api_key="test-key")
    try:
        result = await client.create_revision(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            dataset_id="dataset-a",
            document_id="document-a",
            revision_id="revision-2",
            revision_number=2,
            content_sha256="a" * 64,
            supersedes_revision_id="revision-1",
        )
    finally:
        await client.aclose()

    assert result == {"revision_id": "revision-2"}
    request.assert_awaited_once_with(
        "POST",
        "/v4/catalog/revisions",
        json={
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "dataset_id": "dataset-a",
            "document_id": "document-a",
            "revision_id": "revision-2",
            "revision_number": 2,
            "content_sha256": "a" * 64,
            "supersedes_revision_id": "revision-1",
        },
    )
