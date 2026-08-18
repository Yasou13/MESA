"""Adversarial regression test suite for Task D003 (Descendant-Aware Historical Rollback)
and Task D004 (Separate Content Hash and Manifest Hash)."""

from types import SimpleNamespace

import pytest

from mesa_storage.dao import MemoryDAO, NonHeadRollbackConflictError
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine


@pytest.mark.asyncio
async def test_d003_non_head_historical_rollback_rejection(tmp_path):
    """Verify that attempting to rollback a pipeline producing a non-head historical revision
    is rejected with typed 409 NON_HEAD_ROLLBACK_CONFLICT and does not reactivate predecessors.
    """
    db_path = str(tmp_path / "mesa_test_d003.db")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)

    dao = MemoryDAO(engine, SimpleNamespace())
    tenant_id = "tenant_d003"
    dataset_id = "dataset_d003"
    doc_id = "doc_d003"

    await dao.ensure_v4_catalog_scope(
        tenant_id=tenant_id, workspace_id="ws_3", dataset_id=dataset_id
    )
    await dao.create_v4_document(
        tenant_id=tenant_id, dataset_id=dataset_id, document_id=doc_id, title="Doc 3"
    )

    # Set up R1 (ACTIVE) -> R2 (SUPERSEDED by R3) -> R3 (ACTIVE current head)
    await dao.create_v4_revision(
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        document_id=doc_id,
        revision_id="r1",
        revision_number=1,
        content_hash="1" * 64,
    )
    await dao.create_v4_revision(
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        document_id=doc_id,
        revision_id="r2",
        revision_number=2,
        supersedes_revision_id="r1",
        content_hash="2" * 64,
    )
    await dao.create_v4_revision(
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        document_id=doc_id,
        revision_id="r3",
        revision_number=3,
        supersedes_revision_id="r2",
        content_hash="3" * 64,
    )

    async with engine.connection() as db:
        p_r1 = await dao._catalog.resolve_id_in_tx(
            db, tenant_id=tenant_id, kind="revision", external_id="r1"
        )
        p_r2 = await dao._catalog.resolve_id_in_tx(
            db, tenant_id=tenant_id, kind="revision", external_id="r2"
        )
        p_r3 = await dao._catalog.resolve_id_in_tx(
            db, tenant_id=tenant_id, kind="revision", external_id="r3"
        )
        p_doc = await dao._catalog.resolve_id_in_tx(
            db, tenant_id=tenant_id, kind="document", external_id=doc_id
        )
        p_ds = await dao._catalog.resolve_id_in_tx(
            db, tenant_id=tenant_id, kind="dataset", external_id=dataset_id
        )
        p_ws = await dao._catalog.resolve_id_in_tx(
            db, tenant_id=tenant_id, kind="workspace", external_id="ws_3"
        )

    async with engine.transaction() as db:
        await db.execute(
            "UPDATE document_revisions SET status = 'SUPERSEDED' WHERE revision_id IN (?, ?)",
            (p_r1, p_r2),
        )
        await db.execute(
            "UPDATE document_revisions SET status = 'ACTIVE' WHERE revision_id = ?",
            (p_r3,),
        )

        # Pipeline 2 produced r2
        await db.execute(
            "INSERT INTO pipeline_runs (pipeline_run_id, tenant_id, workspace_id, dataset_id, session_id, agent_id, state) "
            "VALUES ('pipe_r2', ?, ?, ?, 'sess_3', 'agent_3', 'COMMITTED')",
            (tenant_id, p_ws, p_ds),
        )
        await db.execute(
            "INSERT INTO memory_mutations (mutation_id, candidate_id, raw_log_id, tenant_id, workspace_id, "
            "dataset_id, document_id, revision_id, chunk_id, source_ref, evidence_span, agent_id, session_id, "
            "content_payload, metadata_json, source, pipeline_run_id, extraction_version, embedding_provider, "
            "embedding_model, embedding_version, embedding_dimension, state) "
            "VALUES ('mut_r2', 'cand_r2', 300, ?, ?, ?, ?, ?, 'chunk_r2', 'ref', '0:5', 'agent_3', 'sess_3', 'p', '{}', 'api', 'pipe_r2', 'v4', 'st', 'm', 'v', 384, 'COMMITTED')",
            (tenant_id, p_ws, p_ds, p_doc, p_r2),
        )
        await db.commit()

    # Attempt rollback of pipe_r2 (which produced non-head revision r2)
    with pytest.raises(
        NonHeadRollbackConflictError, match="409 NON_HEAD_ROLLBACK_CONFLICT"
    ):
        await dao.request_pipeline_rollback("pipe_r2")

    # Verify R3 remains ACTIVE and R1 was NOT reactivated
    async with engine.connection() as db:
        async with db.execute(
            "SELECT revision_id, status FROM document_revisions WHERE document_id = ? AND status = 'ACTIVE'",
            (p_doc,),
        ) as cur:
            active_rows = await cur.fetchall()
            assert len(active_rows) == 1
            assert active_rows[0][0] == p_r3

    await engine.close()


@pytest.mark.asyncio
async def test_d004_separate_content_and_manifest_hash(tmp_path):
    """Verify that caller-declared content_hash is preserved and not overwritten by manifest_hash."""
    db_path = str(tmp_path / "mesa_test_d004.db")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)

    dao = MemoryDAO(engine, SimpleNamespace())
    tenant_id = "tenant_d004"
    dataset_id = "dataset_d004"
    doc_id = "doc_d004"
    rev_id = "rev_d004"
    declared_content_hash = "d" * 64

    await dao.ensure_v4_catalog_scope(
        tenant_id=tenant_id, workspace_id="ws_4", dataset_id=dataset_id
    )
    await dao.create_v4_document(
        tenant_id=tenant_id, dataset_id=dataset_id, document_id=doc_id, title="Doc 4"
    )

    # Create revision with explicit declared content_hash
    await dao.create_v4_revision(
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        document_id=doc_id,
        revision_id=rev_id,
        revision_number=1,
        content_hash=declared_content_hash,
    )

    # Add source chunk to update manifest
    chunk = await dao.create_v4_source_chunk(
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        document_id=doc_id,
        revision_id=rev_id,
        chunk_id="chk_d004",
        title="Title 4",
        content_payload="Payload 4",
        source_ref="ref_4",
    )

    assert chunk["manifest_hash"] is not None

    # Check that declared_content_hash remains preserved and manifest_hash is separate
    async with engine.connection() as db:
        p_rev = await dao._catalog.resolve_id_in_tx(
            db, tenant_id=tenant_id, kind="revision", external_id=rev_id
        )
        async with db.execute(
            "SELECT declared_content_hash, manifest_hash FROM document_revisions WHERE revision_id = ?",
            (p_rev,),
        ) as cur:
            row = await cur.fetchone()
            assert row is not None
            assert row[0] == declared_content_hash
            assert row[1] is not None
            assert row[1] != declared_content_hash

    # Also verify list_v4_revisions preserves declared_content_hash
    revisions = await dao.list_v4_revisions(
        tenant_id=tenant_id, dataset_id=dataset_id, document_id=doc_id
    )
    assert len(revisions) == 1
    assert revisions[0]["declared_content_hash"] == declared_content_hash
    assert revisions[0]["manifest_hash"] != declared_content_hash

    await engine.close()


@pytest.mark.asyncio
async def test_d003_pending_revision_rollback_keeps_predecessor_head(tmp_path):
    engine = AsyncEngine(str(tmp_path / "pending-rollback.db"))
    await engine.initialize()
    await initialize_schema(engine)
    dao = MemoryDAO(engine, SimpleNamespace())
    await dao.ensure_v4_catalog_scope(
        tenant_id="tenant-pending", workspace_id="ws", dataset_id="ds"
    )
    await dao.create_v4_document(
        tenant_id="tenant-pending",
        dataset_id="ds",
        document_id="doc",
        title="Document",
    )
    await dao.create_v4_revision(
        tenant_id="tenant-pending",
        dataset_id="ds",
        document_id="doc",
        revision_id="r1",
        revision_number=1,
        content_hash="1" * 64,
    )
    await dao.create_v4_revision(
        tenant_id="tenant-pending",
        dataset_id="ds",
        document_id="doc",
        revision_id="r2",
        revision_number=2,
        content_hash="2" * 64,
        supersedes_revision_id="r1",
    )
    async with engine.connection() as db:
        p_r1 = await dao._catalog.resolve_id_in_tx(
            db, tenant_id="tenant-pending", kind="revision", external_id="r1"
        )
        p_r2 = await dao._catalog.resolve_id_in_tx(
            db, tenant_id="tenant-pending", kind="revision", external_id="r2"
        )
        p_doc = await dao._catalog.resolve_id_in_tx(
            db, tenant_id="tenant-pending", kind="document", external_id="doc"
        )
        p_ds = await dao._catalog.resolve_id_in_tx(
            db, tenant_id="tenant-pending", kind="dataset", external_id="ds"
        )
        p_ws = await dao._catalog.resolve_id_in_tx(
            db, tenant_id="tenant-pending", kind="workspace", external_id="ws"
        )

    async with engine.transaction() as db:
        await db.execute(
            "UPDATE document_revisions SET status = 'ACTIVE' WHERE revision_id = ?",
            (p_r1,),
        )
        await db.execute(
            "INSERT INTO pipeline_runs (pipeline_run_id, tenant_id, workspace_id, "
            "dataset_id, session_id, agent_id, state) VALUES "
            "('pending-pipeline', 'tenant-pending', ?, ?, 'session', "
            "'agent', 'RUNNING')",
            (p_ws, p_ds),
        )
        await db.execute(
            "INSERT INTO memory_mutations (mutation_id, candidate_id, raw_log_id, "
            "tenant_id, workspace_id, dataset_id, document_id, revision_id, chunk_id, "
            "source_ref, evidence_span, agent_id, session_id, content_payload, "
            "metadata_json, source, pipeline_run_id, extraction_version, "
            "embedding_provider, embedding_model, embedding_version, "
            "embedding_dimension, state) VALUES ('pending-mutation', 'candidate', 1, "
            "'tenant-pending', ?, ?, ?, ?, 'chunk', 'test', '', "
            "'agent', 'session', 'payload', '{}', 'api', 'pending-pipeline', 'v4', "
            "'local', 'model', 'v1', 384, 'RECEIVED')",
            (p_ws, p_ds, p_doc, p_r2),
        )
        await db.commit()

    result = await dao.request_pipeline_rollback("pending-pipeline")
    assert result["state"] == "ROLLED_BACK"
    async with engine.connection() as db:
        async with db.execute(
            "SELECT revision_id, status FROM document_revisions ORDER BY revision_number"
        ) as cursor:
            rows = [tuple(row) for row in await cursor.fetchall()]
            assert rows == [(p_r1, "ACTIVE"), (p_r2, "ROLLED_BACK")]
    await engine.close()
