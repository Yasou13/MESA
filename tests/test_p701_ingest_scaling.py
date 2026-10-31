"""Ledger query plans and matched 40-chunk bookkeeping scale controls."""

import time
from types import SimpleNamespace

import pytest

from mesa_storage.dao import MemoryDAO
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine

PIPELINE_SQL = "SELECT state FROM memory_mutations WHERE pipeline_run_id = ?"
CHILD_SQL = "SELECT state FROM memory_mutations WHERE revision_id = ?"
COMPLETENESS_SQL = (
    "SELECT COUNT(*) FROM source_chunks c WHERE c.revision_id = ? "
    "AND NOT EXISTS (SELECT 1 FROM memory_mutations m "
    "WHERE m.revision_id = c.revision_id AND m.chunk_id = c.chunk_id "
    "AND m.state = 'COMMITTED')"
)


async def _store(path, size):
    engine = AsyncEngine(str(path))
    await engine.initialize()
    await initialize_schema(engine)
    dao = MemoryDAO(engine, SimpleNamespace())
    await dao.ensure_v4_catalog_scope(tenant_id="t", workspace_id="w", dataset_id="d")
    await dao.create_v4_document(
        tenant_id="t", dataset_id="d", document_id="doc", title="Doc"
    )
    await dao.create_v4_revision(
        tenant_id="t",
        dataset_id="d",
        document_id="doc",
        revision_id="r",
        revision_number=1,
        content_hash="a" * 64,
    )
    # One background revision; all measured chunks belong to a different revision.
    await dao.create_v4_document(
        tenant_id="t", dataset_id="d", document_id="bg-doc", title="Background"
    )
    await dao.create_v4_revision(
        tenant_id="t",
        dataset_id="d",
        document_id="bg-doc",
        revision_id="bg-r",
        revision_number=1,
        content_hash="b" * 64,
    )
    async with engine.transaction() as db:
        ids = {}
        for kind, external in [
            ("dataset", "d"),
            ("revision", "r"),
            ("revision", "bg-r"),
        ]:
            ids[external] = await dao._catalog.resolve_id_in_tx(
                db, tenant_id="t", kind=kind, external_id=external
            )
        for revision, count, prefix in [
            (ids["bg-r"], size, "bg"),
            (ids["r"], 40, "new"),
        ]:
            await db.executemany(
                "INSERT INTO source_chunks (chunk_id, tenant_id, dataset_id, revision_id, ordinal, "
                "content_payload, content_hash, source_ref) VALUES (?, 't', ?, ?, ?, 'chunk text', ?, 'test')",
                [
                    (f"{prefix}-{i}", ids["d"], revision, i, f"hash-{prefix}-{i}")
                    for i in range(count)
                ],
            )
            await db.executemany(
                "INSERT INTO pipeline_runs (pipeline_run_id, tenant_id, agent_id, workspace_id, "
                "dataset_id, session_id, state) VALUES (?, 't', 'a', 'w', ?, 's', ?)",
                [
                    (
                        f"{prefix}-{i}",
                        ids["d"],
                        "COMMITTED" if prefix == "bg" else "RUNNING",
                    )
                    for i in range(count)
                ],
            )
            await db.executemany(
                "INSERT INTO memory_mutations (mutation_id, candidate_id, tenant_id, agent_id, "
                "session_id, content_payload, pipeline_run_id, revision_id, chunk_id, state) "
                "VALUES (?, ?, 't', 'a', 's', 'chunk text', ?, ?, ?, ?)",
                [
                    (
                        f"{prefix}-{i}",
                        f"{prefix}-{i}",
                        f"{prefix}-{i}",
                        revision,
                        f"{prefix}-{i}",
                        "COMMITTED" if prefix == "bg" else "VALIDATED",
                    )
                    for i in range(count)
                ],
            )
        await db.execute(
            "UPDATE document_revisions SET manifest_hash='frozen', manifest_frozen_at=CURRENT_TIMESTAMP WHERE revision_id=?",
            (ids["r"],),
        )
        await db.executemany(
            "INSERT INTO projection_outbox (projection_id, mutation_id, projection_name, state) VALUES (?, ?, ?, 'COMPLETED')",
            [
                (f"new-{i}-{lane}", f"new-{i}", lane)
                for i in range(40)
                for lane in ("SQL", "VECTOR", "GRAPH")
            ],
        )
        await db.commit()
    return engine, ids["r"]


@pytest.mark.asyncio
async def test_ledger_plans_use_indexes(tmp_path):
    engine, revision = await _store(tmp_path / "plans.db", 500)
    try:
        async with engine.connection() as db:
            for sql, value in [
                (PIPELINE_SQL, "new-0"),
                (CHILD_SQL, revision),
                (COMPLETENESS_SQL, revision),
            ]:
                async with db.execute("EXPLAIN QUERY PLAN " + sql, (value,)) as cur:
                    plan = [str(row[3]) for row in await cur.fetchall()]
                assert not any(
                    "SCAN memory_mutations" in row or "SCAN m" in row for row in plan
                ), plan
                assert any("USING" in row and "INDEX" in row for row in plan), plan
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_40_chunk_ingest_cost_is_flat(tmp_path):
    measurements = []
    for size in (500, 5000):
        engine, revision = await _store(tmp_path / f"scale-{size}.db", size)
        steps = 0

        def progress():
            nonlocal steps
            steps += 100
            return 0

        try:
            async with engine.transaction() as db:
                # Count VM instructions as a deterministic rows-visited proxy, alongside wall time.
                await db.set_progress_handler(progress, 100)
                start = time.perf_counter()
                for i in range(40):
                    await MemoryDAO._advance_mutation_projection_state(db, f"new-{i}")
                await db.commit()
                elapsed = time.perf_counter() - start
                await db.set_progress_handler(None, 0)
                async with db.execute(
                    "SELECT status FROM document_revisions WHERE revision_id=?",
                    (revision,),
                ) as cur:
                    assert (await cur.fetchone())[0] == "ACTIVE"
            measurements.append((elapsed / 40, steps / 40))
            print(
                f"scale size={size}: {elapsed / 40:.6f} seconds/chunk, {steps / 40:.0f} VM instructions/chunk"
            )
        finally:
            await engine.close()
    assert measurements[1][1] <= measurements[0][1] * 1.5, measurements


@pytest.mark.asyncio
async def test_commit_reconciles_once_and_rejects_incomplete_children_first(
    tmp_path, monkeypatch
):
    engine, _revision = await _store(tmp_path / "once.db", 0)
    original = MemoryDAO._reconcile_revision_activation_in_tx
    calls = []
    statements = []

    async def reconcile(db, revision):
        calls.append(revision)
        await original(db, revision)

    monkeypatch.setattr(
        MemoryDAO, "_reconcile_revision_activation_in_tx", staticmethod(reconcile)
    )
    try:
        async with engine.transaction() as db:
            await db.set_trace_callback(statements.append)
            await MemoryDAO._advance_mutation_projection_state(db, "new-0")
            await db.set_trace_callback(None)
            await db.commit()
        assert len(calls) == 1
        assert not any(
            "SELECT COUNT(*) FROM source_chunks" in sql for sql in statements
        )
    finally:
        await engine.close()
