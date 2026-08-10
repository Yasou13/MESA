import pytest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock
from mesa_storage.dao import MemoryDAO, _DEFAULT_QUEUE_ADMISSION_POLICY
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.schemas import initialize_schema
from mesa_memory.context_builder import ContextBuilder

@pytest.mark.asyncio
async def test_context_builder_integration(tmp_path):
    """Verify ContextBuilder combines current-session logs, long-term canonical truth, provenance, and token budget."""
    db_path = tmp_path / "mesa_test_context_builder.db"
    engine = AsyncEngine(str(db_path))
    await engine.initialize()
    await initialize_schema(engine)

    mock_vec = SimpleNamespace()
    mock_vec.is_initialized = True
    mock_vec.compute_embedding = AsyncMock(return_value=[0.1] * 384)
    mock_vec.search = AsyncMock(return_value=[])

    mock_graph = SimpleNamespace()
    mock_graph.insert_node = AsyncMock()
    mock_graph.insert_assertion = AsyncMock()

    dao = MemoryDAO(sqlite_engine=engine, vector_engine=mock_vec, graph_provider=mock_graph)

    tenant_id = "tenant_ctx"
    agent_id = "agent_ctx"
    workspace_id = "ws_ctx"
    dataset_id = "dataset_ctx"
    session_id = "sess_ctx"
    doc_id = f"doc_{uuid.uuid4().hex[:8]}"
    rev_id = f"rev_{uuid.uuid4().hex[:8]}"

    await dao.create_v4_workspace(tenant_id=tenant_id, workspace_id=workspace_id, workspace_name="WS Ctx")
    await dao.ensure_v4_catalog_scope(tenant_id=tenant_id, workspace_id=workspace_id, dataset_id=dataset_id)
    await dao.create_v4_document(tenant_id=tenant_id, dataset_id=dataset_id, title="Doc Ctx", document_id=doc_id)
    await dao.create_v4_revision(
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        document_id=doc_id,
        revision_id=rev_id,
        revision_number=1,
        content_hash="a" * 64,
    )

    # Admit raw log for current session
    await dao.admit_raw_log(
        agent_id=agent_id,
        payload={"content": "Current session conversation log for Alice", "session_id": session_id},
        policy=_DEFAULT_QUEUE_ADMISSION_POLICY,
    )

    # Register long-term canonical memory
    mut = {
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "dataset_id": dataset_id,
        "document_id": doc_id,
        "revision_id": rev_id,
        "chunk_id": "c1",
        "agent_id": agent_id,
        "session_id": "sess_prev",
        "pipeline_run_id": "run_1",
        "source_ref": "ref_1",
        "mutation_id": "mut_ctx",
        "candidate_id": "cand_ctx",
        "content_payload": "Alice is Chief Architect",
        "embedding_provider": "st",
        "embedding_model": "model",
        "embedding_version": "1.0",
        "embedding_dimension": 384,
    }
    await dao.record_mutation(mut, raw_log_id=None)
    await dao.project_v4_sql_entity(mutation=mut, entity_name="Alice")

    t = {"head": "Alice", "relation": "ROLE", "literal_value": "Chief Architect", "confidence": 1.0}
    await dao.project_v4_graph_triplet(mutation=mut, triplet=t)

    # Build context using ContextBuilder
    builder = ContextBuilder(dao)
    ctx = await builder.build_context(
        tenant_id=tenant_id,
        agent_id=agent_id,
        dataset_ids=[dataset_id],
        query="Alice",
        session_id=session_id,
        token_budget=500,
    )

    formatted = ctx["formatted_context"]
    assert "Current Session Information" in formatted
    assert "Current session conversation log for Alice" in formatted
    assert "Long-Term Canonical Truth" in formatted
    assert "Alice" in formatted
    assert ctx["estimated_token_count"] > 0
    assert ctx["estimated_token_count"] <= 500

    await engine.close()
