"""Regression tests for P13-02 / P13-02b batched worker projections."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mesa_memory.consolidation.schemas import MemoryCandidate
from mesa_storage.dao import MemoryDAO
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine
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
async def test_projection_outbox_batches_sqlite_commits(
    tmp_path, monkeypatch
) -> None:
    engine = AsyncEngine(str(tmp_path / "batch_commits.sqlite"))
    await engine.initialize()
    await initialize_schema(engine)
    dao = MemoryDAO(
        engine,
        SimpleNamespace(get_active_node_ids=AsyncMock(return_value=set())),
        embedding_enabled=False,
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

        monkeypatch.setattr(engine, "transaction", counting_sqlite_tx)

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
        assert sqlite_commits <= 2  # claim + skip batch

        for item in mutations:
            stored = await dao.get_mutation("agent-batch", item["mutation_id"])
            assert stored is not None and stored["state"] == "COMMITTED"
    finally:
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

        # Drain remaining VECTOR SKIPPED lanes for the healthy mutations.
        await process_projection_outbox_once(dao, worker_id="worker-fb", limit=20)

        for idx in (0, 2):
            stored = await dao.get_mutation("agent-batch", mutations[idx]["mutation_id"])
            assert stored is not None and stored["state"] == "COMMITTED"
        bad_stored = await dao.get_mutation("agent-batch", bad_mid)
        assert bad_stored is not None and bad_stored["state"] == "DEAD_LETTER"
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_sql_batch_mid_failure_rolls_back_and_fallback_completes_healthy_items(
    tmp_path, monkeypatch
) -> None:
    """Mid-batch SQL failure rolls back item 0 in the batch tx before clean per-item recovery."""
    engine = AsyncEngine(str(tmp_path / "sql_mid_fail.sqlite"))
    await engine.initialize()
    await initialize_schema(engine)
    dao = MemoryDAO(
        engine,
        SimpleNamespace(),
        embedding_enabled=False,
    )
    try:
        mutations = await _seed_validated_mutations(dao, count=3, with_triplets=True)
        bad_mid = mutations[1]["mutation_id"]
        orig_assertion_in_tx = MemoryDAO._project_v4_sql_assertion_in_tx
        batch_rolled_back_cleanly = False

        async def fail_middle_mutation(
            self, db, *, mutation, triplet, resolved_entities=None, owner_cache=None
        ):
            if str(mutation["mutation_id"]) == bad_mid:
                raise RuntimeError("injected mid-batch sql write failure")
            return await orig_assertion_in_tx(
                self,
                db,
                mutation=mutation,
                triplet=triplet,
                resolved_entities=resolved_entities,
                owner_cache=owner_cache,
            )

        monkeypatch.setattr(
            MemoryDAO, "_project_v4_sql_assertion_in_tx", fail_middle_mutation
        )

        # First verify rollback when batch fails at item 1: item 0 must not be half-persisted.
        try:
            await dao.apply_v4_sql_projections_batch(
                [
                    (mutations[0], ["SharedDoc", "Chunk_0"], [{"head": "SharedDoc", "relation": "CONTAINS", "tail": "Chunk_0"}]),
                    (mutations[1], ["SharedDoc", "Chunk_1"], [{"head": "SharedDoc", "relation": "CONTAINS", "tail": "Chunk_1"}]),
                ]
            )
        except RuntimeError:
            batch_rolled_back_cleanly = True
        assert batch_rolled_back_cleanly
        async with engine.connection() as db:
            async with db.execute("SELECT COUNT(*) FROM v4_assertions") as cur:
                assert (await cur.fetchone())[0] == 0
            async with db.execute("SELECT COUNT(*) FROM v4_entities") as cur:
                assert (await cur.fetchone())[0] == 0

        res = await process_projection_outbox_once(dao, worker_id="worker-mid", limit=20)
        assert res["completed"] == 2
        assert res["retry_pending"] == 1

        await process_projection_outbox_once(dao, worker_id="worker-mid", limit=20)

        for idx in (0, 2):
            stored = await dao.get_mutation("agent-batch", mutations[idx]["mutation_id"])
            assert stored is not None and stored["state"] == "COMMITTED"

        bad_stored = await dao.get_mutation("agent-batch", bad_mid)
        assert bad_stored is not None and bad_stored["state"] == "RETRY_PENDING"
        async with engine.connection() as db:
            async with db.execute(
                "SELECT COUNT(*) FROM v4_assertions WHERE mutation_id = ?",
                (bad_mid,),
            ) as cur:
                assert (await cur.fetchone())[0] == 0
    finally:
        await engine.close()
