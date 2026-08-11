import uuid
from types import SimpleNamespace

import pytest

from mesa_storage.dao import MemoryDAO
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine


@pytest.mark.asyncio
async def test_projection_fencing_against_rollback(tmp_path):
    """Verify that completing a projection after rollback fails and cannot advance state."""
    db_path = tmp_path / "mesa_test.db"
    engine = AsyncEngine(str(db_path))
    await engine.initialize()
    await initialize_schema(engine)
    dao = MemoryDAO(engine, SimpleNamespace())

    tenant_id = "tenant_test"
    agent_id = "agent_test"
    dataset_id = "dataset_test"
    document_id = "doc_test"
    session_id = "sess_1"
    pipeline_run_id = f"run_{uuid.uuid4().hex[:8]}"
    mutation_id = f"mut_{uuid.uuid4().hex[:8]}"
    candidate_id = f"cand_{uuid.uuid4().hex[:8]}"

    # 1. Create pipeline run in PROJECTING state and memory mutation
    async with engine.transaction() as db:
        await db.execute(
            "INSERT INTO pipeline_runs (pipeline_run_id, tenant_id, agent_id, workspace_id, dataset_id, session_id, state) "
            "VALUES (?, ?, ?, 'ws_default', ?, ?, 'PROJECTING')",
            (pipeline_run_id, tenant_id, agent_id, dataset_id, session_id),
        )
        await db.execute(
            "INSERT INTO memory_mutations (mutation_id, candidate_id, tenant_id, agent_id, dataset_id, document_id, session_id, pipeline_run_id, content_payload, state) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, '{}', 'PENDING')",
            (mutation_id, candidate_id, tenant_id, agent_id, dataset_id, document_id, session_id, pipeline_run_id),
        )
        await db.execute(
            "INSERT INTO projection_outbox (projection_id, mutation_id, projection_name, state) "
            "VALUES (?, ?, 'SQL', 'PENDING')",
            (f"proj_{mutation_id}_SQL", mutation_id),
        )
        await db.commit()

    # 2. Claim projection
    claims = await dao.claim_projection_outbox(worker_id="worker_1", limit=10)
    assert len(claims) == 1
    claim = claims[0]
    assert claim["projection_id"] == f"proj_{mutation_id}_SQL"

    # 3. Request rollback of pipeline run while worker holds claim
    rollback_res = await dao.request_pipeline_rollback(pipeline_run_id)
    assert rollback_res["state"] == "ROLLED_BACK"

    # 4. Attempt to complete projection with stale claim_token
    success = await dao.complete_projection_outbox(
        claim["projection_id"],
        worker_id="worker_1",
        claim_token=claim["claim_token"],
        outcome="DONE",
    )
    assert success is False, "Stale projection completion must fail after rollback"

    # 5. Attempt to register artifact under rolled-back mutation
    with pytest.raises(ValueError, match="cannot register artifact"):
        await dao.record_mutation_artifact(
            mutation_id,
            store_name="SQL",
            artifact_kind="ENTITY",
            artifact_id="ent_1",
        )

    # 6. Verify mutation and pipeline run remain ROLLED_BACK
    async with engine.transaction() as db:
        async with db.execute("SELECT state FROM memory_mutations WHERE mutation_id = ?", (mutation_id,)) as cursor:
            row = await cursor.fetchone()
            assert row["state"] == "ROLLED_BACK"

        async with db.execute("SELECT state FROM pipeline_runs WHERE pipeline_run_id = ?", (pipeline_run_id,)) as cursor:
            row = await cursor.fetchone()
            assert row["state"] == "ROLLED_BACK"

    await engine.close()


@pytest.mark.asyncio
async def test_projection_parity_repair_cannot_race_rollback(tmp_path):
    """A maintenance repair discovered before rollback cannot resurrect it."""
    engine = AsyncEngine(str(tmp_path / "mesa_parity_race.db"))
    await engine.initialize()
    await initialize_schema(engine)
    pipeline_run_id = "run_parity_race"
    mutation_id = "mut_parity_race"
    projection_id = "proj_parity_race"

    class RollbackDuringVectorCheck:
        async def get_existing_node_ids(self, _agent_id, _node_ids):
            async with engine.transaction() as db:
                await db.execute(
                    "UPDATE pipeline_runs SET state = 'ROLLED_BACK' WHERE pipeline_run_id = ?",
                    (pipeline_run_id,),
                )
                await db.execute(
                    "UPDATE memory_mutations SET state = 'ROLLED_BACK' WHERE mutation_id = ?",
                    (mutation_id,),
                )
                await db.execute(
                    "UPDATE projection_outbox SET state = 'CANCELLED' WHERE projection_id = ?",
                    (projection_id,),
                )
                await db.commit()
            return set()

    dao = MemoryDAO(engine, RollbackDuringVectorCheck())
    async with engine.transaction() as db:
        await db.execute(
            "INSERT INTO pipeline_runs (pipeline_run_id, tenant_id, agent_id, workspace_id, dataset_id, session_id, state) "
            "VALUES (?, 'tenant', 'agent', 'workspace', 'dataset', 'session', 'COMMITTED')",
            (pipeline_run_id,),
        )
        await db.execute(
            "INSERT INTO memory_mutations (mutation_id, candidate_id, tenant_id, agent_id, dataset_id, session_id, pipeline_run_id, content_payload, state) "
            "VALUES (?, 'candidate', 'tenant', 'agent', 'dataset', 'session', ?, '{}', 'COMMITTED')",
            (mutation_id, pipeline_run_id),
        )
        await db.execute(
            "INSERT INTO projection_outbox (projection_id, mutation_id, projection_name, state) "
            "VALUES (?, ?, 'VECTOR', 'COMPLETED')",
            (projection_id, mutation_id),
        )
        await db.execute(
            "INSERT INTO memory_artifacts (artifact_row_id, mutation_id, store_name, artifact_kind, artifact_id, state) "
            "VALUES ('artifact-row', ?, 'VECTOR', 'ENTITY_VECTOR', 'entity', 'ACTIVE')",
            (mutation_id,),
        )
        await db.commit()

    result = await dao.reconcile_v4_projection_parity(repair=True)
    assert result["missing_artifacts"] == 1
    assert result["requeued_lanes"] == 0
    async with engine.connection() as db:
        mutation = await (
            await db.execute(
                "SELECT state FROM memory_mutations WHERE mutation_id = ?", (mutation_id,)
            )
        ).fetchone()
        projection = await (
            await db.execute(
                "SELECT state FROM projection_outbox WHERE projection_id = ?", (projection_id,)
            )
        ).fetchone()
    assert mutation[0] == "ROLLED_BACK"
    assert projection[0] == "CANCELLED"
    await engine.close()


@pytest.mark.asyncio
async def test_projection_parity_requeues_every_missing_lane_for_one_mutation(tmp_path):
    """One CAS transition must not prevent repair of a second missing lane."""
    engine = AsyncEngine(str(tmp_path / "mesa_parity_multi_lane.db"))
    await engine.initialize()
    await initialize_schema(engine)
    vector = SimpleNamespace(get_existing_node_ids=lambda *_args: _empty_ids())
    dao = MemoryDAO(engine, vector)
    async with engine.transaction() as db:
        await db.execute(
            "INSERT INTO pipeline_runs (pipeline_run_id, tenant_id, agent_id, workspace_id, dataset_id, session_id, state) "
            "VALUES ('run', 'tenant', 'agent', 'workspace', 'dataset', 'session', 'COMMITTED')"
        )
        await db.execute(
            "INSERT INTO memory_mutations (mutation_id, candidate_id, tenant_id, agent_id, dataset_id, session_id, pipeline_run_id, content_payload, state) "
            "VALUES ('mutation', 'candidate', 'tenant', 'agent', 'dataset', 'session', 'run', '{}', 'COMMITTED')"
        )
        for lane in ("SQL", "VECTOR"):
            await db.execute(
                "INSERT INTO projection_outbox (projection_id, mutation_id, projection_name, state) "
                "VALUES (?, 'mutation', ?, 'COMPLETED')",
                (f"projection-{lane}", lane),
            )
            await db.execute(
                "INSERT INTO memory_artifacts (artifact_row_id, mutation_id, store_name, artifact_kind, artifact_id, state) "
                "VALUES (?, 'mutation', ?, ?, ?, 'ACTIVE')",
                (
                    f"artifact-{lane}",
                    lane,
                    "ENTITY" if lane == "SQL" else "ENTITY_VECTOR",
                    f"missing-{lane}",
                ),
            )
        await db.commit()

    result = await dao.reconcile_v4_projection_parity(repair=True)
    assert result["missing_artifacts"] == 2
    assert result["requeued_lanes"] == 2
    async with engine.connection() as db:
        states = await (
            await db.execute(
                "SELECT projection_name, state FROM projection_outbox ORDER BY projection_name"
            )
        ).fetchall()
        mutation = await (
            await db.execute(
                "SELECT state FROM memory_mutations WHERE mutation_id = 'mutation'"
            )
        ).fetchone()
    assert [(row[0], row[1]) for row in states] == [
        ("SQL", "RETRY_PENDING"),
        ("VECTOR", "RETRY_PENDING"),
    ]
    assert mutation[0] == "RETRY_PENDING"
    await engine.close()


async def _empty_ids() -> set[str]:
    return set()
