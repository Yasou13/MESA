import pytest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock
from mesa_storage.dao import MemoryDAO
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.vector_engine import VectorEngine
from mesa_storage.schemas import initialize_schema

@pytest.mark.asyncio
async def test_model_disabled_runtime_degradation(tmp_path):
    """Verify that when embedding model is disabled, compute_embedding raises RuntimeError and search_v4_memory degrades gracefully without 500 error."""
    db_path = tmp_path / "mesa_test_model_disabled.db"
    lance_path = tmp_path / "mesa_test_model_disabled.lance"

    engine = AsyncEngine(str(db_path))
    await engine.initialize()
    await initialize_schema(engine)

    # VectorEngine created with allow_model_loading=False and embedding_provider=None
    vec = VectorEngine(uri=str(lance_path), allow_model_loading=False, embedding_provider=None)
    await vec.initialize()
    assert vec.semantic_runtime_available is False

    # Calling compute_embedding directly must raise RuntimeError
    with pytest.raises(RuntimeError, match="semantic embedding runtime is disabled"):
        await vec.compute_embedding("test query")

    mock_graph = SimpleNamespace()
    mock_graph.insert_node = AsyncMock()
    mock_graph.insert_assertion = AsyncMock()

    dao = MemoryDAO(sqlite_engine=engine, vector_engine=vec, graph_provider=mock_graph)

    tenant_id = "tenant_dis"
    agent_id = "agent_dis"
    workspace_id = "ws_dis"
    dataset_id = "dataset_dis"
    doc_id = f"doc_{uuid.uuid4().hex[:8]}"
    rev_id = f"rev_{uuid.uuid4().hex[:8]}"

    await dao.create_v4_workspace(tenant_id=tenant_id, workspace_id=workspace_id, workspace_name="WS Dis")
    await dao.ensure_v4_catalog_scope(tenant_id=tenant_id, workspace_id=workspace_id, dataset_id=dataset_id)
    await dao.create_v4_document(tenant_id=tenant_id, dataset_id=dataset_id, title="Doc Dis", document_id=doc_id)
    await dao.create_v4_revision(
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        document_id=doc_id,
        revision_id=rev_id,
        revision_number=1,
        content_hash="a" * 64,
    )

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
        "mutation_id": "mut_dis",
        "candidate_id": "cand_dis",
        "content_payload": "Model disabled entity",
        "embedding_provider": "sentence-transformers",
        "embedding_model": "all-MiniLM-L6-v2",
        "embedding_version": "1.0",
        "embedding_dimension": 384,
    }
    await dao.record_mutation(mut, raw_log_id=None)
    await dao.project_v4_sql_entity(mutation=mut, entity_name="Model Disabled Entity")

    t = {"head": "Model Disabled Entity", "relation": "STATUS", "literal_value": "DISABLED_TEST", "confidence": 1.0}
    await dao.project_v4_graph_triplet(mutation=mut, triplet=t)

    # search_v4_memory should NOT raise 500/RuntimeError, but degrade gracefully using graph/lexical lanes
    res = await dao.search_v4_memory(
        tenant_id=tenant_id,
        agent_id=agent_id,
        dataset_ids=[dataset_id],
        query="Model Disabled Entity",
        limit=10,
    )
    assert len(res) == 1
    assert res[0]["entity"]["canonical_name"] == "Model Disabled Entity"

    await vec.close()
    await engine.close()
