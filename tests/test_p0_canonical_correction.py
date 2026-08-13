import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mesa_storage.dao import MemoryDAO
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine


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
    mock_graph.set_assertion_status = AsyncMock()
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
        "metadata": {"valid_from": "2020-01-01T00:00:00Z"},
    }
    await dao.record_mutation(mut1, raw_log_id=None)
    await dao.project_v4_sql_entity(mutation=mut1, entity_name="Alice")
    t1 = {"head": "Alice", "relation": "ROLE", "literal_value": "Engineer", "confidence": 1.0}
    ass1_id = await dao.project_v4_graph_triplet(mutation=mut1, triplet=t1)
    await _commit_projected_mutation(dao, engine, agent_id, mut1, [t1])

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
        "metadata": {"valid_from": "2023-01-01T00:00:00Z"},
    }
    await dao.record_mutation(mut2, raw_log_id=None)
    await dao.project_v4_sql_entity(mutation=mut2, entity_name="Alice")
    t2 = {"head": "Alice", "relation": "ROLE", "literal_value": "Chief Architect", "confidence": 1.0}
    ass2_id = await dao.project_v4_graph_triplet(mutation=mut2, triplet=t2)

    # Admission/projection is not activation: until commit, old truth remains
    # current and the replacing assertion is retrieval-ineligible.
    before_commit = await dao.search_v4_memory(
        tenant_id=tenant_id,
        agent_id=agent_id,
        dataset_ids=[dataset_id],
        query="Alice",
        limit=10,
    )
    assert [item["literal_value"] for item in before_commit[0]["provenance"]] == [
        "Engineer"
    ]
    await _commit_projected_mutation(dao, engine, agent_id, mut2, [t2])

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

    historical = await dao.search_v4_memory(
        tenant_id=tenant_id,
        agent_id=agent_id,
        dataset_ids=[dataset_id],
        query="Alice",
        limit=10,
        valid_at="2021-06-01T00:00:00Z",
    )
    assert len(historical) == 1
    assert [item["literal_value"] for item in historical[0]["provenance"]] == [
        "Engineer"
    ]

    corrected_period = await dao.search_v4_memory(
        tenant_id=tenant_id,
        agent_id=agent_id,
        dataset_ids=[dataset_id],
        query="Alice",
        limit=10,
        valid_at="2024-06-01T00:00:00Z",
    )
    assert len(corrected_period) == 1
    assert [item["literal_value"] for item in corrected_period[0]["provenance"]] == [
        "Chief Architect"
    ]

    rollback = await dao.request_pipeline_rollback(mut2["pipeline_run_id"])
    assert rollback["state"] == "ROLLING_BACK"
    restored = await dao.search_v4_memory(
        tenant_id=tenant_id,
        agent_id=agent_id,
        dataset_ids=[dataset_id],
        query="Alice",
        limit=10,
    )
    assert [item["literal_value"] for item in restored[0]["provenance"]] == [
        "Engineer"
    ]

    await engine.close()


async def _commit_projected_mutation(dao, engine, agent_id, mutation, triplets):
    await dao.record_mutation_extraction(agent_id, mutation["mutation_id"], triplets)
    assert await dao.set_mutation_state(
        agent_id, mutation["mutation_id"], "VALIDATED"
    )
    async with engine.transaction() as db:
        for lane in ("SQL", "VECTOR", "GRAPH"):
            await db.execute(
                "UPDATE projection_outbox SET state = 'COMPLETED' "
                "WHERE mutation_id = ? AND projection_name = ?",
                (mutation["mutation_id"], lane),
            )
            await MemoryDAO._advance_mutation_projection_state(
                db, mutation["mutation_id"]
            )
        await db.commit()


@pytest.mark.asyncio
async def test_concurrent_corrections_from_same_predecessor_enforce_single_active_head(tmp_path):
    """Two concurrent corrections from the same predecessor must not both become ACTIVE."""
    db_path = tmp_path / "mesa_test_concurrent_head.db"
    engine = AsyncEngine(str(db_path))
    await engine.initialize()
    await initialize_schema(engine)

    dao = MemoryDAO(sqlite_engine=engine, vector_engine=SimpleNamespace(), graph_provider=SimpleNamespace())

    tenant_id = "tenant_cas"
    workspace_id = "ws_cas"
    dataset_id = "dataset_cas"
    doc_id = "doc_cas"

    await dao.create_v4_workspace(tenant_id=tenant_id, workspace_id=workspace_id, workspace_name="WS CAS")
    await dao.ensure_v4_catalog_scope(tenant_id=tenant_id, workspace_id=workspace_id, dataset_id=dataset_id)
    await dao.create_v4_document(tenant_id=tenant_id, dataset_id=dataset_id, title="Doc CAS", document_id=doc_id)

    # Base revision R0 (ACTIVE)
    await dao.create_v4_revision(
        tenant_id=tenant_id, dataset_id=dataset_id, document_id=doc_id,
        revision_id="rev_0", revision_number=1, content_hash="0" * 64
    )
    async with engine.transaction() as db:
        await db.execute(
            "UPDATE document_revisions SET status = 'ACTIVE' WHERE revision_id = 'rev_0'"
        )
        await db.commit()

    # Correction 1 (superseding R0)
    await dao.create_v4_revision(
        tenant_id=tenant_id, dataset_id=dataset_id, document_id=doc_id,
        revision_id="rev_1", revision_number=2, supersedes_revision_id="rev_0", content_hash="1" * 64
    )

    # Correction 2 (also superseding R0)
    await dao.create_v4_revision(
        tenant_id=tenant_id, dataset_id=dataset_id, document_id=doc_id,
        revision_id="rev_2", revision_number=3, supersedes_revision_id="rev_0", content_hash="2" * 64
    )

    # Commit Correction 1 first -> R1 becomes ACTIVE, R0 becomes SUPERSEDED
    m1 = {
        "tenant_id": tenant_id, "workspace_id": workspace_id, "dataset_id": dataset_id,
        "document_id": doc_id, "revision_id": "rev_1", "chunk_id": "c1", "agent_id": "agent_cas",
        "session_id": "sess_1", "pipeline_run_id": "run_1", "source_ref": "ref_1",
        "mutation_id": "mut_1", "candidate_id": "cand_1", "content_payload": "C1",
    }
    await dao.record_mutation(m1, raw_log_id=None)
    await _commit_projected_mutation(dao, engine, "agent_cas", m1, [])

    # Now attempt to commit Correction 2 whose predecessor R0 is no longer ACTIVE -> Must fail closed!
    m2 = {
        "tenant_id": tenant_id, "workspace_id": workspace_id, "dataset_id": dataset_id,
        "document_id": doc_id, "revision_id": "rev_2", "chunk_id": "c2", "agent_id": "agent_cas",
        "session_id": "sess_2", "pipeline_run_id": "run_2", "source_ref": "ref_2",
        "mutation_id": "mut_2", "candidate_id": "cand_2", "content_payload": "C2",
    }
    await dao.record_mutation(m2, raw_log_id=None)
    with pytest.raises(ValueError, match="revision supersession conflict"):
        await _commit_projected_mutation(dao, engine, "agent_cas", m2, [])

    # Verify document has exactly ONE active revision (R1)
    async with engine.connection() as db:
        async with db.execute("SELECT revision_id FROM document_revisions WHERE document_id = ? AND status = 'ACTIVE'", (doc_id,)) as cur:
            rows = await cur.fetchall()
            assert len(rows) == 1
            assert rows[0][0] == "rev_1"

    await engine.close()


@pytest.mark.asyncio
async def test_cannot_append_chunks_to_finalized_revision(tmp_path):
    """Appending a new chunk to an already finalized/ACTIVE revision must be rejected."""
    db_path = tmp_path / "mesa_test_freeze.db"
    engine = AsyncEngine(str(db_path))
    await engine.initialize()
    await initialize_schema(engine)

    dao = MemoryDAO(sqlite_engine=engine, vector_engine=SimpleNamespace(), graph_provider=SimpleNamespace())

    tenant_id = "tenant_freeze"
    workspace_id = "ws_freeze"
    dataset_id = "dataset_freeze"
    doc_id = "doc_freeze"
    rev_id = "rev_freeze"

    await dao.create_v4_workspace(tenant_id=tenant_id, workspace_id=workspace_id, workspace_name="WS Freeze")
    await dao.ensure_v4_catalog_scope(tenant_id=tenant_id, workspace_id=workspace_id, dataset_id=dataset_id)

    # 1. Create initial provenance (Revision + Chunk 1) -> status is ACTIVE
    p1 = await dao.create_v4_source_chunk(
        tenant_id=tenant_id, dataset_id=dataset_id, document_id=doc_id,
        revision_id=rev_id, chunk_id="chk_1", title="Title", content_payload="Chunk 1",
        source_ref="ref_1", revision_number=1, chunk_ordinal=0
    )
    assert p1["manifest_hash"] is not None

    # Manually activate revision to simulate pipeline run finalization
    async with engine.connection() as db:
        await db.execute("UPDATE document_revisions SET status = 'ACTIVE' WHERE revision_id = ?", (rev_id,))
        await db.commit()

    # 2. Re-inserting identical chunk 1 is idempotent
    p1_again = await dao.create_v4_source_chunk(
        tenant_id=tenant_id, dataset_id=dataset_id, document_id=doc_id,
        revision_id=rev_id, chunk_id="chk_1", title="Title", content_payload="Chunk 1",
        source_ref="ref_1", revision_number=1, chunk_ordinal=0
    )
    assert p1_again["manifest_hash"] == p1["manifest_hash"]

    # 3. Attempting to append a NEW chunk_2 to finalized rev_id must fail!
    with pytest.raises(ValueError, match="cannot append source chunk to finalized revision"):
        await dao.create_v4_source_chunk(
            tenant_id=tenant_id, dataset_id=dataset_id, document_id=doc_id,
            revision_id=rev_id, chunk_id="chk_2", title="Title", content_payload="Chunk 2",
            source_ref="ref_2", revision_number=1, chunk_ordinal=1
        )

    await engine.close()
