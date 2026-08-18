"""D008: deterministic full-cognitive composition proof.

Only the model/provider boundary is faked.  Runtime startup, durable
admission, cold-path consolidation, projection, retrieval and context all use
the production combined-runtime composition.
"""

import asyncio
import threading
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI

from mesa_memory.adapter.base import BaseUniversalLLMAdapter
from mesa_memory.api import server
from mesa_memory.config import configured_embedding_identity
from mesa_memory.context_builder import ContextBuilder
from mesa_memory.embedding.service import EmbeddingIdentity, EmbeddingService
from mesa_storage.vector_engine import VectorEngine


class _DeterministicProvider(BaseUniversalLLMAdapter):
    """A fake external adapter with a real adapter-factory interface."""

    def __init__(self, model_name: str = "terra-deterministic-provider") -> None:
        self.model_name = model_name
        self.completions = 0
        self.embeddings = 0

    def complete(self, prompt: str, schema: Any = None, **_: Any) -> Any:
        self.completions += 1
        if schema is not None:
            source_span = prompt.rsplit("<UNTRUSTED_SOURCE>\n", 1)[-1].split(
                "\n</UNTRUSTED_SOURCE>", 1
            )[0]
            return schema.model_validate(
                {
                    "facts": [
                        {
                            "fact_text": "MESA preserves durable memory.",
                            "subject": "MESA",
                            "predicate": "PRESERVES",
                            "object": "durable memory",
                            "confidence": 1.0,
                            "source_span": source_span,
                        }
                    ]
                }
            )
        return '{"decision":"STORE","justification":"deterministic test provider"}'

    async def acomplete(self, prompt: str, schema: Any = None, **kwargs: Any) -> Any:
        return self.complete(prompt, schema, **kwargs)

    def embed(self, text: str, **_: Any) -> list[float]:
        self.embeddings += 1
        return [1.0] + [0.0] * 383

    async def aembed(self, text: str, **kwargs: Any) -> list[float]:
        return self.embed(text, **kwargs)

    def embed_batch(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        return [self.embed(text, **kwargs) for text in texts]

    async def aembed_batch(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        return self.embed_batch(texts, **kwargs)

    def get_token_count(self, text: str) -> int:
        return len(text.split())


def _embedding_service(provider: _DeterministicProvider) -> EmbeddingService:
    configured = configured_embedding_identity()
    return EmbeddingService(
        identity=EmbeddingIdentity(
            provider=configured.provider,
            model=configured.model,
            dimension=configured.dimension,
            version=configured.version,
            normalized=configured.normalized,
            model_revision=configured.model_revision,
        ),
        provider_fn=provider.embed,
        allow_model_loading=False,
        external_enabled=True,
    )


async def _wait_for_committed(dao: Any, mutation_id: str) -> dict[str, Any]:
    summary: dict[str, Any] | None = None
    for _ in range(100):
        summary = await dao.get_mutation_summary(mutation_id)
        if summary and summary["state"] == "COMMITTED":
            return summary
        await asyncio.sleep(0.1)
    raise AssertionError(
        f"combined model-enabled runtime did not commit the mutation: {summary!r}"
    )


@pytest.mark.asyncio
async def test_d008_model_enabled_combined_runtime_survives_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = tmp_path / "runtime-storage"
    provider = _DeterministicProvider()
    validator_a = _DeterministicProvider("terra-validator-a")
    validator_b = _DeterministicProvider("terra-validator-b")
    monkeypatch.setenv("MESA_RUNTIME_PROFILE", "combined")
    monkeypatch.setenv("MESA_STORAGE_ROOT", str(storage))
    monkeypatch.setenv("MESA_LOAD_DOTENV", "false")
    monkeypatch.setenv("MESA_MODEL_ENABLED", "true")
    monkeypatch.setenv("MESA_EXTERNAL_PROVIDER_ENABLED", "true")
    monkeypatch.setenv("MESA_TIER3_MODE", "2")
    monkeypatch.setenv("MESA_TIER3_LLM_PROVIDER_A", "mock")
    monkeypatch.setenv("MESA_TIER3_LLM_MODEL_A", "terra-validator-a")
    monkeypatch.setenv("MESA_TIER3_LLM_PROVIDER_B", "mock")
    monkeypatch.setenv("MESA_TIER3_LLM_MODEL_B", "terra-validator-b")
    monkeypatch.setenv("MESA_EMBEDDING_DIMENSION", "384")
    monkeypatch.setenv("MESA_LLM_PROVIDER", "mock")
    monkeypatch.setenv("MESA_API_KEY", "d008-test-key")
    monkeypatch.setenv("MESA_PRINCIPAL_ID", "d008-principal")
    monkeypatch.setenv("MESA_PRINCIPAL_STATUS", "active")

    def provider_boundary(provider_name=None, *, model_name=None, thinking=None):
        _ = thinking
        if model_name == "terra-validator-a":
            return validator_a
        if model_name == "terra-validator-b":
            return validator_b
        return provider

    monkeypatch.setattr(
        server.AdapterFactory, "get_adapter", staticmethod(provider_boundary)
    )
    monkeypatch.setattr(
        server,
        "_get_embedding_service",
        lambda **_kwargs: _embedding_service(provider),
    )

    # The REBEL provider boundary is deliberately unavailable: real
    # consolidation must exercise its LLM adapter fallback without a download.
    from mesa_memory.extraction import rebel_pipeline

    rebel_pipeline._model_holder.reset()
    monkeypatch.setattr(
        rebel_pipeline,
        "pipeline",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("fake REBEL unavailable")
        ),
    )

    async with server.lifespan(FastAPI()):
        assert server.state.is_ready is True
        assert "tier3-deferred" in server.state.worker_supervisor.readiness()["workers"]
        dao = server.state.dao
        await dao.ensure_v4_catalog_scope(
            tenant_id="tenant-d008", workspace_id="default", dataset_id="main"
        )
        session = await dao.create_v4_session(
            tenant_id="tenant-d008",
            workspace_id="default",
            dataset_ids=["main"],
            agent_id="agent-d008",
            principal_id="d008-principal",
        )
        embedding_identity = configured_embedding_identity()
        admitted = await dao.admit_v4_memory(
            tenant_id="tenant-d008",
            workspace_id="default",
            dataset_id="main",
            agent_id="agent-d008",
            session_id=session["session_id"],
            document_id="doc-1",
            revision_id="rev-1",
            chunk_id="chunk-1",
            title="Durable memory",
            content_payload="MESA preserves durable memory across restarts.",
            source_ref="d008-test",
            evidence_span="0:45",
            revision_number=1,
            chunk_ordinal=0,
            supersedes_revision_id=None,
            metadata={"memory_type": "decision", "importance": 0.9},
            embedding_provider=embedding_identity.provider,
            embedding_model=embedding_identity.model,
            embedding_version=embedding_identity.version,
            embedding_dimension=embedding_identity.dimension,
            validation_mode=2,
            policy=server.config.queue_admission_policy,
        )
        mutation_id = admitted["response"]["mutation_id"]
        summary = await _wait_for_committed(dao, mutation_id)
        pipeline = await dao.get_pipeline_run(str(summary["pipeline_run_id"]))
        assert pipeline is not None and pipeline["state"] == "COMMITTED"
        recall = await dao.search_v4_memory(
            tenant_id="tenant-d008",
            agent_id="agent-d008",
            dataset_ids=["main"],
            query="durable memory",
        )
        assert recall
        context = await ContextBuilder(dao).build_context(
            tenant_id="tenant-d008",
            agent_id="agent-d008",
            dataset_ids=["main"],
            query="durable memory",
            session_id=session["session_id"],
        )
        assert context["canonical_memories"]

    async with server.lifespan(FastAPI()):
        assert server.state.is_ready is True
        recall_after_restart = await server.state.dao.search_v4_memory(
            tenant_id="tenant-d008",
            agent_id="agent-d008",
            dataset_ids=["main"],
            query="durable memory",
        )
        assert recall_after_restart

    assert validator_a.completions == 1
    assert validator_b.completions == 1
    assert provider.completions > 0
    assert provider.embeddings > 0


@pytest.mark.asyncio
async def test_r4_mode_zero_combined_runtime_has_no_validator_dependency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = tmp_path / "mode-zero-runtime-storage"
    provider = _DeterministicProvider()
    monkeypatch.setenv("MESA_RUNTIME_PROFILE", "combined")
    monkeypatch.setenv("MESA_STORAGE_ROOT", str(storage))
    monkeypatch.setenv("MESA_LOAD_DOTENV", "false")
    monkeypatch.setenv("MESA_MODEL_ENABLED", "true")
    monkeypatch.setenv("MESA_EXTERNAL_PROVIDER_ENABLED", "true")
    monkeypatch.setenv("MESA_TIER3_MODE", "0")
    monkeypatch.setenv("MESA_REBEL_ENABLED", "false")
    monkeypatch.setenv("MESA_EMBEDDING_DIMENSION", "384")
    monkeypatch.setenv("MESA_API_KEY", "r4-mode-zero-key")
    monkeypatch.setenv("MESA_PRINCIPAL_ID", "r4-mode-zero-principal")
    monkeypatch.setenv("MESA_PRINCIPAL_STATUS", "active")
    monkeypatch.setattr(
        server.AdapterFactory,
        "get_adapter",
        staticmethod(lambda *args, **kwargs: provider),
    )
    monkeypatch.setattr(
        server,
        "_get_embedding_service",
        lambda **_kwargs: _embedding_service(provider),
    )

    async with server.lifespan(FastAPI()):
        loop = server.state.consolidation_loop
        assert loop.validation_policy.mode == 0
        assert loop.validation_policy.validator_count == 0
        assert loop.validation_policy.llm_validation_enabled is False
        assert (
            "tier3-deferred"
            not in server.state.worker_supervisor.readiness()["workers"]
        )
        dao = server.state.dao
        await dao.ensure_v4_catalog_scope(
            tenant_id="tenant-r4-0", workspace_id="default", dataset_id="main"
        )
        session = await dao.create_v4_session(
            tenant_id="tenant-r4-0",
            workspace_id="default",
            dataset_ids=["main"],
            agent_id="agent-r4-0",
            principal_id="r4-mode-zero-principal",
        )
        embedding_identity = configured_embedding_identity()
        admitted = await dao.admit_v4_memory(
            tenant_id="tenant-r4-0",
            workspace_id="default",
            dataset_id="main",
            agent_id="agent-r4-0",
            session_id=session["session_id"],
            document_id="doc-0",
            revision_id="rev-0",
            chunk_id="chunk-0",
            title="Mode zero durable memory",
            content_payload="Mode zero preserves extraction and durable recall.",
            source_ref="r4-mode-zero-test",
            evidence_span="0:52",
            revision_number=1,
            chunk_ordinal=0,
            supersedes_revision_id=None,
            metadata={"memory_type": "decision", "importance": 0.9},
            embedding_provider=embedding_identity.provider,
            embedding_model=embedding_identity.model,
            embedding_version=embedding_identity.version,
            embedding_dimension=embedding_identity.dimension,
            validation_mode=0,
            policy=server.config.queue_admission_policy,
        )
        summary = await _wait_for_committed(dao, admitted["response"]["mutation_id"])
        assert summary["tier3_audit"]["decisions"]["primary"] == "SKIPPED_BY_POLICY"
        recall = await dao.search_v4_memory(
            tenant_id="tenant-r4-0",
            agent_id="agent-r4-0",
            dataset_ids=["main"],
            query="durable recall",
        )
        assert recall

    async with server.lifespan(FastAPI()):
        recall_after_restart = await server.state.dao.search_v4_memory(
            tenant_id="tenant-r4-0",
            agent_id="agent-r4-0",
            dataset_ids=["main"],
            query="durable recall",
        )
        assert recall_after_restart

    assert provider.completions > 0
    assert provider.embeddings > 0


@pytest.mark.asyncio
async def test_r4_mode_one_combined_runtime_uses_only_validator_a(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = tmp_path / "mode-one-runtime-storage"
    provider = _DeterministicProvider()
    validator_a = _DeterministicProvider("terra-validator-a")
    monkeypatch.setenv("MESA_RUNTIME_PROFILE", "combined")
    monkeypatch.setenv("MESA_STORAGE_ROOT", str(storage))
    monkeypatch.setenv("MESA_LOAD_DOTENV", "false")
    monkeypatch.setenv("MESA_MODEL_ENABLED", "true")
    monkeypatch.setenv("MESA_EXTERNAL_PROVIDER_ENABLED", "true")
    monkeypatch.setenv("MESA_TIER3_MODE", "1")
    monkeypatch.setenv("MESA_TIER3_LLM_PROVIDER_A", "mock")
    monkeypatch.setenv("MESA_TIER3_LLM_MODEL_A", "terra-validator-a")
    monkeypatch.delenv("MESA_TIER3_LLM_PROVIDER_B", raising=False)
    monkeypatch.delenv("MESA_TIER3_LLM_MODEL_B", raising=False)
    monkeypatch.setenv("MESA_REBEL_ENABLED", "false")
    monkeypatch.setenv("MESA_EMBEDDING_DIMENSION", "384")
    monkeypatch.setenv("MESA_API_KEY", "r4-mode-one-key")
    monkeypatch.setenv("MESA_PRINCIPAL_ID", "r4-mode-one-principal")
    monkeypatch.setenv("MESA_PRINCIPAL_STATUS", "active")

    def provider_boundary(provider_name=None, *, model_name=None, thinking=None):
        _ = thinking
        return validator_a if model_name == "terra-validator-a" else provider

    monkeypatch.setattr(
        server.AdapterFactory, "get_adapter", staticmethod(provider_boundary)
    )
    monkeypatch.setattr(
        server,
        "_get_embedding_service",
        lambda **_kwargs: _embedding_service(provider),
    )

    async with server.lifespan(FastAPI()):
        loop = server.state.consolidation_loop
        assert loop.validation_policy.mode == 1
        assert loop.validation_policy.validator_count == 1
        assert "tier3-deferred" in server.state.worker_supervisor.readiness()["workers"]
        dao = server.state.dao
        await dao.ensure_v4_catalog_scope(
            tenant_id="tenant-r4-1", workspace_id="default", dataset_id="main"
        )
        session = await dao.create_v4_session(
            tenant_id="tenant-r4-1",
            workspace_id="default",
            dataset_ids=["main"],
            agent_id="agent-r4-1",
            principal_id="r4-mode-one-principal",
        )
        embedding_identity = configured_embedding_identity()
        admitted = await dao.admit_v4_memory(
            tenant_id="tenant-r4-1",
            workspace_id="default",
            dataset_id="main",
            agent_id="agent-r4-1",
            session_id=session["session_id"],
            document_id="doc-1",
            revision_id="rev-1",
            chunk_id="chunk-1",
            title="Mode one durable memory",
            content_payload="Mode one validates with only validator A.",
            source_ref="r4-mode-one-test",
            evidence_span="0:42",
            revision_number=1,
            chunk_ordinal=0,
            supersedes_revision_id=None,
            metadata={"memory_type": "decision", "importance": 0.9},
            embedding_provider=embedding_identity.provider,
            embedding_model=embedding_identity.model,
            embedding_version=embedding_identity.version,
            embedding_dimension=embedding_identity.dimension,
            validation_mode=1,
            policy=server.config.queue_admission_policy,
        )
        summary = await _wait_for_committed(dao, admitted["response"]["mutation_id"])
        assert summary["tier3_audit"]["decisions"] == {
            "primary": "STORE",
            "secondary": "NOT_RUN",
        }

    assert validator_a.completions == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "provider_a", "model_a", "provider_b", "model_b"),
    [
        (1, None, None, None, None),
        (2, "mock", "validator-a", None, None),
        (2, "mock", "same", "mock", "same"),
    ],
)
async def test_r4_invalid_validation_composition_fails_server_startup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: int,
    provider_a: str | None,
    model_a: str | None,
    provider_b: str | None,
    model_b: str | None,
) -> None:
    storage = tmp_path / f"invalid-mode-{mode}-{model_a}-{model_b}"
    provider = _DeterministicProvider()
    monkeypatch.setenv("MESA_RUNTIME_PROFILE", "combined")
    monkeypatch.setenv("MESA_STORAGE_ROOT", str(storage))
    monkeypatch.setenv("MESA_LOAD_DOTENV", "false")
    monkeypatch.setenv("MESA_MODEL_ENABLED", "true")
    monkeypatch.setenv("MESA_EXTERNAL_PROVIDER_ENABLED", "true")
    monkeypatch.setenv("MESA_TIER3_MODE", str(mode))
    monkeypatch.setenv("MESA_API_KEY", "r4-invalid-composition-key")
    monkeypatch.setenv("MESA_PRINCIPAL_ID", "r4-invalid-composition-principal")
    monkeypatch.setenv("MESA_PRINCIPAL_STATUS", "active")
    for name, value in (
        ("MESA_TIER3_LLM_PROVIDER_A", provider_a),
        ("MESA_TIER3_LLM_MODEL_A", model_a),
        ("MESA_TIER3_LLM_PROVIDER_B", provider_b),
        ("MESA_TIER3_LLM_MODEL_B", model_b),
    ):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    monkeypatch.setattr(
        server.AdapterFactory,
        "get_adapter",
        staticmethod(lambda *args, **kwargs: provider),
    )
    monkeypatch.setattr(
        server,
        "_get_embedding_service",
        lambda **_kwargs: _embedding_service(provider),
    )

    with pytest.raises(ValueError):
        async with server.lifespan(FastAPI()):
            raise AssertionError("invalid validation composition reached READY")


@pytest.mark.asyncio
async def test_lancedb_background_loop_is_one_bounded_daemon_after_restarts(
    tmp_path: Path,
) -> None:
    for index in range(3):
        engine = VectorEngine(str(tmp_path / f"lance-restart-{index}"))
        await engine.initialize()
        await engine.close()

    threads = [
        thread
        for thread in threading.enumerate()
        if thread.name == "LanceDBBackgroundEventLoop" and thread.is_alive()
    ]

    assert len(threads) == 1
    assert threads[0].daemon is True
