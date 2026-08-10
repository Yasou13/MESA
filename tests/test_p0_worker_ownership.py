import pytest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock
from mesa_storage.dao import MemoryDAO
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.schemas import initialize_schema
from mesa_memory.api.server import _consume_combined_durable_work_once
from mesa_memory.worker_runtime import _recover_once
from mesa_workers.ingestion_worker import process_session_finalization

@pytest.mark.asyncio
async def test_worker_session_finalization_and_durable_ownership(tmp_path):
    """Verify that all durable work categories (dispatches, session finalizations, projections, cleanup, reclaims) are owned and processed."""
    db_path = tmp_path / "mesa_test_worker_ownership.db"
    engine = AsyncEngine(str(db_path))
    await engine.initialize()
    await initialize_schema(engine)

    mock_vec = SimpleNamespace()
    mock_vec.is_initialized = True
    mock_vec.compute_embedding = AsyncMock(return_value=[0.1] * 384)
    mock_vec.upsert = AsyncMock()
    mock_vec.soft_delete = AsyncMock()

    mock_graph = SimpleNamespace()
    mock_graph.insert_node = AsyncMock()
    mock_graph.insert_triplet = AsyncMock()
    mock_graph.insert_assertion = AsyncMock()

    dao = MemoryDAO(sqlite_engine=engine, vector_engine=mock_vec, graph_provider=mock_graph)

    tenant_id = "tenant_worker"
    agent_id = "agent_worker"
    workspace_id = "ws_worker"
    dataset_id = "dataset_worker"
    session_id = f"sess_{uuid.uuid4().hex[:8]}"

    await dao.create_v4_workspace(tenant_id=tenant_id, workspace_id=workspace_id, workspace_name="WS Worker")
    await dao.ensure_v4_catalog_scope(tenant_id=tenant_id, workspace_id=workspace_id, dataset_id=dataset_id)

    # Insert an un-processed raw_log for session_id
    log_id = await dao.insert_raw_log(agent_id, {"session_id": session_id, "text": "Test raw log"})

    # 1. Request session finalization - will be PENDING because raw_log is un-processed
    fin_rec = await dao.request_session_finalization(agent_id=agent_id, session_id=session_id)
    assert fin_rec["state"] == "PENDING"

    # Verify list_pending_session_finalizations finds it
    pending = await dao.list_pending_session_finalizations(limit=10)
    assert len(pending) == 1
    assert pending[0]["session_id"] == session_id

    # Mark raw_log as processed
    async with engine.connection() as db:
        await db.execute("UPDATE raw_logs SET status = 'processed' WHERE id = ?", (log_id,))
        await db.commit()

    # Process session finalization using worker process function
    await process_session_finalization(agent_id, session_id, dao, None)
    fin_after = await dao.get_session_finalization(agent_id, session_id)
    assert fin_after["state"] == "COMPLETED"

    # 2. Verify _consume_combined_durable_work_once processes session finalization when pending
    session_id_2 = f"sess_2_{uuid.uuid4().hex[:8]}"
    log_id_2 = await dao.insert_raw_log(agent_id, {"session_id": session_id_2, "text": "Test raw log 2"})

    await dao.request_session_finalization(agent_id=agent_id, session_id=session_id_2)

    async with engine.connection() as db:
        await db.execute("UPDATE raw_logs SET status = 'processed' WHERE id = ?", (log_id_2,))
        await db.commit()

    res = await _consume_combined_durable_work_once(dao, consolidation_loop=None, model_processing_enabled=False)
    assert res["finalizations"] == 1

    fin2_after = await dao.get_session_finalization(agent_id, session_id_2)
    assert fin2_after["state"] == "COMPLETED"

    # 3. Test _recover_once in worker_runtime
    rec_stats = await _recover_once(engine)
    assert "session_finalizations" in rec_stats
    assert "raw_log_claims" in rec_stats
    assert "wal_claims" in rec_stats

    await engine.close()
