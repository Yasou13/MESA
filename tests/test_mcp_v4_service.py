from __future__ import annotations

import asyncio

import pytest

from mesa_client.client import MesaAPIError
from mesa_mcp.configuration import MCPSettings
from mesa_mcp.v4_service import MesaHttpV4Service


class RecordingV4Client:
    def __init__(self) -> None:
        self.documents: list[dict] = []
        self.inserts: list[dict] = []

    async def start_session(self, **_kwargs):
        return {"session_id": "session-1"}

    async def create_document(self, **kwargs):
        self.documents.append(kwargs)
        return {"document_id": kwargs["document_id"]}

    async def insert(self, **kwargs):
        self.inserts.append(kwargs)
        return {"mutation_id": f"mutation-{len(self.inserts)}"}

    async def search(self, **_kwargs):
        return {
            "results": [
                {
                    "entity": {
                        "entity_id": "entity-1",
                        "canonical_name": "Approved writes",
                        "status": "ACTIVE",
                    },
                    "rrf_score": 0.2,
                    "final_score": 0.25,
                    "provenance": [
                        {
                            "document_id": "doc-1",
                            "revision_id": "rev-1",
                            "chunk_id": "chunk-1",
                            "source_ref": "mcp_tool",
                            "metadata": {"memory_type": "decision"},
                        }
                    ],
                }
            ]
        }

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_v4_remember_generates_unique_provenance_per_write_and_stable_ids_per_retry():
    service = MesaHttpV4Service(MCPSettings(api_key="test-key", use_v4=True))
    client = RecordingV4Client()
    service._http_client = client  # type: ignore[assignment]

    await service.v4_remember(
        tenant_id="tenant",
        workspace_id="workspace",
        dataset_id="dataset",
        actor_id="agent",
        content="first",
        idempotency_key="write-1",
        source_ref="meeting://architecture/42",
        evidence_span="12:51",
    )
    await service.v4_remember(
        tenant_id="tenant",
        workspace_id="workspace",
        dataset_id="dataset",
        actor_id="agent",
        content="second",
        idempotency_key="write-2",
    )
    await service.v4_remember(
        tenant_id="tenant",
        workspace_id="workspace",
        dataset_id="dataset",
        actor_id="agent",
        content="first",
        idempotency_key="write-1",
    )

    first, second, retry = client.inserts
    assert first["document_id"] != second["document_id"]
    assert first["revision_id"] != second["revision_id"]
    assert first["chunk_id"] != second["chunk_id"]
    assert {key: retry[key] for key in ("document_id", "revision_id", "chunk_id")} == {
        key: first[key] for key in ("document_id", "revision_id", "chunk_id")
    }
    assert first["source_ref"] == "meeting://architecture/42"
    assert first["evidence_span"] == "12:51"


@pytest.mark.asyncio
async def test_v4_recall_maps_v4_entity_and_assertion_shape_to_typed_memory():
    service = MesaHttpV4Service(MCPSettings(api_key="test-key", use_v4=True))
    client = RecordingV4Client()
    service._http_client = client  # type: ignore[assignment]

    results = await service.v4_recall(
        tenant_id="tenant",
        workspace_id="workspace",
        dataset_id="dataset",
        actor_id="agent",
        query="approved writes",
    )

    assert results == [
        {
            "memory_id": "entity-1",
            "document_id": "doc-1",
            "chunk_id": "chunk-1",
            "content": "Approved writes",
            "memory_type": "decision",
            "status": "ACTIVE",
            "score": 0.25,
            "provenance": {
                "entity_id": "entity-1",
                "assertions": [
                    {
                        "document_id": "doc-1",
                        "revision_id": "rev-1",
                        "chunk_id": "chunk-1",
                        "source_ref": "mcp_tool",
                        "metadata": {"memory_type": "decision"},
                    }
                ],
            },
        }
    ]


@pytest.mark.asyncio
async def test_session_cache_single_flights_and_replaces_a_rejected_session() -> None:
    class SessionClient:
        def __init__(self) -> None:
            self.start_calls = 0
            self.search_sessions: list[str] = []

        async def start_session(self, **_kwargs):
            self.start_calls += 1
            await asyncio.sleep(0)
            return {"session_id": f"session-{self.start_calls}"}

        async def search(self, *, session_id: str, **_kwargs):
            self.search_sessions.append(session_id)
            if session_id == "session-1":
                raise MesaAPIError(409, "SESSION_INACTIVE", "Session is not active")
            return {"results": []}

        async def aclose(self) -> None:
            return None

    service = MesaHttpV4Service(MCPSettings(api_key="test-key", use_v4=True))
    client = SessionClient()
    service._http_client = client  # type: ignore[assignment]
    sessions = await asyncio.gather(
        *[
            service._get_session_id(
                client,
                "dataset",
                tenant_id="tenant",
                workspace_id="workspace",
                actor_id="agent",
            )
            for _ in range(8)
        ]
    )
    assert sessions == ["session-1"] * 8
    assert client.start_calls == 1

    assert (
        await service.v4_recall(
            tenant_id="tenant",
            workspace_id="workspace",
            dataset_id="dataset",
            actor_id="agent",
            query="replace stale session",
        )
        == []
    )
    assert client.search_sessions == ["session-1", "session-2"]


@pytest.mark.asyncio
async def test_v4_physical_ids_are_scoped_and_unrelated_409_is_not_swallowed():
    class ConflictClient(RecordingV4Client):
        async def create_document(self, **kwargs):
            raise MesaAPIError(409, "CONFLICT", "unrelated document collision")

        async def list_documents(self, **_kwargs):
            return {"documents": []}

    first = MesaHttpV4Service(MCPSettings(api_key="test-key", use_v4=True))
    second = MesaHttpV4Service(MCPSettings(api_key="test-key", use_v4=True))
    first_client = RecordingV4Client()
    second_client = RecordingV4Client()
    first._http_client = first_client  # type: ignore[assignment]
    second._http_client = second_client  # type: ignore[assignment]

    await first.v4_remember(
        tenant_id="tenant-a",
        workspace_id="workspace",
        dataset_id="dataset",
        actor_id="agent",
        content="fact",
        idempotency_key="same-key",
    )
    await second.v4_remember(
        tenant_id="tenant-b",
        workspace_id="workspace",
        dataset_id="dataset",
        actor_id="agent",
        content="fact",
        idempotency_key="same-key",
    )
    assert (
        first_client.inserts[0]["document_id"]
        != second_client.inserts[0]["document_id"]
    )

    conflict = MesaHttpV4Service(MCPSettings(api_key="test-key", use_v4=True))
    conflict._http_client = ConflictClient()  # type: ignore[assignment]
    with pytest.raises(Exception) as raised:
        await conflict.v4_remember(
            tenant_id="tenant-a",
            workspace_id="workspace",
            dataset_id="dataset",
            actor_id="agent",
            content="fact",
            idempotency_key="same-key",
        )
    assert getattr(raised.value, "code", None) == "CONFLICT"


@pytest.mark.asyncio
async def test_unrelated_session_409_is_not_treated_as_cache_recovery():
    class ConflictClient(RecordingV4Client):
        def __init__(self):
            super().__init__()
            self.start_calls = 0

        async def start_session(self, **_kwargs):
            self.start_calls += 1
            return {"session_id": "session-1"}

        async def search(self, **_kwargs):
            raise MesaAPIError(409, "REVISION_HEAD_CONFLICT", "not a session error")

    service = MesaHttpV4Service(MCPSettings(api_key="test-key", use_v4=True))
    client = ConflictClient()
    service._http_client = client  # type: ignore[assignment]
    with pytest.raises(Exception) as raised:
        await service.v4_recall(query="x")
    assert getattr(raised.value, "code", None) == "REVISION_HEAD_CONFLICT"
    assert client.start_calls == 1


@pytest.mark.asyncio
async def test_v4_context_and_improve_preserve_canonical_v4_arguments() -> None:
    """MCP context and corrections must use the V4 service without data loss."""

    class ContextAndCorrectionClient(RecordingV4Client):
        def __init__(self) -> None:
            super().__init__()
            self.context_calls: list[dict] = []
            self.revision_calls: list[dict] = []

        async def get_context(self, **kwargs):
            self.context_calls.append(kwargs)
            return {"canonical_memory": ["current policy"]}

        async def list_revisions(self, **kwargs):
            self.revision_calls.append(kwargs)
            return {
                "revisions": [
                    {
                        "revision_id": "rev-old",
                        "revision_number": 1,
                        "status": "SUPERSEDED",
                    },
                    {
                        "revision_id": "rev-current",
                        "revision_number": 2,
                        "status": "ACTIVE",
                    },
                ]
            }

    service = MesaHttpV4Service(MCPSettings(api_key="test-key", use_v4=True))
    client = ContextAndCorrectionClient()
    service._http_client = client  # type: ignore[assignment]

    context = await service.v4_context(
        tenant_id="tenant",
        workspace_id="workspace",
        dataset_id="dataset",
        actor_id="agent",
        query="current policy",
        token_budget=321,
        valid_at="2026-01-01T00:00:00Z",
    )
    mutation = await service.v4_improve(
        tenant_id="tenant",
        workspace_id="workspace",
        dataset_id="dataset",
        actor_id="agent",
        document_id="doc-policy",
        content="the corrected policy",
        idempotency_key="correction-1",
        metadata={"memory_type": "decision"},
    )

    assert context == {"canonical_memory": ["current policy"]}
    assert client.context_calls == [
        {
            "session_id": "session-1",
            "query": "current policy",
            "token_budget": 321,
            "valid_at": "2026-01-01T00:00:00Z",
        }
    ]
    assert client.revision_calls == [
        {
            "tenant_id": "tenant",
            "workspace_id": "workspace",
            "dataset_id": "dataset",
            "document_id": "doc-policy",
        }
    ]
    assert mutation == {"mutation_id": "mutation-1"}
    assert len(client.inserts) == 1
    insert = client.inserts[0]
    assert insert["session_id"] == "session-1"
    assert insert["document_id"] == "doc-policy"
    assert insert["revision_id"].startswith("rev_")
    assert insert["chunk_id"].startswith("chunk_")
    assert insert["revision_number"] == 3
    assert insert["metadata"] == {"memory_type": "decision"}
    assert insert["idempotency_key"] == "correction-1"
    assert insert["supersedes_revision_id"] == "rev-current"
