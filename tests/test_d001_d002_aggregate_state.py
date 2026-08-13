"""Adversarial regression test suite for Task D001 (Aggregate Revision Activation)
and Task D002 (Aggregate Pipeline State)."""

import pytest
import asyncio
from types import SimpleNamespace
from mesa_storage.dao import MemoryDAO
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.schemas import initialize_schema


@pytest.mark.asyncio
async def test_d001_aggregate_revision_activation_barrier(tmp_path):
    """Verify that a revision remains non-ACTIVE while any required child mutation is incomplete/failed,
    and transitions to ACTIVE exactly once when all required children complete."""
    db_path = str(tmp_path / "mesa_test_d001.db")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)

    dao = MemoryDAO(engine, SimpleNamespace())
    tenant_id = "tenant_d001"
    dataset_id = "dataset_d001"
    doc_id = "doc_d001"
    rev_id = "rev_d001"

    await dao.ensure_v4_catalog_scope(tenant_id=tenant_id, workspace_id="ws_1", dataset_id=dataset_id)
    await dao.create_v4_document(tenant_id=tenant_id, dataset_id=dataset_id, document_id=doc_id, title="Doc 1")
    await dao.create_v4_revision(
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        document_id=doc_id,
        revision_id=rev_id,
        revision_number=1,
        content_hash="a" * 64,
    )

    # Insert 3 source chunks
    for i in range(3):
        await dao.create_v4_source_chunk(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            document_id=doc_id,
            revision_id=rev_id,
            chunk_id=f"chunk_{i}",
            title=f"Chunk {i}",
            content_payload=f"Payload {i}",
            source_ref=f"ref_{i}",
            chunk_ordinal=i,
        )

    pipeline_run_id = "pipe_d001"
    async with engine.transaction() as db:
        await db.execute(
            "INSERT INTO pipeline_runs (pipeline_run_id, tenant_id, workspace_id, dataset_id, session_id, agent_id, state) "
            "VALUES (?, ?, 'ws_1', ?, 'sess_1', 'agent_1', 'RUNNING')",
            (pipeline_run_id, tenant_id, dataset_id),
        )
        for i in range(3):
            mut_id = f"mut_d001_{i}"
            cand_id = f"cand_d001_{i}"
            await db.execute(
                "INSERT INTO memory_mutations (mutation_id, candidate_id, raw_log_id, tenant_id, workspace_id, "
                "dataset_id, document_id, revision_id, chunk_id, source_ref, evidence_span, agent_id, session_id, "
                "content_payload, metadata_json, source, pipeline_run_id, extraction_version, embedding_provider, "
                "embedding_model, embedding_version, embedding_dimension, state) "
                "VALUES (?, ?, ?, ?, 'ws_1', ?, ?, ?, ?, 'ref', '0:5', 'agent_1', 'sess_1', 'payload', '{}', 'api', ?, 'v4', 'st', 'm', 'v', 384, 'RECEIVED')",
                (mut_id, cand_id, 100 + i, tenant_id, dataset_id, doc_id, rev_id, f"chunk_{i}", pipeline_run_id),
            )
        await db.commit()

    # Move Child A to COMMITTED, Child B to RETRY_PENDING, Child C to DEAD_LETTER
    async with engine.transaction() as db:
        await dao._transition_memory_mutation_in_tx(db, "mut_d001_0", to_state="COMMITTED")
        await dao._transition_memory_mutation_in_tx(db, "mut_d001_1", to_state="RETRY_PENDING")
        await dao._transition_memory_mutation_in_tx(db, "mut_d001_2", to_state="DEAD_LETTER")
        await db.commit()

    # Revision must REMAIN PENDING (non-ACTIVE)
    async with engine.connection() as db:
        async with db.execute("SELECT status FROM document_revisions WHERE revision_id = ?", (rev_id,)) as cur:
            row = await cur.fetchone()
            assert row[0] == "PENDING"

    # Now complete Child B and Child C to COMMITTED
    async with engine.transaction() as db:
        await dao._transition_memory_mutation_in_tx(db, "mut_d001_1", to_state="COMMITTED")
        await dao._transition_memory_mutation_in_tx(db, "mut_d001_2", to_state="COMMITTED")
        await db.commit()

    # Now all 3 children are COMMITTED -> revision must become ACTIVE exactly once
    async with engine.connection() as db:
        async with db.execute("SELECT status FROM document_revisions WHERE revision_id = ?", (rev_id,)) as cur:
            row = await cur.fetchone()
            assert row[0] == "ACTIVE"

    await engine.close()


@pytest.mark.asyncio
async def test_d002_aggregate_pipeline_state_derivation(tmp_path):
    """Verify that parent pipeline state is derived from aggregate child mutation state
    and single child mutation cannot directly declare a multi-child pipeline COMMITTED."""
    db_path = str(tmp_path / "mesa_test_d002.db")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)

    dao = MemoryDAO(engine, SimpleNamespace())
    tenant_id = "tenant_d002"
    dataset_id = "dataset_d002"
    doc_id = "doc_d002"
    rev_id = "rev_d002"
    pipeline_run_id = "pipe_d002"

    await dao.ensure_v4_catalog_scope(tenant_id=tenant_id, workspace_id="ws_2", dataset_id=dataset_id)
    await dao.create_v4_document(tenant_id=tenant_id, dataset_id=dataset_id, document_id=doc_id, title="Doc 2")
    await dao.create_v4_revision(
        tenant_id=tenant_id, dataset_id=dataset_id, document_id=doc_id, revision_id=rev_id, revision_number=1, content_hash="b" * 64
    )

    async with engine.transaction() as db:
        await db.execute(
            "INSERT INTO pipeline_runs (pipeline_run_id, tenant_id, workspace_id, dataset_id, session_id, agent_id, state) "
            "VALUES (?, ?, 'ws_2', ?, 'sess_2', 'agent_2', 'RUNNING')",
            (pipeline_run_id, tenant_id, dataset_id),
        )
        for i in range(3):
            mut_id = f"mut_d002_{i}"
            cand_id = f"cand_d002_{i}"
            await db.execute(
                "INSERT INTO memory_mutations (mutation_id, candidate_id, raw_log_id, tenant_id, workspace_id, "
                "dataset_id, document_id, revision_id, chunk_id, source_ref, evidence_span, agent_id, session_id, "
                "content_payload, metadata_json, source, pipeline_run_id, extraction_version, embedding_provider, "
                "embedding_model, embedding_version, embedding_dimension, state) "
                "VALUES (?, ?, ?, ?, 'ws_2', ?, ?, ?, ?, 'ref', '0:5', 'agent_2', 'sess_2', 'payload', '{}', 'api', ?, 'v4', 'st', 'm', 'v', 384, 'RECEIVED')",
                (mut_id, cand_id, 200 + i, tenant_id, dataset_id, doc_id, rev_id, f"chunk_{i}", pipeline_run_id),
            )
        await db.commit()

    # Move Child 0 to COMMITTED, Child 1 to RETRY_PENDING, Child 2 stays RECEIVED
    async with engine.transaction() as db:
        await dao._transition_memory_mutation_in_tx(db, "mut_d002_0", to_state="COMMITTED")
        await dao._transition_memory_mutation_in_tx(db, "mut_d002_1", to_state="RETRY_PENDING")
        await db.commit()

    # Pipeline state MUST NOT be COMMITTED (it should be non-COMMITTED, e.g. RUNNING or RETRY_PENDING)
    pipeline_info = await dao.get_pipeline_run(pipeline_run_id)
    assert pipeline_info is not None
    assert pipeline_info["state"] != "COMMITTED"
    assert pipeline_info["state"] in ("RUNNING", "RETRY_PENDING", "PROJECTING")

    # Move Child 1 and Child 2 to COMMITTED
    async with engine.transaction() as db:
        await dao._transition_memory_mutation_in_tx(db, "mut_d002_1", to_state="COMMITTED")
        await dao._transition_memory_mutation_in_tx(db, "mut_d002_2", to_state="COMMITTED")
        await db.commit()

    # Now ALL children are COMMITTED -> pipeline run state becomes COMMITTED
    pipeline_info_final = await dao.get_pipeline_run(pipeline_run_id)
    assert pipeline_info_final is not None
    assert pipeline_info_final["state"] == "COMMITTED"

    await engine.close()
