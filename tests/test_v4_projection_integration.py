"""Real SQLite/LanceDB/Kùzu proof for the V4 outbox projector."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from mesa_memory.consolidation.schemas import MemoryCandidate
from mesa_memory.embedding.service import EmbeddingIdentity, EmbeddingService
from mesa_storage.dao import MemoryDAO
from mesa_storage.kuzu_provider import KuzuGraphProvider
from mesa_storage.kuzu_setup import initialize_schema_artifact
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.vector_engine import VectorEngine
from mesa_workers.projection_worker import (
    process_artifact_cleanup_once,
    process_projection_outbox_once,
)


@pytest.mark.asyncio
async def test_real_outbox_projects_sql_vector_and_graph_v2(tmp_path) -> None:
    sql = AsyncEngine(str(tmp_path / "mesa.db"))
    identity = EmbeddingIdentity(
        provider="mock", model="projection-test", version="v1", dimension=8
    )
    vector = VectorEngine(
        str(tmp_path / "vectors.lance"),
        max_workers=1,
        embedding_service=EmbeddingService(identity=identity),
    )
    graph_path = tmp_path / "graph"
    await sql.initialize()
    await initialize_schema(sql)
    await vector.initialize()
    initialize_schema_artifact(str(graph_path))
    graph = KuzuGraphProvider(str(graph_path), max_workers=1)
    await graph.initialize()
    dao = MemoryDAO(sqlite_engine=sql, vector_engine=vector, graph_provider=graph)
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
            for _ in range(3):
                single = await process_projection_outbox_once(
                    dao, worker_id="projector-a"
                )
                for key in result:
                    result[key] += single[key]

        assert result == {
            "claimed": 3,
            "completed": 3,
            "retry_pending": 0,
            "dead_letter": 0,
        }
        mutation = await dao.get_mutation("tenant-a", candidate["mutation_id"])
        assert mutation is not None and mutation["state"] == "COMMITTED"
        vector_ids = await vector.get_active_node_ids("tenant-a")
        assert len(vector_ids) == 1
        assertions = await graph.execute_query(
            "MATCH (a:Assertion {agent_id: $agent_id}) RETURN a.id",
            {"agent_id": "tenant-a"},
        )
        assert len(assertions) == 1
        assert await dao.reconcile_v4_projection_parity() == {
            "checked_artifacts": 7,
            "missing_artifacts": 0,
            "missing_sql": 0,
            "missing_vector": 0,
            "missing_graph": 0,
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
        await graph.insert_node(
            node_id="orphan-graph",
            name="Orphan",
            agent_id="tenant-a",
        )
        orphan_report = await dao.reconcile_v4_bidirectional(
            tenant_id="tenant-a",
            agent_id="tenant-a",
            dataset_ids=["__legacy__"],
            repair=True,
        )
        assert orphan_report["physical_orphans"] == 2
        assert orphan_report["cleanup_enqueued"] == 2
        cleanup = await process_artifact_cleanup_once(
            dao, worker_id="reconciler-cleanup", limit=2
        )
        assert cleanup["completed"] == 2
        assert "orphan-vector" not in await vector.get_active_node_ids("tenant-a")
        assert await graph.verify_nodes_absent(
            agent_id="tenant-a", node_ids=["orphan-graph"]
        )
    finally:
        await graph.close()
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
    graph = SimpleNamespace(
        insert_node=AsyncMock(),
        insert_assertion=AsyncMock(),
        link_assertions=AsyncMock(),
        delete_assertions=AsyncMock(),
        delete_nodes=AsyncMock(),
    )
    await sql.initialize()
    await initialize_schema(sql)
    await vector.initialize()
    dao = MemoryDAO(sqlite_engine=sql, vector_engine=vector, graph_provider=graph)
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
            for _ in range(3):
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
