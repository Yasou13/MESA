import pytest
import uuid
from types import SimpleNamespace
from mesa_storage.dao import MemoryDAO
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.schemas import initialize_schema

@pytest.mark.asyncio
async def test_purge_before_projection_prevents_resurrection(tmp_path):
    """Verify that purging a document with pending/in-flight mutations prevents resurrection."""
    db_path = tmp_path / "mesa_test_purge.db"
    engine = AsyncEngine(str(db_path))
    await engine.initialize()
    await initialize_schema(engine)
    dao = MemoryDAO(engine, SimpleNamespace())

    tenant_id = "tenant_test"
    agent_id = "agent_test"
    workspace_id = "ws_test"
    dataset_id = "dataset_test"
    document_id = f"doc_{uuid.uuid4().hex[:8]}"
    session_id = "sess_1"

    await dao.create_v4_workspace(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        workspace_name="Workspace Test",
    )
    await dao.ensure_v4_catalog_scope(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        dataset_id=dataset_id,
    )
    doc_res = await dao.create_v4_document(
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        title="Test Document",
        document_id=document_id,
    )

    pipeline_run_id = f"run_{uuid.uuid4().hex[:8]}"
    mutation_id = f"mut_{uuid.uuid4().hex[:8]}"
    candidate_id = f"cand_{uuid.uuid4().hex[:8]}"

    # 1. Create a pending mutation before projection completes
    async with engine.transaction() as db:
        await db.execute(
            "INSERT INTO pipeline_runs (pipeline_run_id, tenant_id, agent_id, workspace_id, dataset_id, session_id, state) "
            "VALUES (?, ?, ?, ?, ?, ?, 'PROJECTING')",
            (pipeline_run_id, tenant_id, agent_id, workspace_id, dataset_id, session_id),
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

    # 2. Claim projection worker
    claims = await dao.claim_projection_outbox(worker_id="worker_purge_1", limit=10)
    assert len(claims) == 1
    claim = claims[0]

    # 3. Purge document BEFORE projection worker completes!
    purge_res = await dao.purge_v4_document(
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        document_id=document_id,
    )
    assert purge_res["state"] == "PURGED"

    # 4. Attempt to complete projection for purged document
    success = await dao.complete_projection_outbox(
        claim["projection_id"],
        worker_id="worker_purge_1",
        claim_token=claim["claim_token"],
        outcome="DONE",
    )
    assert success is False, "Projection completion for purged document must fail"

    # 5. Attempt to record artifact for purged document
    with pytest.raises(ValueError, match="cannot register artifact"):
        await dao.record_mutation_artifact(
            mutation_id,
            store_name="SQL",
            artifact_kind="ENTITY",
            artifact_id="ent_purged_1",
        )

    # 6. Verify document is PURGED and mutation is fenced in terminal state
    async with engine.transaction() as db:
        async with db.execute("SELECT status FROM documents WHERE document_id = ?", (document_id,)) as cursor:
            row = await cursor.fetchone()
            assert row["status"] == "PURGED"

        async with db.execute("SELECT state FROM memory_mutations WHERE mutation_id = ?", (mutation_id,)) as cursor:
            row = await cursor.fetchone()
            assert row["state"] in ("PURGED", "ROLLED_BACK")

    await engine.close()
