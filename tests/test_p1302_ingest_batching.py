"""Regression tests for P13-02 batched worker projections and Mode 0 cold-path commits."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from mesa_memory.config import configured_embedding_identity
from mesa_memory.consolidation.schemas import MemoryCandidate
from mesa_storage.dao import MemoryDAO
from mesa_storage.kuzu_provider import KuzuGraphProvider
from mesa_storage.kuzu_setup import initialize_schema_artifact
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_workers.ingestion_worker import process_cold_path
from mesa_workers.projection_worker import process_projection_outbox_once


async def _seed_validated_mutations(
    dao: MemoryDAO,
    *,
    count: int,
    agent_id: str = "agent-batch",
    with_triplets: bool = False,
) -> list[dict]:
    mutations: list[dict] = []
    for idx in range(count):
        triplets = (
            [{"head": "SharedDoc", "relation": "CONTAINS", "tail": f"Chunk_{idx}"}]
            if with_triplets
            else []
        )
        candidate = MemoryCandidate.from_raw_log(
            raw_log_id=idx + 1,
            agent_id=agent_id,
            session_id="sess-batch",
            content_payload=f"Canonical clause {idx}",
            document_id="doc-shared",
            revision_id="rev-1",
            chunk_id=f"chunk-{idx}",
        ).as_consolidation_record()
        await dao.record_mutation(candidate, raw_log_id=idx + 1)
        await dao.record_mutation_extraction(agent_id, candidate["mutation_id"], triplets)
        await dao.set_mutation_state(agent_id, candidate["mutation_id"], "VALIDATED")
        mutations.append(candidate)
    return mutations


@pytest.mark.asyncio
async def test_projection_outbox_batches_sqlite_commits_and_kuzu_transactions(
    tmp_path, monkeypatch
) -> None:
    engine = AsyncEngine(str(tmp_path / "batch_commits.sqlite"))
    await engine.initialize()
    await initialize_schema(engine)
    graph_path = tmp_path / "kuzu_db"
    initialize_schema_artifact(str(graph_path))
    graph = KuzuGraphProvider(str(graph_path))
    await graph.initialize()
    dao = MemoryDAO(
        engine,
        SimpleNamespace(),
        graph_provider=graph,
        embedding_enabled=False,
        graph_enabled=True,
    )
    try:
        mutations = await _seed_validated_mutations(dao, count=10)

        sqlite_commits = 0
        orig_transaction = engine.transaction

        @asynccontextmanager
        async def counting_sqlite_tx():
            async with orig_transaction() as db:
                orig_commit = db.commit

                async def counted_commit():
                    nonlocal sqlite_commits
                    sqlite_commits += 1
                    return await orig_commit()

                monkeypatch.setattr(db, "commit", counted_commit)
                yield db

        kuzu_txs = 0
        orig_kuzu_tx = graph.transaction

        @asynccontextmanager
        async def counting_kuzu_tx():
            nonlocal kuzu_txs
            kuzu_txs += 1
            async with orig_kuzu_tx():
                yield

        monkeypatch.setattr(engine, "transaction", counting_sqlite_tx)
        monkeypatch.setattr(graph, "transaction", counting_kuzu_tx)

        # Pass 1 claims all 10 SQL projections and completes them in bounded commits.
        sql_res = await process_projection_outbox_once(dao, worker_id="worker-1", limit=50)
        assert sql_res["claimed"] == 10
        assert sql_res["completed"] == 10
        assert sqlite_commits <= 3  # claim + sql batch + outbox batch

        # Pass 2 claims all 10 VECTOR (disabled) projections in 2 commits (claim + skip batch).
        sqlite_commits = 0
        vec_res = await process_projection_outbox_once(dao, worker_id="worker-1", limit=50)
        assert vec_res["claimed"] == 10
        assert vec_res["completed"] == 10
        assert kuzu_txs == 0
        assert sqlite_commits <= 2  # claim + skip batch

        # Pass 3 claims all 10 GRAPH projections in 1 Kuzu tx and bounded SQLite commits.
        sqlite_commits = 0
        graph_res = await process_projection_outbox_once(
            dao, worker_id="worker-1", limit=50
        )
        assert graph_res["claimed"] == 10
        assert graph_res["completed"] == 10
        assert kuzu_txs == 1
        assert sqlite_commits <= 3  # claim + graph artifacts + outbox batch

        for item in mutations:
            stored = await dao.get_mutation("agent-batch", item["mutation_id"])
            assert stored is not None and stored["state"] == "COMMITTED"
    finally:
        await graph.close()
        await engine.close()


@pytest.mark.asyncio
async def test_sql_batch_rollback_falls_back_cleanly_on_bad_item(tmp_path) -> None:
    engine = AsyncEngine(str(tmp_path / "batch_fallback.sqlite"))
    await engine.initialize()
    await initialize_schema(engine)
    dao = MemoryDAO(
        engine,
        SimpleNamespace(),
        embedding_enabled=False,
        graph_enabled=False,
    )
    try:
        mutations = await _seed_validated_mutations(dao, count=3, with_triplets=True)
        # Corrupt the middle mutation's extracted triplets so _triplets raises PermanentProjectionError.
        bad_mid = mutations[1]["mutation_id"]
        async with engine.transaction() as db:
            await db.execute(
                "UPDATE memory_mutations SET metadata_json = ? WHERE mutation_id = ?",
                ('{"_mesa_v4_projection_triplets": ["not-a-dict"]}', bad_mid),
            )
            await db.commit()

        res = await process_projection_outbox_once(dao, worker_id="worker-fb", limit=20)
        assert res["dead_letter"] == 1
        assert res["completed"] >= 2

        # Drain remaining VECTOR and GRAPH SKIPPED lanes for the healthy mutations.
        await process_projection_outbox_once(dao, worker_id="worker-fb", limit=20)
        await process_projection_outbox_once(dao, worker_id="worker-fb", limit=20)

        for idx in (0, 2):
            stored = await dao.get_mutation("agent-batch", mutations[idx]["mutation_id"])
            assert stored is not None and stored["state"] == "COMMITTED"

        bad_stored = await dao.get_mutation("agent-batch", bad_mid)
        assert bad_stored is not None and bad_stored["state"] == "DEAD_LETTER"
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_graph_batch_error_falls_back_and_commits_healthy_items(
    tmp_path, monkeypatch
) -> None:
    engine = AsyncEngine(str(tmp_path / "graph_fallback.sqlite"))
    await engine.initialize()
    await initialize_schema(engine)
    graph_path = tmp_path / "kuzu_db"
    initialize_schema_artifact(str(graph_path))
    graph = KuzuGraphProvider(str(graph_path))
    await graph.initialize()
    dao = MemoryDAO(
        engine,
        SimpleNamespace(),
        graph_provider=graph,
        embedding_enabled=False,
        graph_enabled=True,
    )
    try:
        mutations = await _seed_validated_mutations(dao, count=3)
        # Complete SQL and VECTOR (skipped) first.
        await process_projection_outbox_once(dao, worker_id="worker-gf", limit=20)
        await process_projection_outbox_once(dao, worker_id="worker-gf", limit=20)

        # Force the batch artifact recorder to fail once so the worker falls back to _handle_one.
        orig_batch = MemoryDAO.record_mutation_artifacts_batch
        calls = 0

        async def fail_once(self, artifacts):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("transient sqlite write error")
            return await orig_batch(self, artifacts)

        monkeypatch.setattr(MemoryDAO, "record_mutation_artifacts_batch", fail_once)

        res = await process_projection_outbox_once(dao, worker_id="worker-gf", limit=20)
        assert calls == 1
        assert res["completed"] == 3  # 3 GRAPH committed via fallback

        for item in mutations:
            stored = await dao.get_mutation("agent-batch", item["mutation_id"])
            assert stored is not None and stored["state"] == "COMMITTED"
    finally:
        await graph.close()
        await engine.close()


@pytest.mark.asyncio
async def test_mode_zero_cold_path_uses_single_lifecycle_commit_and_honors_fencing(
    tmp_path, monkeypatch
) -> None:
    engine = AsyncEngine(str(tmp_path / "cold_path_batch.sqlite"))
    await engine.initialize()
    await initialize_schema(engine)
    dao = MemoryDAO(engine, SimpleNamespace())
    try:
        identity = configured_embedding_identity()
        candidate = MemoryCandidate.from_raw_log(
            raw_log_id=1,
            agent_id="agent-cp",
            session_id="sess-cp",
            content_payload="Deterministic Mode 0 memory",
            embedding_provider=identity.provider,
            embedding_model=identity.model,
            embedding_version=identity.version,
            embedding_dimension=identity.dimension,
            validation_mode=0,
        ).as_consolidation_record()
        payload = json.dumps(
            {
                "mutation_id": candidate["mutation_id"],
                "candidate_id": candidate["candidate_id"],
                "tenant_id": candidate["tenant_id"],
                "workspace_id": candidate["workspace_id"],
                "dataset_id": candidate["dataset_id"],
                "document_id": candidate["document_id"],
                "revision_id": candidate["revision_id"],
                "session_id": "sess-cp",
                "agent_id": "agent-cp",
                "chunk_id": candidate["chunk_id"],
                "source_ref": candidate["source_ref"],
                "content": "Deterministic Mode 0 memory",
            }
        )
        async with engine.transaction() as db:
            await db.execute(
                "INSERT INTO raw_logs (id, agent_id, payload, status) "
                "VALUES (1, 'agent-cp', ?, 'DEFERRED')",
                (payload,),
            )
            await db.commit()
        await dao.record_mutation(candidate, raw_log_id=1)

        sqlite_commits = 0
        orig_transaction = engine.transaction

        @asynccontextmanager
        async def counting_sqlite_tx():
            async with orig_transaction() as db:
                orig_commit = db.commit

                async def counted_commit():
                    nonlocal sqlite_commits
                    sqlite_commits += 1
                    return await orig_commit()

                monkeypatch.setattr(db, "commit", counted_commit)
                yield db

        monkeypatch.setattr(engine, "transaction", counting_sqlite_tx)

        await process_cold_path(
            1,
            "agent-cp",
            dao,
            consolidation_loop=None,
            model_processing_enabled=False,
        )
        # Only claim_raw_log + complete_v4_mode_zero_cold_path (2 commits total vs 5 previously).
        assert sqlite_commits == 2

        stored = await dao.get_mutation("agent-cp", candidate["mutation_id"])
        assert stored is not None and stored["state"] == "VALIDATED"

        # Stale claim token is still rejected by fenced completion.
        outcome, transitioned = await dao.complete_v4_mode_zero_cold_path(
            agent_id="agent-cp",
            mutation_id=candidate["mutation_id"],
            log_id=1,
            worker_id="cold-path",
            claim_token="stale-token",
            audit={"mode": 0},
        )
        assert outcome == "deferred"
        assert transitioned is False
    finally:
        await engine.close()
