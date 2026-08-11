import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mesa_storage.dao import MemoryDAO
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine


@pytest.mark.asyncio
async def test_retrieval_eligibility_and_temporal_truth(tmp_path):
    """Verify that stale, superseded, purged, or temporal-ineligible memories do not survive retrieval/fusion."""
    db_path = tmp_path / "mesa_test_retrieval_eligibility.db"
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

    tenant_id = "tenant_elig"
    agent_id = "agent_elig"
    workspace_id = "ws_elig"
    dataset_id = "dataset_elig"

    await dao.create_v4_workspace(tenant_id=tenant_id, workspace_id=workspace_id, workspace_name="WS Elig")
    await dao.ensure_v4_catalog_scope(tenant_id=tenant_id, workspace_id=workspace_id, dataset_id=dataset_id)

    doc1_id = f"doc1_{uuid.uuid4().hex[:8]}"
    doc2_id = f"doc2_{uuid.uuid4().hex[:8]}"
    await dao.create_v4_document(tenant_id=tenant_id, dataset_id=dataset_id, title="Doc 1", document_id=doc1_id)
    await dao.create_v4_document(tenant_id=tenant_id, dataset_id=dataset_id, title="Doc 2", document_id=doc2_id)

    rev1_id = f"rev1_{uuid.uuid4().hex[:8]}"
    rev2_id = f"rev2_{uuid.uuid4().hex[:8]}"

    await dao.create_v4_revision(
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        document_id=doc1_id,
        revision_id=rev1_id,
        revision_number=1,
        content_hash="a" * 64,
    )
    await dao.create_v4_revision(
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        document_id=doc2_id,
        revision_id=rev2_id,
        revision_number=1,
        content_hash="b" * 64,
    )

    mut1 = {
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "dataset_id": dataset_id,
        "document_id": doc1_id,
        "revision_id": rev1_id,
        "chunk_id": "c1",
        "agent_id": agent_id,
        "session_id": "sess_1",
        "pipeline_run_id": "run_1",
        "source_ref": "ref_1",
        "mutation_id": "mut_1",
        "candidate_id": "cand_1",
        "content_payload": "Acme Corp is ACTIVE_2020",
        "embedding_provider": "sentence-transformers",
        "embedding_model": "all-MiniLM-L6-v2",
        "embedding_version": "1.0",
        "embedding_dimension": 384,
        "metadata": {"valid_from": "2020-01-01T00:00:00Z", "valid_to": "2022-12-31T23:59:59Z"},
    }

    # Record mutation first
    await dao.record_mutation(mut1, raw_log_id=None)

    # 1. Register active entities and assertions
    await dao.project_v4_sql_entity(mutation=mut1, entity_name="Acme Corp")

    t1 = {"head": "Acme Corp", "relation": "STATUS", "literal_value": "ACTIVE_2020", "confidence": 1.0}
    await dao.project_v4_graph_triplet(mutation=mut1, triplet=t1)

    # Physical artifacts are not authoritative until the mutation commits.
    assert (
        await dao.search_v4_memory(
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_ids=[dataset_id],
            query="Acme Corp",
            limit=10,
            valid_at="2021-06-01T00:00:00Z",
        )
        == []
    )
    await dao.record_mutation_extraction(agent_id, mut1["mutation_id"], [t1])
    assert await dao.set_mutation_state(agent_id, mut1["mutation_id"], "VALIDATED")
    async with engine.transaction() as db:
        for lane in ("SQL", "VECTOR", "GRAPH"):
            await db.execute(
                "UPDATE projection_outbox SET state = 'COMPLETED' "
                "WHERE mutation_id = ? AND projection_name = ?",
                (mut1["mutation_id"], lane),
            )
            await MemoryDAO._advance_mutation_projection_state(db, mut1["mutation_id"])
        await db.commit()

    # Search for Acme Corp under dataset scope
    res = await dao.search_v4_memory(
        tenant_id=tenant_id,
        agent_id=agent_id,
        dataset_ids=[dataset_id],
        query="Acme Corp",
        limit=10,
        valid_at="2021-06-01T00:00:00Z",
    )
    assert len(res) == 1
    assert res[0]["entity"]["canonical_name"] == "Acme Corp"

    # Search with valid_at outside valid range (e.g. 2025-01-01) -> assertion is temporally ineligible
    res_expired = await dao.search_v4_memory(
        tenant_id=tenant_id,
        agent_id=agent_id,
        dataset_ids=[dataset_id],
        query="Acme Corp",
        limit=10,
        valid_at="2025-01-01T00:00:00Z",
    )
    assert res_expired == []

    # A second agent can share the tenant and dataset but must not inherit
    # canonical entity/assertion eligibility from this agent.
    res_other_agent = await dao.search_v4_memory(
        tenant_id=tenant_id,
        agent_id="agent_other",
        dataset_ids=[dataset_id],
        query="Acme Corp",
        limit=10,
    )
    assert res_other_agent == []

    # 2. Test Purging: Purge doc1_id -> entity/assertion becomes RETRACTED/PURGED and must NOT be returned
    await dao.purge_v4_document(tenant_id=tenant_id, dataset_id=dataset_id, document_id=doc1_id)

    res_purged = await dao.search_v4_memory(
        tenant_id=tenant_id,
        agent_id=agent_id,
        dataset_ids=[dataset_id],
        query="Acme Corp",
        limit=10,
    )
    assert len(res_purged) == 0

    await engine.close()
