"""Integrated certification suite for V4 Recovery and SQLite Production Durability."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from mesa_memory.config import QueueAdmissionPolicy
from mesa_memory.embedding.service import EmbeddingIdentity
from mesa_memory.security.api_keys import APIKeyStore
from mesa_memory.security.rbac import AccessControl
from mesa_storage.dao import MemoryDAO
from mesa_storage.recovery import create_backup, restore_backup, validate_snapshot
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.writer_lock import StorageWriterLock
from mesa_workers.projection_worker import process_projection_outbox_once


@pytest.mark.asyncio
async def test_integrated_backup_quiescence_and_canonical_readback(
    tmp_path: Path,
) -> None:
    """Track A: Supported production backup entrypoint -> restore -> V4 canonical readback."""
    storage_root = tmp_path / "live_storage"
    storage_root.mkdir()
    db_path = storage_root / "mesa.db"

    engine = AsyncEngine(str(db_path))
    await engine.initialize()
    await initialize_schema(engine)
    vec_mock = MagicMock()
    vec_mock.is_initialized = True
    vec_mock.semantic_operational = False
    vec_mock.compute_embedding = AsyncMock(return_value=[0.0] * 8)
    vec_mock.search = AsyncMock(return_value=[])
    dao = MemoryDAO(
        sqlite_engine=engine,
        vector_engine=vec_mock,
        embedding_enabled=False,
        graph_enabled=False,
    )
    await dao.ensure_v4_catalog_scope(
        tenant_id="r8-tenant", workspace_id="ws-r8", dataset_id="ds-r8"
    )
    sess = await dao.create_v4_session(
        tenant_id="r8-tenant",
        workspace_id="ws-r8",
        dataset_ids=["ds-r8"],
        agent_id="r8-agent-primary",
        principal_id="r8-principal",
    )
    identity = EmbeddingIdentity(
        provider="test", model="r8", version="v1", dimension=8, normalized=True
    )
    admission = await dao.admit_v4_memory(
        tenant_id="r8-tenant",
        workspace_id="ws-r8",
        dataset_id="ds-r8",
        agent_id="r8-agent-primary",
        session_id=sess["session_id"],
        document_id="r8-doc-1",
        revision_id="r8-rev-1",
        chunk_id="r8-chunk-0",
        title="Quantum Computing Overview",
        content_payload="Canonical state preserved across backup snapshot and restore",
        source_ref="file://quantum.txt",
        evidence_span="Canonical state preserved across backup snapshot and restore",
        revision_number=1,
        chunk_ordinal=0,
        supersedes_revision_id=None,
        metadata={},
        embedding_provider=identity.provider,
        embedding_model=identity.model,
        embedding_version=identity.version,
        embedding_dimension=identity.dimension,
        embedding_space_id=identity.embedding_space_id,
        embedding_normalized=True,
        policy=QueueAdmissionPolicy(),
        validation_mode=0,
    )
    mutation_id = admission["response"]["mutation_id"]
    await dao.record_mutation_extraction("r8-agent-primary", mutation_id, [])
    await dao.set_mutation_state("r8-agent-primary", mutation_id, "VALIDATED")
    for _ in range(4):
        res = await process_projection_outbox_once(dao, worker_id="r8-seed", limit=5)
        if res["claimed"] == 0:
            break
    await engine.close()

    # Create dummy projection artifact
    (storage_root / "vector.lance").mkdir()
    (storage_root / "vector.lance" / "metadata.bin").write_bytes(b"lance-vector-data")

    # Prove active writer lock fails closed
    backup_root = tmp_path / "production_backup"
    with StorageWriterLock.acquire(storage_root, owner="live-worker"):
        with pytest.raises(Exception, match="active writer"):
            create_backup(storage_root, backup_root, tmp_path, stores_stopped=True)

    # Perform supported production backup when quiescent
    backup_result = create_backup(
        storage_root, backup_root, tmp_path, stores_stopped=True
    )
    assert backup_result["valid"] is True
    assert "mesa.db" in backup_result["sqlite"]

    # Restore backup into fresh isolated storage location
    restored_root = tmp_path / "restored_storage"
    restore_result = restore_backup(backup_root, restored_root, tmp_path)
    assert restore_result["restored"] is True

    # Validate snapshot integrity
    snapshot_validation = validate_snapshot(restored_root)
    assert snapshot_validation["valid"] is True

    # Readback through canonical AsyncEngine & MemoryDAO
    restored_engine = AsyncEngine(str(restored_root / "mesa.db"))
    await restored_engine.initialize()
    try:
        restored_dao = MemoryDAO(
            sqlite_engine=restored_engine,
            vector_engine=vec_mock,
            embedding_enabled=False,
            graph_enabled=False,
        )
        found = await restored_dao.search_v4_memory(
            tenant_id="r8-tenant",
            agent_id="r8-agent-primary",
            dataset_ids=["ds-r8"],
            query="backup snapshot restore",
            limit=5,
            graph_enabled=False,
        )
        assert any(r["document_id"] == "r8-doc-1" for r in found)
    finally:
        await restored_engine.close()


@pytest.mark.asyncio
async def test_integrated_sqlite_production_durability(tmp_path: Path) -> None:
    """Track B: Production SQLite connection factories request durable FULL synchronization."""
    db_file = tmp_path / "durability_canonical.db"
    engine = AsyncEngine(str(db_file))
    await engine.initialize()
    assert engine.synchronous_mode == "FULL"

    async with engine.connection() as db:
        async with db.execute("PRAGMA synchronous;") as cursor:
            row = await cursor.fetchone()
            assert row[0] == 2  # 2 is FULL in SQLite numeric PRAGMA

        async with db.execute("PRAGMA journal_mode;") as cursor:
            row = await cursor.fetchone()
            assert row[0].upper() == "WAL"

    await engine.close()

    # AccessControl / RBAC connection durability
    rbac_file = str(tmp_path / "rbac_durability.db")
    ac = AccessControl(policy_path=rbac_file)
    await ac.initialize()
    async with ac._connect() as db:
        async with db.execute("PRAGMA synchronous;") as cursor:
            row = await cursor.fetchone()
            assert row[0] == 2

    # APIKeyStore credential connection durability
    store = APIKeyStore(policy_path=rbac_file)
    await store.initialize()
    async with store._connect() as db:
        async with db.execute("PRAGMA synchronous;") as cursor:
            row = await cursor.fetchone()
            assert row[0] == 2
