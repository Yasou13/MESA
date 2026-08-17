import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mesa_memory.embedding.service import EmbeddingIdentity
from mesa_storage.dao import MemoryDAO
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine


@pytest.mark.asyncio
async def test_embedding_contract_and_dimension_validation(tmp_path):
    """Verify canonical embedding identity and fail-closed dimension validation."""
    db_path = tmp_path / "mesa_test_embedding_contract.db"
    engine = AsyncEngine(str(db_path))
    await engine.initialize()
    await initialize_schema(engine)

    mock_vec = SimpleNamespace()
    mock_vec.is_initialized = True
    mock_vec.compute_embedding = AsyncMock(return_value=[0.1] * 384)
    mock_vec.embedding_identity = EmbeddingIdentity(
        provider="sentence-transformers",
        model="all-MiniLM-L6-v2",
        version="1.0",
        dimension=384,
        normalized=True,
        model_revision="revision-a",
    )
    mock_vec.upsert = AsyncMock()
    mock_vec.soft_delete = AsyncMock()

    mock_graph = SimpleNamespace()
    mock_graph.insert_node = AsyncMock()

    dao = MemoryDAO(
        sqlite_engine=engine, vector_engine=mock_vec, graph_provider=mock_graph
    )

    tenant_id = "tenant_embed"
    agent_id = "agent_embed"
    workspace_id = "ws_embed"
    dataset_id = "dataset_embed"
    doc_id = f"doc_{uuid.uuid4().hex[:8]}"
    rev_id = f"rev_{uuid.uuid4().hex[:8]}"

    await dao.create_v4_workspace(
        tenant_id=tenant_id, workspace_id=workspace_id, workspace_name="WS Embed"
    )
    await dao.ensure_v4_catalog_scope(
        tenant_id=tenant_id, workspace_id=workspace_id, dataset_id=dataset_id
    )
    await dao.create_v4_document(
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        title="Doc Embed",
        document_id=doc_id,
    )

    mut_matching = {
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
        "mutation_id": "mut_match",
        "candidate_id": "cand_match",
        "content_payload": "Matching dim entity",
        "embedding_provider": "sentence-transformers",
        "embedding_model": "all-MiniLM-L6-v2",
        "embedding_version": "1.0",
        "embedding_model_revision": "revision-a",
        "embedding_dimension": 384,
    }
    await dao.record_mutation(mut_matching, raw_log_id=None)

    # 1. Matching dimension projects successfully
    e_id = await dao.project_v4_vector_entity(
        mutation=mut_matching, entity_name="Matching Entity"
    )
    assert e_id is not None

    # 2. Dimension Mismatch raises ValueError
    mut_mismatched = {
        **mut_matching,
        "mutation_id": "mut_mismatch",
        "candidate_id": "cand_mismatch",
        "embedding_dimension": 1536,  # Vector model outputs 384, mismatch with 1536!
    }
    await dao.record_mutation(mut_mismatched, raw_log_id=None)

    with pytest.raises(ValueError, match="embedding dimension mismatch"):
        await dao.project_v4_vector_entity(
            mutation=mut_mismatched, entity_name="Mismatched Entity"
        )

    # 3. Same dimension is not the same embedding space.
    mut_wrong_space = {
        **mut_matching,
        "mutation_id": "mut_wrong_space",
        "candidate_id": "cand_wrong_space",
        "embedding_model": "different-384d-model",
    }
    await dao.record_mutation(mut_wrong_space, raw_log_id=None)
    with pytest.raises(ValueError, match="embedding identity mismatch"):
        await dao.project_v4_vector_entity(
            mutation=mut_wrong_space, entity_name="Wrong Space Entity"
        )

    async with engine.connection() as db:
        async with db.execute(
            "SELECT metadata_json FROM memory_artifacts "
            "WHERE mutation_id = ? AND store_name = 'VECTOR'",
            (mut_matching["mutation_id"],),
        ) as cursor:
            artifact = await cursor.fetchone()
    assert artifact is not None
    assert '"embedding_space_id"' in artifact[0]
    assert '"model_revision": "revision-a"' in artifact[0]

    await engine.close()
