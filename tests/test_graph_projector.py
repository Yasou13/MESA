"""Tests for the production canonical GraphProjector boundary."""

from unittest.mock import AsyncMock, MagicMock
from types import SimpleNamespace
import asyncio
import pytest

from mesa_memory.consolidation.schemas import MemoryCandidate
from mesa_memory.embedding.service import EmbeddingIdentity
from mesa_memory.graph.projector import GraphProjectionError, GraphProjector
from mesa_storage.dao import MemoryDAO
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_workers.projection_worker import process_projection_outbox_once


@pytest.mark.asyncio
async def test_graph_projector_projects_only_durable_canonical_assertions():
    dao = MagicMock()
    dao.project_v4_graph_triplet = AsyncMock(return_value="assertion-1")
    projector = GraphProjector(dao=dao)
    mutation = {"mutation_id": "mutation-1"}
    triplet = {"head": "Alice", "relation": "WORKS_AT", "tail": "Acme"}

    result = await projector.project_triplet(mutation=mutation, triplet=triplet)

    assert result == "assertion-1"
    dao.project_v4_graph_triplet.assert_awaited_once_with(
        mutation=mutation, triplet=triplet
    )


@pytest.mark.asyncio
async def test_graph_projector_rejects_noncanonical_input():
    dao = MagicMock()
    projector = GraphProjector(dao=dao)
    with pytest.raises(GraphProjectionError):
        await projector.project_triplet(
            mutation={}, triplet={"head": "Alice", "relation": "KNOWS"}
        )


@pytest.mark.asyncio
async def test_graph_failure_preserves_canonical_sql_assertion_for_retry(tmp_path):
    engine = AsyncEngine(str(tmp_path / "graph-failure.sqlite"))
    await asyncio.wait_for(engine.initialize(), timeout=5)
    await asyncio.wait_for(initialize_schema(engine), timeout=5)
    identity = EmbeddingIdentity(
        provider="mock", model="canonical", version="v1", dimension=4
    )
    vector = SimpleNamespace(
        embedding_identity=identity,
        compute_embedding=AsyncMock(return_value=[0.5] * 4),
        upsert=AsyncMock(),
        hard_delete=AsyncMock(),
    )
    graph = SimpleNamespace(
        insert_node=AsyncMock(),
        insert_assertion=AsyncMock(side_effect=RuntimeError("kuzu unavailable")),
        link_assertions=AsyncMock(),
        delete_assertions=AsyncMock(),
        delete_nodes=AsyncMock(),
    )
    dao = MemoryDAO(engine, vector, graph_provider=graph)
    candidate = MemoryCandidate.from_raw_log(
        raw_log_id=1201,
        agent_id="tenant-a",
        session_id="session-a",
        content_payload="Alice knows Bob.",
        embedding_provider=identity.provider,
        embedding_model=identity.model,
        embedding_version=identity.version,
        embedding_dimension=identity.dimension,
        validation_mode=0,
    ).as_consolidation_record()
    try:
        await dao.record_mutation(candidate, raw_log_id=1201)
        await dao.record_mutation_extraction(
            "tenant-a",
            candidate["mutation_id"],
            [
                {
                    "head": "Alice",
                    "relation": "KNOWS",
                    "tail": "Bob",
                    "fact_text": "Alice knows Bob.",
                    "valid_from": "2026-01-01",
                    "source_span": "Alice knows Bob.",
                }
            ],
        )
        await dao.set_mutation_state("tenant-a", candidate["mutation_id"], "VALIDATED")

        assert (await asyncio.wait_for(process_projection_outbox_once(dao), timeout=5))[
            "completed"
        ] == 1
        assert (await asyncio.wait_for(process_projection_outbox_once(dao), timeout=5))[
            "completed"
        ] == 1
        graph_result = await asyncio.wait_for(
            process_projection_outbox_once(dao), timeout=5
        )

        assert graph_result["retry_pending"] == 1
        async with engine.connection() as db:
            async with db.execute(
                "SELECT valid_from, evidence_span FROM v4_assertions "
                "WHERE mutation_id = ?",
                (candidate["mutation_id"],),
            ) as cursor:
                assertion = await cursor.fetchone()
        assert assertion is not None
        assert tuple(assertion) == ("2026-01-01", "Alice knows Bob.")
        mutation = await dao.get_mutation("tenant-a", candidate["mutation_id"])
        assert mutation is not None and mutation["state"] == "RETRY_PENDING"
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_fact_level_supersession_changes_current_truth_and_rolls_back(tmp_path):
    engine = AsyncEngine(str(tmp_path / "fact-supersession.sqlite"))
    await engine.initialize()
    await initialize_schema(engine)
    identity = EmbeddingIdentity(
        provider="mock", model="canonical", version="v1", dimension=4
    )
    vector = SimpleNamespace(
        embedding_identity=identity,
        compute_embedding=AsyncMock(return_value=[0.5] * 4),
        upsert=AsyncMock(),
        hard_delete=AsyncMock(),
    )
    graph = SimpleNamespace(
        insert_node=AsyncMock(),
        insert_assertion=AsyncMock(),
        link_assertions=AsyncMock(),
        delete_assertions=AsyncMock(),
        delete_nodes=AsyncMock(),
    )
    dao = MemoryDAO(engine, vector, graph_provider=graph)

    async def commit_fact(raw_log_id, object_name, supersedes=None):
        candidate = MemoryCandidate.from_raw_log(
            raw_log_id=raw_log_id,
            agent_id="tenant-a",
            session_id="session-a",
            content_payload=f"Backend {object_name} kullanıyor.",
            embedding_provider=identity.provider,
            embedding_model=identity.model,
            embedding_version=identity.version,
            embedding_dimension=identity.dimension,
            validation_mode=0,
        ).as_consolidation_record()
        await dao.record_mutation(candidate, raw_log_id=raw_log_id)
        await dao.record_mutation_extraction(
            "tenant-a",
            candidate["mutation_id"],
            [
                {
                    "head": "Backend",
                    "relation": "FRAMEWORK",
                    "tail": object_name,
                    "fact_text": f"Backend {object_name} kullanıyor.",
                    "source_span": f"Backend {object_name} kullanıyor.",
                    "supersedes": supersedes,
                }
            ],
        )
        await dao.set_mutation_state("tenant-a", candidate["mutation_id"], "VALIDATED")
        for _ in range(3):
            result = await process_projection_outbox_once(dao)
            assert result["completed"] == 1
        return candidate

    try:
        old = await commit_fact(1301, "FastAPI")
        new = await commit_fact(1302, "Spring Boot", supersedes="FastAPI")
        async with engine.connection() as db:
            async with db.execute(
                "SELECT mutation_id, status FROM v4_assertions "
                "WHERE predicate = 'FRAMEWORK'"
            ) as cursor:
                rows = await cursor.fetchall()
        assert {str(row[0]): str(row[1]) for row in rows} == {
            str(old["mutation_id"]): "SUPERSEDED",
            str(new["mutation_id"]): "ACTIVE",
        }

        await dao.request_pipeline_rollback(str(new["pipeline_run_id"]))
        async with engine.connection() as db:
            async with db.execute(
                "SELECT status FROM v4_assertions WHERE mutation_id = ?",
                (old["mutation_id"],),
            ) as cursor:
                old_status = await cursor.fetchone()
        assert old_status is not None and old_status[0] == "ACTIVE"
    finally:
        await engine.close()
