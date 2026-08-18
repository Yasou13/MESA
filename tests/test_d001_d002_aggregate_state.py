"""Adversarial regression test suite for Task D001 (Aggregate Revision Activation Barrier)
and Task D002 (Aggregate Pipeline State Derivation)."""

from types import SimpleNamespace

import pytest

from mesa_storage.dao import MemoryDAO
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine


async def _add_projection_outbox(db, mutation_id: str) -> None:
    for proj in ("SQL", "VECTOR", "GRAPH"):
        await db.execute(
            "INSERT INTO projection_outbox (projection_id, mutation_id, projection_name, state) "
            "VALUES (?, ?, ?, 'PENDING')",
            (f"proj_{mutation_id}_{proj}", mutation_id, proj),
        )


async def _complete_projection(db, mutation_id: str) -> None:
    # Transition all outbox projections to COMPLETED
    await db.execute(
        "UPDATE projection_outbox SET state = 'COMPLETED' WHERE mutation_id = ?",
        (mutation_id,),
    )
    async with db.execute(
        "SELECT state FROM memory_mutations WHERE mutation_id = ?", (mutation_id,)
    ) as cur:
        row = await cur.fetchone()
        curr = row[0] if row else "RECEIVED"

    if curr == "RECEIVED":
        await MemoryDAO._transition_memory_mutation_in_tx(
            db, mutation_id, to_state="EXTRACTED"
        )
        await MemoryDAO._transition_memory_mutation_in_tx(
            db, mutation_id, to_state="VALIDATED"
        )
        await MemoryDAO._transition_memory_mutation_in_tx(
            db, mutation_id, to_state="SQL_APPLIED"
        )
        await MemoryDAO._transition_memory_mutation_in_tx(
            db, mutation_id, to_state="VECTOR_APPLIED"
        )
        await MemoryDAO._transition_memory_mutation_in_tx(
            db, mutation_id, to_state="GRAPH_APPLIED"
        )
        await MemoryDAO._transition_memory_mutation_in_tx(
            db, mutation_id, to_state="COMMITTED"
        )
    elif curr == "DEAD_LETTER":
        await MemoryDAO._transition_memory_mutation_in_tx(
            db, mutation_id, to_state="RETRY_PENDING"
        )
        await MemoryDAO._transition_memory_mutation_in_tx(
            db, mutation_id, to_state="PENDING"
        )
        await MemoryDAO._transition_memory_mutation_in_tx(
            db, mutation_id, to_state="SQL_APPLIED"
        )
        await MemoryDAO._transition_memory_mutation_in_tx(
            db, mutation_id, to_state="VECTOR_APPLIED"
        )
        await MemoryDAO._transition_memory_mutation_in_tx(
            db, mutation_id, to_state="GRAPH_APPLIED"
        )
        await MemoryDAO._transition_memory_mutation_in_tx(
            db, mutation_id, to_state="COMMITTED"
        )
    elif curr == "RETRY_PENDING":
        await MemoryDAO._transition_memory_mutation_in_tx(
            db, mutation_id, to_state="PENDING"
        )
        await MemoryDAO._transition_memory_mutation_in_tx(
            db, mutation_id, to_state="SQL_APPLIED"
        )
        await MemoryDAO._transition_memory_mutation_in_tx(
            db, mutation_id, to_state="VECTOR_APPLIED"
        )
        await MemoryDAO._transition_memory_mutation_in_tx(
            db, mutation_id, to_state="GRAPH_APPLIED"
        )
        await MemoryDAO._transition_memory_mutation_in_tx(
            db, mutation_id, to_state="COMMITTED"
        )
    elif curr in ("VALIDATED", "PENDING"):
        await MemoryDAO._transition_memory_mutation_in_tx(
            db, mutation_id, to_state="SQL_APPLIED"
        )
        await MemoryDAO._transition_memory_mutation_in_tx(
            db, mutation_id, to_state="VECTOR_APPLIED"
        )
        await MemoryDAO._transition_memory_mutation_in_tx(
            db, mutation_id, to_state="GRAPH_APPLIED"
        )
        await MemoryDAO._transition_memory_mutation_in_tx(
            db, mutation_id, to_state="COMMITTED"
        )


@pytest.mark.asyncio
async def test_d001_aggregate_revision_activation_barrier(tmp_path):
    """Verify that a document revision with 3 child mutations does NOT become ACTIVE
    when only 1 or 2 child mutations commit, and only becomes ACTIVE when ALL 3 commit.
    """
    db_path = str(tmp_path / "mesa_test_d001.db")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)

    dao = MemoryDAO(engine, SimpleNamespace())
    tenant_id = "tenant_d001"
    dataset_id = "dataset_d001"
    doc_id = "doc_d001"
    rev_id = "rev_d001"

    await dao.ensure_v4_catalog_scope(
        tenant_id=tenant_id, workspace_id="ws_1", dataset_id=dataset_id
    )
    await dao.create_v4_document(
        tenant_id=tenant_id, dataset_id=dataset_id, document_id=doc_id, title="Doc 1"
    )
    await dao.create_v4_revision(
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        document_id=doc_id,
        revision_id=rev_id,
        revision_number=1,
        content_hash="a" * 64,
    )

    # 3 source chunks belonging to the same revision
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
            finalize_revision=i == 2,
        )

    pipeline_run_id = "pipe_d001"
    async with engine.connection() as db:
        p_ws = await dao._catalog.resolve_id_in_tx(
            db, tenant_id=tenant_id, kind="workspace", external_id="ws_1"
        )
        p_ds = await dao._catalog.resolve_id_in_tx(
            db, tenant_id=tenant_id, kind="dataset", external_id=dataset_id
        )
        p_doc = await dao._catalog.resolve_id_in_tx(
            db, tenant_id=tenant_id, kind="document", external_id=doc_id
        )
        p_rev = await dao._catalog.resolve_id_in_tx(
            db, tenant_id=tenant_id, kind="revision", external_id=rev_id
        )
        p_chunks = [
            await dao._catalog.resolve_id_in_tx(
                db, tenant_id=tenant_id, kind="chunk", external_id=f"chunk_{i}"
            )
            for i in range(3)
        ]

    async with engine.transaction() as db:
        await db.execute(
            "INSERT INTO pipeline_runs (pipeline_run_id, tenant_id, workspace_id, dataset_id, session_id, agent_id, state) "
            "VALUES (?, ?, ?, ?, 'sess_1', 'agent_1', 'RUNNING')",
            (pipeline_run_id, tenant_id, p_ws, p_ds),
        )
        for i in range(3):
            mut_id = f"mut_d001_{i}"
            cand_id = f"cand_d001_{i}"
            await db.execute(
                "INSERT INTO memory_mutations (mutation_id, candidate_id, raw_log_id, tenant_id, workspace_id, "
                "dataset_id, document_id, revision_id, chunk_id, source_ref, evidence_span, agent_id, session_id, "
                "content_payload, metadata_json, source, pipeline_run_id, extraction_version, embedding_provider, "
                "embedding_model, embedding_version, embedding_dimension, state) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ref', '0:5', 'agent_1', 'sess_1', 'payload', '{}', 'api', ?, 'v4', 'st', 'm', 'v', 384, 'RECEIVED')",
                (
                    mut_id,
                    cand_id,
                    100 + i,
                    tenant_id,
                    p_ws,
                    p_ds,
                    p_doc,
                    p_rev,
                    p_chunks[i],
                    pipeline_run_id,
                ),
            )
            await _add_projection_outbox(db, mut_id)
        await db.commit()

    # Move Child A to COMMITTED, Child B to RETRY_PENDING, Child C to DEAD_LETTER
    async with engine.transaction() as db:
        await _complete_projection(db, "mut_d001_0")
        await dao._transition_memory_mutation_in_tx(
            db, "mut_d001_1", to_state="RETRY_PENDING"
        )
        await dao._transition_memory_mutation_in_tx(
            db, "mut_d001_2", to_state="DEAD_LETTER"
        )
        await db.commit()

    # Revision must REMAIN PENDING (non-ACTIVE)
    async with engine.connection() as db:
        async with db.execute(
            "SELECT status FROM document_revisions WHERE revision_id = ?", (p_rev,)
        ) as cur:
            row = await cur.fetchone()
            assert row[0] == "PENDING"

    # Now complete Child B and Child C to COMMITTED
    async with engine.transaction() as db:
        await _complete_projection(db, "mut_d001_1")
        await dao._transition_memory_mutation_in_tx(
            db, "mut_d001_2", to_state="RETRY_PENDING"
        )
        await _complete_projection(db, "mut_d001_2")
        await db.commit()

    # Now all 3 children are COMMITTED -> revision must become ACTIVE exactly once
    async with engine.connection() as db:
        async with db.execute(
            "SELECT status FROM document_revisions WHERE revision_id = ?", (p_rev,)
        ) as cur:
            row = await cur.fetchone()
            assert row[0] == "ACTIVE"

    await engine.close()


@pytest.mark.asyncio
async def test_d002_aggregate_pipeline_state_derivation(tmp_path):
    """Verify that parent pipeline state is derived from aggregate child mutation state
    and single child mutation cannot directly declare a multi-child pipeline COMMITTED.
    """
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

    await dao.ensure_v4_catalog_scope(
        tenant_id=tenant_id, workspace_id="ws_2", dataset_id=dataset_id
    )
    await dao.create_v4_document(
        tenant_id=tenant_id, dataset_id=dataset_id, document_id=doc_id, title="Doc 2"
    )
    await dao.create_v4_revision(
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        document_id=doc_id,
        revision_id=rev_id,
        revision_number=1,
        content_hash="b" * 64,
    )

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
            finalize_revision=i == 2,
        )

    async with engine.connection() as db:
        p_ws = await dao._catalog.resolve_id_in_tx(
            db, tenant_id=tenant_id, kind="workspace", external_id="ws_2"
        )
        p_ds = await dao._catalog.resolve_id_in_tx(
            db, tenant_id=tenant_id, kind="dataset", external_id=dataset_id
        )
        p_doc = await dao._catalog.resolve_id_in_tx(
            db, tenant_id=tenant_id, kind="document", external_id=doc_id
        )
        p_rev = await dao._catalog.resolve_id_in_tx(
            db, tenant_id=tenant_id, kind="revision", external_id=rev_id
        )
        p_chunks = [
            await dao._catalog.resolve_id_in_tx(
                db, tenant_id=tenant_id, kind="chunk", external_id=f"chunk_{i}"
            )
            for i in range(3)
        ]

    async with engine.transaction() as db:
        await db.execute(
            "INSERT INTO pipeline_runs (pipeline_run_id, tenant_id, workspace_id, dataset_id, session_id, agent_id, state) "
            "VALUES (?, ?, ?, ?, 'sess_2', 'agent_2', 'RUNNING')",
            (pipeline_run_id, tenant_id, p_ws, p_ds),
        )
        for i in range(3):
            mut_id = f"mut_d002_{i}"
            cand_id = f"cand_d002_{i}"
            await db.execute(
                "INSERT INTO memory_mutations (mutation_id, candidate_id, raw_log_id, tenant_id, workspace_id, "
                "dataset_id, document_id, revision_id, chunk_id, source_ref, evidence_span, agent_id, session_id, "
                "content_payload, metadata_json, source, pipeline_run_id, extraction_version, embedding_provider, "
                "embedding_model, embedding_version, embedding_dimension, state) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ref', '0:5', 'agent_2', 'sess_2', 'payload', '{}', 'api', ?, 'v4', 'st', 'm', 'v', 384, 'RECEIVED')",
                (
                    mut_id,
                    cand_id,
                    200 + i,
                    tenant_id,
                    p_ws,
                    p_ds,
                    p_doc,
                    p_rev,
                    p_chunks[i],
                    pipeline_run_id,
                ),
            )
            await _add_projection_outbox(db, mut_id)
        await db.commit()

    # Move Child 0 to COMMITTED, Child 1 to RETRY_PENDING, Child 2 stays RECEIVED
    async with engine.transaction() as db:
        await _complete_projection(db, "mut_d002_0")
        await dao._transition_memory_mutation_in_tx(
            db, "mut_d002_1", to_state="RETRY_PENDING"
        )
        await db.commit()

    # Pipeline state MUST NOT be COMMITTED (it should be non-COMMITTED, e.g. RUNNING or RETRY_PENDING)
    pipeline_info = await dao.get_pipeline_run(pipeline_run_id)
    assert pipeline_info is not None
    assert pipeline_info["state"] != "COMMITTED"
    assert pipeline_info["state"] in ("RUNNING", "RETRY_PENDING", "PROJECTING")

    # Move Child 1 and Child 2 to COMMITTED
    async with engine.transaction() as db:
        await _complete_projection(db, "mut_d002_1")
        await _complete_projection(db, "mut_d002_2")
        await db.commit()

    # Now ALL children are COMMITTED -> pipeline run state becomes COMMITTED
    pipeline_info_final = await dao.get_pipeline_run(pipeline_run_id)
    assert pipeline_info_final is not None
    assert pipeline_info_final["state"] == "COMMITTED"

    await engine.close()


@pytest.mark.asyncio
async def test_revision_cannot_activate_before_manifest_is_frozen(tmp_path):
    engine = AsyncEngine(str(tmp_path / "manifest-freeze.db"))
    await engine.initialize()
    await initialize_schema(engine)
    dao = MemoryDAO(engine, SimpleNamespace())
    await dao.ensure_v4_catalog_scope(
        tenant_id="tenant-freeze", workspace_id="ws", dataset_id="ds"
    )
    await dao.create_v4_document(
        tenant_id="tenant-freeze", dataset_id="ds", document_id="doc", title="Doc"
    )
    await dao.create_v4_revision(
        tenant_id="tenant-freeze",
        dataset_id="ds",
        document_id="doc",
        revision_id="rev",
        revision_number=1,
        content_hash="f" * 64,
    )
    await dao.create_v4_source_chunk(
        tenant_id="tenant-freeze",
        dataset_id="ds",
        document_id="doc",
        revision_id="rev",
        chunk_id="chunk-1",
        title="Doc",
        content_payload="first",
        source_ref="test",
        finalize_revision=False,
    )

    async with engine.connection() as db:
        p_ws = await dao._catalog.resolve_id_in_tx(
            db, tenant_id="tenant-freeze", kind="workspace", external_id="ws"
        )
        p_ds = await dao._catalog.resolve_id_in_tx(
            db, tenant_id="tenant-freeze", kind="dataset", external_id="ds"
        )
        p_doc = await dao._catalog.resolve_id_in_tx(
            db, tenant_id="tenant-freeze", kind="document", external_id="doc"
        )
        p_rev = await dao._catalog.resolve_id_in_tx(
            db, tenant_id="tenant-freeze", kind="revision", external_id="rev"
        )
        p_chunk = await dao._catalog.resolve_id_in_tx(
            db, tenant_id="tenant-freeze", kind="chunk", external_id="chunk-1"
        )

    async with engine.transaction() as db:
        await db.execute(
            "INSERT INTO pipeline_runs (pipeline_run_id, tenant_id, workspace_id, "
            "dataset_id, session_id, agent_id, state) "
            "VALUES ('pipe-freeze', 'tenant-freeze', ?, ?, 'session', "
            "'agent', 'RUNNING')",
            (p_ws, p_ds),
        )
        await db.execute(
            "INSERT INTO memory_mutations (mutation_id, candidate_id, raw_log_id, "
            "tenant_id, workspace_id, dataset_id, document_id, revision_id, chunk_id, "
            "source_ref, evidence_span, agent_id, session_id, content_payload, "
            "metadata_json, source, pipeline_run_id, extraction_version, "
            "embedding_provider, embedding_model, embedding_version, "
            "embedding_dimension, state) VALUES ('mut-freeze', 'candidate-freeze', 1, "
            "'tenant-freeze', ?, ?, ?, ?, ?, 'test', '', "
            "'agent', 'session', 'first', '{}', 'api', 'pipe-freeze', 'v4', "
            "'local', 'model', 'v1', 384, 'RECEIVED')",
            (p_ws, p_ds, p_doc, p_rev, p_chunk),
        )
        await _add_projection_outbox(db, "mut-freeze")
        await _complete_projection(db, "mut-freeze")
        await db.commit()
    revisions = await dao.list_v4_revisions(
        tenant_id="tenant-freeze", dataset_id="ds", document_id="doc"
    )
    assert revisions[0]["status"] == "PENDING"
    await engine.close()
