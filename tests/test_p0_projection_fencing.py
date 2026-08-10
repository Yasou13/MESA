import pytest
import uuid
from types import SimpleNamespace
from mesa_storage.dao import MemoryDAO
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.schemas import initialize_schema

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
