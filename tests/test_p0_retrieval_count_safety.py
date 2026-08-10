import pytest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock
from mesa_storage.dao import MemoryDAO
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.schemas import initialize_schema

@pytest.mark.asyncio
async def test_bounded_count_and_existence_primitives(tmp_path):
    """Verify count_active_memories and has_active_memories perform bounded SQL COUNT and LIMIT 1 queries."""
    db_path = tmp_path / "mesa_test_count_safety.db"
    engine = AsyncEngine(str(db_path))
    await engine.initialize()
    await initialize_schema(engine)

    mock_vec = SimpleNamespace()
    mock_vec.is_initialized = True
    mock_graph = SimpleNamespace()

    dao = MemoryDAO(sqlite_engine=engine, vector_engine=mock_vec, graph_provider=mock_graph)

    tenant_id = "tenant_cnt"
    agent_id = "agent_cnt"
    workspace_id = "ws_cnt"
    dataset_id = "dataset_cnt"
    doc_id = f"doc_{uuid.uuid4().hex[:8]}"
    rev_id = f"rev_{uuid.uuid4().hex[:8]}"

    await dao.create_v4_workspace(tenant_id=tenant_id, workspace_id=workspace_id, workspace_name="WS Cnt")
    await dao.ensure_v4_catalog_scope(tenant_id=tenant_id, workspace_id=workspace_id, dataset_id=dataset_id)
    await dao.create_v4_document(tenant_id=tenant_id, dataset_id=dataset_id, title="Doc Cnt", document_id=doc_id)
    await dao.create_v4_revision(
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        document_id=doc_id,
        revision_id=rev_id,
        revision_number=1,
        content_hash="a" * 64,
    )

    # Initial state: 0 memories
    assert await dao.count_active_memories(tenant_id, dataset_ids=[dataset_id]) == 0
    assert not await dao.has_active_memories(tenant_id, dataset_ids=[dataset_id])

    # Project entity
    mut = {
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "dataset_id": dataset_id,
        "document_id": doc_id,
        "revision_id": rev_id,
        "chunk_id": "c1",
        "agent_id": agent_id,
        "session_id": "sess_1",
        "pipeline_run_id": "run_1",
        "source_ref": "ref_1",
        "mutation_id": "mut_cnt",
        "candidate_id": "cand_cnt",
        "content_payload": "Count entity",
        "embedding_provider": "st",
        "embedding_model": "model",
        "embedding_version": "1.0",
        "embedding_dimension": 384,
    }
    await dao.record_mutation(mut, raw_log_id=None)
    await dao.project_v4_sql_entity(mutation=mut, entity_name="Count Entity")

    # State after projection: 1 memory
    assert await dao.count_active_memories(tenant_id, dataset_ids=[dataset_id]) == 1
    assert await dao.has_active_memories(tenant_id, dataset_ids=[dataset_id])

    await engine.close()
