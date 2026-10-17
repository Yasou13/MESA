"""Test sync and async MesaClient /v4/memory/insert parity with legal_identity.

Verifies T06c (A-15):
- MesaClient.insert and AsyncMesaClient.insert accept optional legal_identity.
- When provided, legal_identity is serialized in the POST /v4/memory/insert payload.
- When omitted, legacy payload structure is preserved without breaking changes.
"""

from unittest.mock import AsyncMock, MagicMock
import pytest

from mesa_client.client import AsyncMesaV4Client, MesaV4Client


def test_sync_mesa_client_insert_legal_identity_parity():
    client = MesaV4Client(base_url="http://test.local", api_key="secret", max_retries=0)
    client._request = MagicMock(return_value={"status": "admitted"})

    # 1. With legal_identity
    legal_id = {
        "law_id": "law-5651",
        "statute_code": "5651",
        "article_id": "8",
    }
    client.insert(
        session_id="session-1",
        dataset_id="dataset-1",
        document_id="doc-1",
        revision_id="rev-1",
        chunk_id="chunk-1",
        title="Title",
        source_ref="ref-1",
        content="Content payload",
        legal_identity=legal_id,
    )
    client._request.assert_called_once()
    method, path = client._request.call_args[0]
    json_body = client._request.call_args[1]["json"]
    assert method == "POST"
    assert path == "/v4/memory/insert"
    assert json_body["legal_identity"] == legal_id
    assert json_body["content"] == "Content payload"

    # 2. Without legal_identity (backwards compatible)
    client._request.reset_mock()
    client.insert(
        session_id="session-1",
        dataset_id="dataset-1",
        document_id="doc-1",
        revision_id="rev-1",
        chunk_id="chunk-1",
        title="Title",
        source_ref="ref-1",
        content="Content payload",
    )
    json_body2 = client._request.call_args[1]["json"]
    assert "legal_identity" not in json_body2


@pytest.mark.asyncio
async def test_async_mesa_client_insert_legal_identity_parity():
    client = AsyncMesaV4Client(base_url="http://test.local", api_key="secret", max_retries=0)
    client._request = AsyncMock(return_value={"status": "admitted"})

    # 1. With legal_identity
    legal_id = {
        "law_id": "law-6698",
        "statute_code": "6698",
        "article_id": "10",
    }
    await client.insert(
        session_id="session-1",
        dataset_id="dataset-1",
        document_id="doc-1",
        revision_id="rev-1",
        chunk_id="chunk-1",
        title="Title",
        source_ref="ref-1",
        content="Content payload",
        legal_identity=legal_id,
    )
    client._request.assert_awaited_once()
    method, path = client._request.call_args[0]
    json_body = client._request.call_args[1]["json"]
    assert method == "POST"
    assert path == "/v4/memory/insert"
    assert json_body["legal_identity"] == legal_id

    # 2. Without legal_identity
    client._request.reset_mock()
    await client.insert(
        session_id="session-1",
        dataset_id="dataset-1",
        document_id="doc-1",
        revision_id="rev-1",
        chunk_id="chunk-1",
        title="Title",
        source_ref="ref-1",
        content="Content payload",
    )
    json_body2 = client._request.call_args[1]["json"]
    assert "legal_identity" not in json_body2
    await client.aclose()
