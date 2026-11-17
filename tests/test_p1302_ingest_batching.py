"""Regression tests for P13-02 / P13-02b batched worker projections."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mesa_memory.consolidation.schemas import MemoryCandidate
from mesa_storage.dao import MemoryDAO
from mesa_storage.kuzu_provider import KuzuGraphProvider
from mesa_storage.kuzu_setup import initialize_schema_artifact
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
        SimpleNamespace(get_active_node_ids=AsyncMock(return_value=set())),
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
        SimpleNamespace(get_active_node_ids=AsyncMock(return_value=set())),
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
        graph_enabled=False,
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


@pytest.mark.asyncio
async def test_graph_kuzu_commit_succeeds_then_sqlite_write_fails_retry_idempotent(
    tmp_path, monkeypatch
) -> None:
    """If Kuzu commits and SQLite artifact or outbox completion fails, retry is idempotent with zero orphans."""
    monkeypatch.setattr("mesa_storage.dao._retry_delay_seconds", lambda _attempt: 0)
    engine = AsyncEngine(str(tmp_path / "graph_split_fail.sqlite"))
    await engine.initialize()
    await initialize_schema(engine)
    graph_path = tmp_path / "kuzu_db"
    initialize_schema_artifact(str(graph_path))
    graph = KuzuGraphProvider(str(graph_path))
    await graph.initialize()
    dao = MemoryDAO(
        engine,
        SimpleNamespace(get_active_node_ids=AsyncMock(return_value=set())),
        graph_provider=graph,
        embedding_enabled=False,
        graph_enabled=True,
    )
    try:
        mutations = await _seed_validated_mutations(dao, count=1)
        mid = mutations[0]["mutation_id"]
        # Complete SQL and VECTOR (skipped).
        await process_projection_outbox_once(dao, worker_id="worker-gs", limit=20)
        await process_projection_outbox_once(dao, worker_id="worker-gs", limit=20)

        # 1) Fail SQLite artifact batch write AFTER Kuzu commits.
        orig_artifacts_batch = MemoryDAO.record_mutation_artifacts_batch
        art_calls = 0

        async def fail_artifacts_once(self, artifacts):
            nonlocal art_calls
            art_calls += 1
            if art_calls == 1:
                raise RuntimeError("sqlite artifact write failed after kuzu commit")
            return await orig_artifacts_batch(self, artifacts)

        monkeypatch.setattr(
            MemoryDAO, "record_mutation_artifacts_batch", fail_artifacts_once
        )

        first = await process_projection_outbox_once(
            dao, worker_id="worker-gs", limit=20
        )
        assert first == {
            "claimed": 1,
            "completed": 0,
            "retry_pending": 1,
            "dead_letter": 0,
        }
        # The committed Kuzu write had no receipt, so it is compensated (no orphan).
        assert await graph.execute_query("MATCH (a:Assertion) RETURN count(a)") == [[0]]

        # 2) Fail SQLite outbox batch completion AFTER artifact write succeeds.
        orig_complete_batch = MemoryDAO.complete_projection_outbox_batch
        complete_calls = 0

        async def fail_complete_once(self, items, *, worker_id, outcome="APPLIED"):
            nonlocal complete_calls
            complete_calls += 1
            if complete_calls == 1:
                raise RuntimeError("sqlite outbox completion failed")
            return await orig_complete_batch(
                self, items, worker_id=worker_id, outcome=outcome
            )

        monkeypatch.setattr(
            MemoryDAO, "complete_projection_outbox_batch", fail_complete_once
        )
        with pytest.raises(RuntimeError, match="sqlite outbox completion failed"):
            await process_projection_outbox_once(dao, worker_id="worker-gs", limit=20)

        # Expire the in-flight lease and retry cleanly; retry stays idempotent with zero orphans.
        async with engine.transaction() as db:
            await db.execute(
                "UPDATE projection_outbox SET lease_expires_at = datetime('now', '-1 second') "
                "WHERE mutation_id = ? AND projection_name = 'GRAPH'",
                (mid,),
            )
            await db.commit()

        final = await process_projection_outbox_once(
            dao, worker_id="worker-gs", limit=20
        )
        assert final == {
            "claimed": 1,
            "completed": 1,
            "retry_pending": 0,
            "dead_letter": 0,
        }
        stored = await dao.get_mutation("agent-batch", mid)
        assert stored is not None and stored["state"] == "COMMITTED"
        assert await graph.execute_query("MATCH (a:Assertion) RETURN count(a)") == [[1]]
        parity = await dao.reconcile_v4_projection_parity()
        assert parity["missing_graph"] == 0
        async with engine.connection() as db:
            async with db.execute(
                "SELECT artifact_kind, COUNT(*) FROM memory_artifacts "
                "WHERE mutation_id = ? AND store_name = 'GRAPH' "
                "GROUP BY artifact_kind",
                (mid,),
            ) as cur:
                graph_artifacts = {str(r[0]): int(r[1]) for r in await cur.fetchall()}
        assert graph_artifacts == {"ASSERTION": 1, "ENTITY": 2}
        orphan_report = await dao.reconcile_v4_bidirectional(
            tenant_id=str(mutations[0]["tenant_id"]),
            agent_id="agent-batch",
            dataset_ids=[str(mutations[0]["dataset_id"])],
            repair=False,
        )
        assert orphan_report["physical_orphans"] == 0
    finally:
        await graph.close()
        await engine.close()


@pytest.mark.asyncio
async def test_complete_projection_outbox_batch_fences_stale_claim_token(
    tmp_path,
) -> None:
    engine = AsyncEngine(str(tmp_path / "outbox_fence.sqlite"))
    await engine.initialize()
    await initialize_schema(engine)
    dao = MemoryDAO(engine, SimpleNamespace())
    try:
        await _seed_validated_mutations(dao, count=2)
        claimed = await dao.claim_projection_outbox(worker_id="worker-f", limit=10)
        assert len(claimed) == 2
        stale_item = dict(claimed[1])
        stale_item["claim_token"] = "stale-claim-token"

        completed = await dao.complete_projection_outbox_batch(
            [claimed[0], stale_item], worker_id="worker-f", outcome="APPLIED"
        )
        assert completed == 1

        async with engine.connection() as db:
            async with db.execute(
                "SELECT projection_id, state FROM projection_outbox"
            ) as cur:
                states = {row[0]: row[1] for row in await cur.fetchall()}
        assert states[claimed[0]["projection_id"]] == "COMPLETED"
        assert states[claimed[1]["projection_id"]] == "IN_FLIGHT"
    finally:
        await engine.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [1, 2])
async def test_graph_batch_lost_fence_after_kuzu_commit_is_compensated(
    tmp_path, monkeypatch, count
) -> None:
    """A mutation fenced after the Kuzu commit leaves no graph write without a receipt."""
    engine = AsyncEngine(str(tmp_path / "graph_lost_fence.sqlite"))
    await engine.initialize()
    await initialize_schema(engine)
    graph_path = tmp_path / "kuzu_db"
    initialize_schema_artifact(str(graph_path))
    graph = KuzuGraphProvider(str(graph_path))
    await graph.initialize()
    dao = MemoryDAO(
        engine,
        SimpleNamespace(get_active_node_ids=AsyncMock(return_value=set())),
        graph_provider=graph,
        embedding_enabled=False,
        graph_enabled=True,
    )
    try:
        mutations = await _seed_validated_mutations(
            dao, count=count, with_triplets=True
        )
        # Complete SQL and VECTOR (skipped).
        await process_projection_outbox_once(dao, worker_id="worker-lf", limit=20)
        await process_projection_outbox_once(dao, worker_id="worker-lf", limit=20)

        fenced_mid = mutations[0]["mutation_id"]
        orig_artifacts_batch = MemoryDAO.record_mutation_artifacts_batch

        async def fence_then_record(self, artifacts):
            # A rollback wins the race after the graph transaction committed.
            async with engine.transaction() as db:
                await db.execute(
                    "UPDATE memory_mutations SET state = 'ROLLING_BACK' "
                    "WHERE mutation_id = ?",
                    (fenced_mid,),
                )
                await db.commit()
            return await orig_artifacts_batch(self, artifacts)

        monkeypatch.setattr(
            MemoryDAO, "record_mutation_artifacts_batch", fence_then_record
        )
        res = await process_projection_outbox_once(dao, worker_id="worker-lf", limit=20)
        assert res["claimed"] == count
        assert res["completed"] == count - 1

        assert await graph.execute_query(
            "MATCH (a:Assertion) WHERE a.mutation_id = $mid RETURN count(a)",
            {"mid": fenced_mid},
        ) == [[0]]
        async with engine.connection() as db:
            async with db.execute(
                "SELECT COUNT(*) FROM memory_artifacts "
                "WHERE mutation_id = ? AND store_name = 'GRAPH'",
                (fenced_mid,),
            ) as cur:
                assert (await cur.fetchone())[0] == 0
        for item in mutations[1:]:
            assert await graph.execute_query(
                "MATCH (a:Assertion) WHERE a.mutation_id = $mid RETURN count(a)",
                {"mid": item["mutation_id"]},
            ) == [[1]]
            stored = await dao.get_mutation("agent-batch", item["mutation_id"])
            assert stored is not None and stored["state"] == "COMMITTED"
    finally:
        await graph.close()
        await engine.close()
