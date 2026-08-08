"""Python SDK routing contract for the breaking V4 surface."""

from unittest.mock import AsyncMock, MagicMock, call

import pytest

from mesa_client.client import (
    AsyncMesaV4Client,
    MesaV4Client,
    MesaValidationError,
)


def test_sync_v4_capability_uses_versioned_contract(monkeypatch) -> None:
    request = MagicMock(return_value={"api_version": "v4"})
    monkeypatch.setattr(MesaV4Client, "_request", request)
    client = MesaV4Client(base_url="http://mesa.invalid", api_key="test-key")
    try:
        result = client.capability()
    finally:
        client.close()

    assert result == {"api_version": "v4"}
    request.assert_called_once_with("GET", "/v4/capability")


@pytest.mark.asyncio
async def test_async_v4_capability_matches_sync_contract(monkeypatch) -> None:
    request = AsyncMock(return_value={"api_version": "v4"})
    monkeypatch.setattr(AsyncMesaV4Client, "_request", request)
    client = AsyncMesaV4Client(base_url="http://mesa.invalid", api_key="test-key")
    try:
        result = await client.capability()
    finally:
        await client.aclose()

    assert result == {"api_version": "v4"}
    request.assert_awaited_once_with("GET", "/v4/capability")


def test_sync_v4_rebuild_operation_methods_match_admin_contract(monkeypatch) -> None:
    request = MagicMock(return_value={"operation_id": "operation-a"})
    monkeypatch.setattr(MesaV4Client, "_request", request)
    client = MesaV4Client(base_url="http://mesa.invalid", api_key="test-key")
    try:
        assert client.submit_rebuild(idempotency_key="rebuild-a") == {
            "operation_id": "operation-a"
        }
        client.operation_status("operation-a")
        client.cancel_operation("operation-a")
        client.retry_operation("operation-a")
    finally:
        client.close()

    assert request.call_args_list == [
        call(
            "POST",
            "/v4/operations/rebuild",
            headers={"Idempotency-Key": "rebuild-a"},
        ),
        call("GET", "/v4/operations/operation-a"),
        call("POST", "/v4/operations/operation-a/cancel"),
        call("POST", "/v4/operations/operation-a/retry"),
    ]


@pytest.mark.asyncio
async def test_async_v4_rebuild_operation_methods_match_sync_contract(
    monkeypatch,
) -> None:
    request = AsyncMock(return_value={"operation_id": "operation-a"})
    monkeypatch.setattr(AsyncMesaV4Client, "_request", request)
    client = AsyncMesaV4Client(base_url="http://mesa.invalid", api_key="test-key")
    try:
        await client.submit_rebuild(idempotency_key="rebuild-a")
        await client.operation_status("operation-a")
        await client.cancel_operation("operation-a")
        await client.retry_operation("operation-a")
    finally:
        await client.aclose()

    assert request.await_args_list == [
        call(
            "POST",
            "/v4/operations/rebuild",
            headers={"Idempotency-Key": "rebuild-a"},
        ),
        call("GET", "/v4/operations/operation-a"),
        call("POST", "/v4/operations/operation-a/cancel"),
        call("POST", "/v4/operations/operation-a/retry"),
    ]


def test_v4_rebuild_sdk_rejects_unsafe_control_values() -> None:
    client = MesaV4Client(base_url="http://mesa.invalid", api_key="test-key")
    try:
        with pytest.raises(MesaValidationError, match="idempotency_key"):
            client.submit_rebuild(idempotency_key=" leading-space")
        with pytest.raises(MesaValidationError, match="operation_id"):
            client.operation_status("")
    finally:
        client.close()


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
