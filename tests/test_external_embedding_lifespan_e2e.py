"""External embedding composition through the real combined-runtime lifespan."""

from __future__ import annotations

import pytest
from fastapi import FastAPI

from mesa_memory.api import server
from mesa_memory.config import config, configured_embedding_identity
from mesa_workers.projection_worker import process_projection_outbox_once


@pytest.mark.asyncio
async def test_external_embedding_server_lifespan_composes_factory_and_persists_vectors(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = tmp_path / "external-runtime"
    original_config = {
        field: getattr(config, field)
        for field in (
            "external_provider_enabled",
            "embedding_provider",
            "external_embedding_model",
            "embedding_dimension",
            "embedding_version",
            "embedding_model_revision",
            "embedding_normalized",
        )
    }
    monkeypatch.setenv("MESA_RUNTIME_PROFILE", "combined")
    monkeypatch.setenv("MESA_STORAGE_ROOT", str(storage))
    monkeypatch.setenv("MESA_LOAD_DOTENV", "false")
    monkeypatch.setenv("MESA_MODEL_ENABLED", "false")
    monkeypatch.setenv("MESA_EXTERNAL_PROVIDER_ENABLED", "true")
    monkeypatch.setenv("MESA_EMBEDDING_PROVIDER", "openai_compatible")
    monkeypatch.setenv("MESA_EXTERNAL_EMBEDDING_MODEL", "external-test-model")
    monkeypatch.setenv("MESA_EMBEDDING_DIMENSION", "4")
    monkeypatch.setenv("MESA_EMBEDDING_VERSION", "v9")
    monkeypatch.setenv("MESA_EMBEDDING_MODEL_REVISION", "revision-42")
    monkeypatch.setenv("MESA_EMBEDDING_NORMALIZED", "true")
    monkeypatch.setenv("LLM_API_KEY", "test-only-key")
    monkeypatch.setenv("MESA_API_KEY", "server-key")
    monkeypatch.setenv("MESA_PRINCIPAL_ID", "server-principal")
    monkeypatch.setenv("MESA_PRINCIPAL_STATUS", "active")
    calls: list[tuple[str, str]] = []

    class FakeExternalTransport:
        """Fake only the HTTP/provider boundary; runtime composition is real."""

        def __init__(self, identity) -> None:
            self.identity = identity
            calls.append(("construct", identity.model))

        def embed_document(self, text: str) -> list[float]:
            calls.append(("document", text))
            return [1.0, 0.0, 0.0, 0.0]

        async def aembed_document(self, text: str) -> list[float]:
            calls.append(("document", text))
            return [1.0, 0.0, 0.0, 0.0]

        def embed_query(self, text: str) -> list[float]:
            calls.append(("query", text))
            return [1.0, 0.0, 0.0, 0.0]

        async def aembed_query(self, text: str) -> list[float]:
            calls.append(("query", text))
            return [1.0, 0.0, 0.0, 0.0]

    monkeypatch.setattr(
        "mesa_memory.embedding.service._OpenAICompatibleEmbeddingBackend",
        FakeExternalTransport,
    )

    async with server.lifespan(FastAPI()):
        dao = server.state.dao
        await dao.ensure_v4_catalog_scope(
            tenant_id="tenant-external", workspace_id="workspace", dataset_id="dataset"
        )
        session = await dao.create_v4_session(
            tenant_id="tenant-external",
            workspace_id="workspace",
            dataset_ids=["dataset"],
            agent_id="agent-external",
            principal_id="server-principal",
        )
        identity = configured_embedding_identity()
        assert identity.provider == "openai_compatible"
        assert identity.model == "external-test-model"
        admitted = await dao.admit_v4_memory(
            tenant_id="tenant-external",
            workspace_id="workspace",
            dataset_id="dataset",
            agent_id="agent-external",
            session_id=session["session_id"],
            document_id="document",
            revision_id="revision",
            chunk_id="chunk",
            title="External embedding",
            content_payload="MESA uses external embeddings.",
            source_ref="test",
            evidence_span="MESA uses external embeddings.",
            revision_number=1,
            chunk_ordinal=0,
            supersedes_revision_id=None,
            metadata={},
            embedding_provider=identity.provider,
            embedding_model=identity.model,
            embedding_version=identity.version,
            embedding_dimension=identity.dimension,
            embedding_space_id=identity.embedding_space_id,
            embedding_model_revision=identity.model_revision,
            embedding_normalized=identity.normalized,
            validation_mode=0,
            policy=server.config.queue_admission_policy,
        )
        mutation_id = admitted["response"]["mutation_id"]
        await dao.record_mutation_extraction(
            "agent-external",
            mutation_id,
            [
                {
                    "head": "MESA",
                    "relation": "USES",
                    "tail": "external embeddings",
                    "fact_text": "MESA uses external embeddings.",
                    "source_span": "MESA uses external embeddings.",
                }
            ],
        )
        await dao.set_mutation_state("agent-external", mutation_id, "VALIDATED")
        for _ in range(3):
            assert (await process_projection_outbox_once(dao))["completed"] == 1
        results = await dao.search_v4_memory(
            tenant_id="tenant-external",
            agent_id="agent-external",
            dataset_ids=["dataset"],
            query="external embedding query",
        )
        assert results
        assert ("construct", "external-test-model") in calls
        assert ("document", "MESA USES external embeddings") in calls
        assert ("query", "external embedding query") in calls
    for field, value in original_config.items():
        object.__setattr__(config, field, value)
