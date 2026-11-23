"""Tests for canonical SQLite fact-level supersession and rollback."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mesa_memory.consolidation.schemas import MemoryCandidate
from mesa_memory.embedding.service import EmbeddingIdentity
from mesa_storage.dao import MemoryDAO
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_workers.projection_worker import process_projection_outbox_once


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
    dao = MemoryDAO(engine, vector)

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
        for _ in range(2):
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


@pytest.mark.asyncio
async def test_supersession_targets_only_the_exact_old_value_and_null_confidence(
    tmp_path,
):
    engine = AsyncEngine(str(tmp_path / "exact-supersession.sqlite"))
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
    dao = MemoryDAO(engine, vector)

    async def commit(raw_log_id, object_name, supersedes=None, confidence=0.9):
        candidate = MemoryCandidate.from_raw_log(
            raw_log_id=raw_log_id,
            agent_id="tenant-a",
            session_id="session-a",
            content_payload=f"Ali knows {object_name}.",
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
                    "head": "Ali",
                    "relation": "KNOWS",
                    "tail": object_name,
                    "fact_text": f"Ali knows {object_name}.",
                    "source_span": f"Ali knows {object_name}.",
                    "supersedes": supersedes,
                    "confidence": confidence,
                }
            ],
        )
        await dao.set_mutation_state("tenant-a", candidate["mutation_id"], "VALIDATED")
        for _ in range(2):
            result = await process_projection_outbox_once(dao)
            assert result["completed"] == 1
        return candidate

    try:
        first = await commit(1401, "Python")
        second = await commit(1402, "Rust")
        third = await commit(1403, "Go", supersedes="Python", confidence=None)
        async with engine.connection() as db:
            async with db.execute(
                "SELECT mutation_id, status FROM v4_assertions "
                "WHERE predicate = 'KNOWS'"
            ) as cursor:
                rows = await cursor.fetchall()
        assert {str(row[0]): str(row[1]) for row in rows} == {
            str(first["mutation_id"]): "SUPERSEDED",
            str(second["mutation_id"]): "ACTIVE",
            str(third["mutation_id"]): "ACTIVE",
        }
    finally:
        await engine.close()
