import pytest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock
from mesa_storage.dao import MemoryDAO
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.schemas import initialize_schema
from mesa_workers.projection_worker import process_projection_outbox_once

@pytest.mark.asyncio
async def test_multi_memory_extraction_contract(tmp_path):
    """Verify 1 Event -> 0..N Memories extraction and outbox projection behavior."""
    db_path = tmp_path / "mesa_test_multi_mem.db"
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

    tenant_id = "tenant_multi"
    agent_id = "agent_multi"
    workspace_id = "ws_multi"
    dataset_id = "dataset_multi"
    document_id = f"doc_{uuid.uuid4().hex[:8]}"
    revision_id = f"rev_{uuid.uuid4().hex[:8]}"
    chunk_id = f"chk_{uuid.uuid4().hex[:8]}"
    session_id = "sess_multi"
    pipeline_run_id = f"run_{uuid.uuid4().hex[:8]}"

    await dao.create_v4_workspace(tenant_id=tenant_id, workspace_id=workspace_id, workspace_name="WS Multi")
    await dao.ensure_v4_catalog_scope(tenant_id=tenant_id, workspace_id=workspace_id, dataset_id=dataset_id)
    await dao.create_v4_document(tenant_id=tenant_id, dataset_id=dataset_id, title="Multi Doc", document_id=document_id)

    # 1 Event -> 3 Memories extracted from single document chunk
    candidate_base = {
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "dataset_id": dataset_id,
        "document_id": document_id,
        "revision_id": revision_id,
        "chunk_id": chunk_id,
        "agent_id": agent_id,
        "session_id": session_id,
        "pipeline_run_id": pipeline_run_id,
        "source_ref": "doc_chunk_1",
        "embedding_provider": "sentence-transformers",
        "embedding_model": "all-MiniLM-L6-v2",
        "embedding_version": "1.0",
        "embedding_dimension": 384,
    }

    mut_1_id = f"mut_{uuid.uuid4().hex[:8]}"
    mut_2_id = f"mut_{uuid.uuid4().hex[:8]}"
    mut_3_id = f"mut_{uuid.uuid4().hex[:8]}"

    m1 = {**candidate_base, "mutation_id": mut_1_id, "candidate_id": f"cand_1_{mut_1_id}", "content_payload": "Alice is CEO"}
    m2 = {**candidate_base, "mutation_id": mut_2_id, "candidate_id": f"cand_2_{mut_2_id}", "content_payload": "Acme Corp founded in 2020"}
    m3 = {**candidate_base, "mutation_id": mut_3_id, "candidate_id": f"cand_3_{mut_3_id}", "content_payload": "Alice lives in London"}

    # Record all 3 memories for the single event/pipeline run
    await dao.record_mutation(m1, raw_log_id=None)
    await dao.record_mutation(m2, raw_log_id=None)
    await dao.record_mutation(m3, raw_log_id=None)

    # Attach extractions
    await dao.record_mutation_extraction(agent_id, mut_1_id, [{"head": "Alice", "relation": "ROLE", "literal_value": "CEO"}])
    await dao.record_mutation_extraction(agent_id, mut_2_id, [{"head": "Acme Corp", "relation": "FOUNDED", "literal_value": "2020"}])
    await dao.record_mutation_extraction(agent_id, mut_3_id, [{"head": "Alice", "relation": "LIVES_IN", "literal_value": "London"}])

    # Validate mutations to unblock projection outbox
    await dao.set_mutation_state(agent_id, mut_1_id, "VALIDATED")
    await dao.set_mutation_state(agent_id, mut_2_id, "VALIDATED")
    await dao.set_mutation_state(agent_id, mut_3_id, "VALIDATED")

    # Verify get_pipeline_mutations returns all 3 distinct memories for the 1 pipeline run event
    muts = await dao.get_pipeline_mutations(pipeline_run_id)
    assert len(muts) == 3
    mutation_ids = {m["mutation_id"] for m in muts}
    assert mutation_ids == {mut_1_id, mut_2_id, mut_3_id}

    # Process outbox for all 3 mutations across projection lanes
    claimed_total = 0
    completed_total = 0
    while True:
        res = await process_projection_outbox_once(dao, limit=20)
        if res["claimed"] == 0:
            break
        claimed_total += res["claimed"]
        completed_total += res["completed"]

    assert claimed_total == 9
    assert completed_total == 9

    # Verify all 3 memories advanced to COMMITTED
    for mut_id in (mut_1_id, mut_2_id, mut_3_id):
        m_rec = await dao.get_mutation(agent_id, mut_id)
        assert m_rec["state"] == "COMMITTED"

    await engine.close()
