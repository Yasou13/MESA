import pytest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock
from mesa_storage.dao import MemoryDAO
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.schemas import initialize_schema

@pytest.mark.asyncio
async def test_canonical_correction_flow(tmp_path):
    """Verify that a new revision correcting prior facts supersedes old assertions and normal retrieval returns corrected truth."""
    db_path = tmp_path / "mesa_test_correction.db"
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
    mock_graph.link_assertions = AsyncMock()
    mock_graph.execute_write = AsyncMock()

    dao = MemoryDAO(sqlite_engine=engine, vector_engine=mock_vec, graph_provider=mock_graph)

    tenant_id = "tenant_corr"
    agent_id = "agent_corr"
    workspace_id = "ws_corr"
    dataset_id = "dataset_corr"
    doc_id = f"doc_{uuid.uuid4().hex[:8]}"

    await dao.create_v4_workspace(tenant_id=tenant_id, workspace_id=workspace_id, workspace_name="WS Corr")
    await dao.ensure_v4_catalog_scope(tenant_id=tenant_id, workspace_id=workspace_id, dataset_id=dataset_id)
    await dao.create_v4_document(tenant_id=tenant_id, dataset_id=dataset_id, title="Doc Corr", document_id=doc_id)

    # 1. Create Revision 1 with old truth: Alice -> ROLE -> "Engineer"
    rev1_id = f"rev1_{uuid.uuid4().hex[:8]}"
    await dao.create_v4_revision(
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        document_id=doc_id,
        revision_id=rev1_id,
        revision_number=1,
        content_hash="a" * 64,
    )

    mut1 = {
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "dataset_id": dataset_id,
        "document_id": doc_id,
        "revision_id": rev1_id,
        "chunk_id": "c1",
        "agent_id": agent_id,
        "session_id": "sess_1",
        "pipeline_run_id": "run_1",
        "source_ref": "ref_1",
        "mutation_id": "mut_1",
        "candidate_id": "cand_1",
        "content_payload": "Alice is Engineer",
        "embedding_provider": "st",
        "embedding_model": "model",
        "embedding_version": "1.0",
        "embedding_dimension": 384,
    }
    await dao.record_mutation(mut1, raw_log_id=None)
    await dao.project_v4_sql_entity(mutation=mut1, entity_name="Alice")
    t1 = {"head": "Alice", "relation": "ROLE", "literal_value": "Engineer", "confidence": 1.0}
    ass1_id = await dao.project_v4_graph_triplet(mutation=mut1, triplet=t1)

    # 2. Create Revision 2 superseding Revision 1 with corrected truth: Alice -> ROLE -> "Chief Architect"
    rev2_id = f"rev2_{uuid.uuid4().hex[:8]}"
    await dao.create_v4_revision(
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        document_id=doc_id,
        revision_id=rev2_id,
        revision_number=2,
        supersedes_revision_id=rev1_id,
        content_hash="b" * 64,
    )

    mut2 = {
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "dataset_id": dataset_id,
        "document_id": doc_id,
        "revision_id": rev2_id,
        "chunk_id": "c1",
        "agent_id": agent_id,
        "session_id": "sess_2",
        "pipeline_run_id": "run_2",
        "source_ref": "ref_2",
        "mutation_id": "mut_2",
        "candidate_id": "cand_2",
        "content_payload": "Alice is Chief Architect",
        "embedding_provider": "st",
        "embedding_model": "model",
        "embedding_version": "1.0",
        "embedding_dimension": 384,
    }
    await dao.record_mutation(mut2, raw_log_id=None)
    await dao.project_v4_sql_entity(mutation=mut2, entity_name="Alice")
    t2 = {"head": "Alice", "relation": "ROLE", "literal_value": "Chief Architect", "confidence": 1.0}
    ass2_id = await dao.project_v4_graph_triplet(mutation=mut2, triplet=t2)

    # 3. Check assertion status in SQLite
    async with dao._sql.connection() as db:
        async with db.execute("SELECT status FROM v4_assertions WHERE assertion_id = ?", (ass1_id,)) as cursor:
            st1 = (await cursor.fetchone())[0]
        async with db.execute("SELECT status FROM v4_assertions WHERE assertion_id = ?", (ass2_id,)) as cursor:
            st2 = (await cursor.fetchone())[0]

    assert st1 == "SUPERSEDED"
    assert st2 == "ACTIVE"

    # 4. Search for Alice -> Normal retrieval returns ONLY corrected active truth ("Chief Architect")
    res = await dao.search_v4_memory(
        tenant_id=tenant_id,
        agent_id=agent_id,
        dataset_ids=[dataset_id],
        query="Alice",
        limit=10,
    )
    assert len(res) == 1
    provenance = res[0]["provenance"]
    assert len(provenance) == 1
    assert provenance[0]["literal_value"] == "Chief Architect"

    await engine.close()
