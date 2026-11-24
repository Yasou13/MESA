"""Real SQLite/LanceDB proof for the V4 outbox projector."""

from unittest.mock import AsyncMock, patch

import pytest

from mesa_memory.consolidation.schemas import MemoryCandidate
from mesa_memory.embedding.service import EmbeddingIdentity, EmbeddingService
from mesa_storage.dao import MemoryDAO
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.vector_engine import VectorEngine
from mesa_workers.projection_worker import (
    process_artifact_cleanup_once,
    process_projection_outbox_once,
)


@pytest.mark.asyncio
async def test_real_outbox_projects_sql_and_vector(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("mesa_storage.dao._retry_delay_seconds", lambda _attempt: 0)
    sql = AsyncEngine(str(tmp_path / "mesa.db"))
    identity = EmbeddingIdentity(
        provider="mock", model="projection-test", version="v1", dimension=8
    )
    vector = VectorEngine(
        str(tmp_path / "vectors.lance"),
        max_workers=1,
        embedding_service=EmbeddingService(identity=identity),
    )
    await sql.initialize()
    await initialize_schema(sql)
    await vector.initialize()
    dao = MemoryDAO(sqlite_engine=sql, vector_engine=vector)
    await dao.create_v4_workspace(
        tenant_id="tenant-a",
        workspace_id="__legacy__",
        workspace_name="Legacy integration scope",
    )
    await dao.ensure_v4_catalog_scope(
        tenant_id="tenant-a",
        workspace_id="__legacy__",
        dataset_id="__legacy__",
    )
    candidate = MemoryCandidate.from_raw_log(
        raw_log_id=101,
        agent_id="tenant-a",
        session_id="session-a",
        content_payload="Alice knows Bob.",
        embedding_provider=identity.provider,
        embedding_model=identity.model,
        embedding_version=identity.version,
        embedding_dimension=identity.dimension,
    ).as_consolidation_record()
    try:
        await dao.record_mutation(candidate, raw_log_id=101)
        await dao.record_mutation_extraction(
            "tenant-a",
            candidate["mutation_id"],
            [{"head": "Alice", "relation": "KNOWS", "tail": "Bob"}],
        )
        await dao.set_mutation_state("tenant-a", candidate["mutation_id"], "VALIDATED")
        with patch.object(
            VectorEngine, "compute_embedding", new=AsyncMock(return_value=[0.1] * 8)
        ):
            result = {
                "claimed": 0,
                "completed": 0,
                "retry_pending": 0,
                "dead_letter": 0,
            }
            for _ in range(2):
                single = await process_projection_outbox_once(
                    dao, worker_id="projector-a"
                )
                for key in result:
                    result[key] += single[key]

        assert result == {
            "claimed": 2,
            "completed": 2,
            "retry_pending": 0,
            "dead_letter": 0,
        }
        mutation = await dao.get_mutation("tenant-a", candidate["mutation_id"])
        assert mutation is not None and mutation["state"] == "COMMITTED"
        vector_ids = await vector.get_active_node_ids("tenant-a")
        assert len(vector_ids) == 1
        assert await dao.reconcile_v4_projection_parity() == {
            "checked_artifacts": 4,
            "missing_artifacts": 0,
            "missing_sql": 0,
            "missing_vector": 0,
            "requeued_lanes": 0,
        }

        await vector.hard_delete(sorted(vector_ids)[0], "tenant-a")
        repaired = await dao.reconcile_v4_projection_parity(repair=True)
        assert repaired["missing_artifacts"] == 1
        assert repaired["missing_vector"] == 1
        assert repaired["requeued_lanes"] == 1
        mutation = await dao.get_mutation("tenant-a", candidate["mutation_id"])
        assert mutation is not None and mutation["state"] == "RETRY_PENDING"
        health = await dao.health_check()
        assert health["v4_projection"]["backlog"] == 1

        await vector.upsert(
            node_id="orphan-vector",
            agent_id="tenant-a",
            embedding=[0.2] * 8,
            content_hash="orphan",
        )
        orphan_report = await dao.reconcile_v4_bidirectional(
            tenant_id="tenant-a",
            agent_id="tenant-a",
            dataset_ids=["__legacy__"],
            repair=True,
        )
        assert orphan_report["physical_orphans"] == 1
        assert orphan_report["cleanup_enqueued"] == 1
        cleanup = await process_artifact_cleanup_once(
            dao, worker_id="reconciler-cleanup", limit=2
        )
        assert cleanup["completed"] == 1
        assert "orphan-vector" not in await vector.get_active_node_ids("tenant-a")
    finally:
        await vector.close()
        await sql.close()


@pytest.mark.asyncio
async def test_integrated_canonical_fact_retrieval_smoke(tmp_path) -> None:
    """Supported V4 retrieval ranks fact vectors, not entity-name vectors."""
    sql = AsyncEngine(str(tmp_path / "retrieval.sqlite"))
    identity = EmbeddingIdentity(
        provider="test", model="keyword", version="v1", dimension=10
    )
    keywords = [
        "dark",
        "postgres",
        "calendar",
        "turkish",
        "docker",
        "french",
        "istanbul",
        "weekly",
        "redis",
        "vegan",
    ]

    def provider(text: str) -> list[float]:
        folded = text.casefold()
        return [1.0 if word in folded else 0.0 for word in keywords]

    vector = VectorEngine(
        str(tmp_path / "vectors.lance"),
        max_workers=1,
        embedding_service=EmbeddingService(identity=identity, provider_fn=provider),
    )
    await sql.initialize()
    await initialize_schema(sql)
    await vector.initialize()
    dao = MemoryDAO(sqlite_engine=sql, vector_engine=vector)
    cases = [
        ("ThemePreference", "PREFERS", "dark", "dark tema", "dark"),
        ("DatabaseConfig", "USES", "postgres", "postgres yapılandırması", "postgres"),
        ("CalendarPlan", "USES", "calendar", "calendar tercihi", "calendar"),
        ("LanguageChoice", "USES", "turkish", "turkish ayarı", "turkish"),
        ("DeploymentConfig", "USES", "docker", "docker yapılandırması", "docker"),
        ("LanguageCorrection", "USES", "french", "french düzeltmesi", "french"),
        ("LocationFact", "LOCATED_IN", "istanbul", "istanbul konumu", "istanbul"),
        ("ScheduleFact", "RUNS", "weekly", "weekly zamanlama", "weekly"),
        ("CacheConfig", "USES", "redis", "redis yapılandırması", "redis"),
        ("DietPreference", "PREFERS", "vegan", "vegan tercihi", "vegan"),
    ]
    try:
        await dao.ensure_v4_catalog_scope(
            tenant_id="tenant-a", workspace_id="workspace-a", dataset_id="dataset-a"
        )
        for raw_log_id, (
            subject,
            predicate,
            object_value,
            fact_text,
            _query,
        ) in enumerate(cases, start=1):
            candidate = MemoryCandidate.from_raw_log(
                raw_log_id=raw_log_id,
                tenant_id="tenant-a",
                workspace_id="workspace-a",
                dataset_id="dataset-a",
                document_id=f"document-{raw_log_id}",
                revision_id=f"revision-{raw_log_id}",
                chunk_id=f"chunk-{raw_log_id}",
                source_ref=f"source-{raw_log_id}",
                agent_id="agent-a",
                session_id="session-a",
                content_payload=fact_text,
                embedding_provider=identity.provider,
                embedding_model=identity.model,
                embedding_version=identity.version,
                embedding_dimension=identity.dimension,
                validation_mode=0,
            ).as_consolidation_record()
            await dao.record_mutation(candidate, raw_log_id=raw_log_id)
            await dao.record_mutation_extraction(
                "agent-a",
                candidate["mutation_id"],
                [
                    {
                        "head": subject,
                        "relation": predicate,
                        "tail": object_value,
                        "fact_text": fact_text,
                        "source_span": fact_text,
                    }
                ],
            )
            await dao.set_mutation_state(
                "agent-a", candidate["mutation_id"], "VALIDATED"
            )
            for _ in range(2):
                assert (await process_projection_outbox_once(dao))["completed"] == 1

        for subject, _predicate, _object, _fact_text, query in cases:
            results = await dao.search_v4_memory(
                tenant_id="tenant-a",
                agent_id="agent-a",
                dataset_ids=["dataset-a"],
                query=query,
                limit=3,
            )
            assert any(
                result["entity"]["canonical_name"] == subject for result in results[:3]
            )
    finally:
        await vector.close()
        await sql.close()
