import pytest
import uuid
from types import SimpleNamespace
from mesa_storage.dao import MemoryDAO
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.schemas import initialize_schema

@pytest.mark.asyncio
async def test_mutation_state_machine_illegal_transitions_rejected(tmp_path):
    """Verify state machine rejects illegal state transitions for mutations and pipeline runs."""
    db_path = tmp_path / "mesa_test_sm.db"
    engine = AsyncEngine(str(db_path))
    await engine.initialize()
    await initialize_schema(engine)
    dao = MemoryDAO(engine, SimpleNamespace())

    tenant_id = "tenant_sm"
    agent_id = "agent_sm"
    dataset_id = "dataset_sm"
    document_id = f"doc_{uuid.uuid4().hex[:8]}"
    session_id = "sess_sm"
    pipeline_run_id = f"run_{uuid.uuid4().hex[:8]}"
    mutation_id = f"mut_{uuid.uuid4().hex[:8]}"

    # Setup terminal rolled-back pipeline run and mutation
    async with engine.transaction() as db:
        await db.execute(
            "INSERT INTO pipeline_runs (pipeline_run_id, tenant_id, agent_id, workspace_id, dataset_id, session_id, state) "
            "VALUES (?, ?, ?, 'ws_default', ?, ?, 'ROLLED_BACK')",
            (pipeline_run_id, tenant_id, agent_id, dataset_id, session_id),
        )
        await db.execute(
            "INSERT INTO memory_mutations (mutation_id, candidate_id, tenant_id, agent_id, dataset_id, document_id, session_id, pipeline_run_id, content_payload, state) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, '{}', 'ROLLED_BACK')",
            (mutation_id, f"cand_{uuid.uuid4().hex[:8]}", tenant_id, agent_id, dataset_id, document_id, session_id, pipeline_run_id),
        )
        await db.commit()

    # 1. Verify ROLLED_BACK -> COMMITTED pipeline run transition is rejected
    async with engine.transaction() as db:
        p_res = await MemoryDAO._transition_pipeline_run_in_tx(
            db, pipeline_run_id, to_state="COMMITTED", event_type="ILLEGAL_COMMITTED"
        )
        assert p_res is False, "ROLLED_BACK pipeline run must not transition to COMMITTED"

    # 2. Verify ROLLED_BACK -> COMMITTED mutation transition is rejected
    async with engine.transaction() as db:
        m_res = await MemoryDAO._transition_memory_mutation_in_tx(
            db, mutation_id, to_state="COMMITTED"
        )
        assert m_res is False, "ROLLED_BACK mutation must not transition to COMMITTED"

    # 3. Verify PURGED -> COMMITTED or SQL_APPLIED is rejected
    purged_mut_id = f"mut_purged_{uuid.uuid4().hex[:8]}"
    async with engine.transaction() as db:
        await db.execute(
            "INSERT INTO memory_mutations (mutation_id, candidate_id, tenant_id, agent_id, dataset_id, document_id, session_id, pipeline_run_id, content_payload, state) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, '{}', 'PURGED')",
            (purged_mut_id, f"cand_{uuid.uuid4().hex[:8]}", tenant_id, agent_id, dataset_id, document_id, session_id, pipeline_run_id),
        )
        await db.commit()

    async with engine.transaction() as db:
        p_mut_res = await MemoryDAO._transition_memory_mutation_in_tx(
            db, purged_mut_id, to_state="SQL_APPLIED"
        )
        assert p_mut_res is False, "PURGED mutation must not transition to SQL_APPLIED"

    # 4. Verify REJECTED -> COMMITTED is rejected
    rejected_mut_id = f"mut_rejected_{uuid.uuid4().hex[:8]}"
    async with engine.transaction() as db:
        await db.execute(
            "INSERT INTO memory_mutations (mutation_id, candidate_id, tenant_id, agent_id, dataset_id, document_id, session_id, pipeline_run_id, content_payload, state) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, '{}', 'REJECTED')",
            (rejected_mut_id, f"cand_{uuid.uuid4().hex[:8]}", tenant_id, agent_id, dataset_id, document_id, session_id, pipeline_run_id),
        )
        await db.commit()

    async with engine.transaction() as db:
        r_mut_res = await MemoryDAO._transition_memory_mutation_in_tx(
            db, rejected_mut_id, to_state="COMMITTED"
        )
        assert r_mut_res is False, "REJECTED mutation must not transition to COMMITTED"

    # The public state setter must not bypass the canonical transition table.
    assert await dao.set_mutation_state(agent_id, mutation_id, "COMMITTED") is False
    mutation = await dao.get_mutation(agent_id, mutation_id)
    assert mutation is not None
    assert mutation["state"] == "ROLLED_BACK"

    await engine.close()
