"""Integrated Round 8 certification suite for Recovery, Durability, and V3 Compatibility."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from mesa_memory.retrieval.core import QueryAnalyzer
from mesa_memory.retrieval.hybrid import HybridRetriever
from mesa_memory.security.api_keys import APIKeyStore
from mesa_memory.security.rbac import AccessControl
from mesa_storage.dao import MemoryDAO
from mesa_storage.recovery import create_backup, restore_backup, validate_snapshot
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.writer_lock import StorageWriterLock


@pytest.mark.asyncio
async def test_integrated_backup_quiescence_and_canonical_readback(tmp_path: Path) -> None:
    """Track A: Supported production backup entrypoint -> restore -> canonical readback."""
    storage_root = tmp_path / "live_storage"
    storage_root.mkdir()
    db_path = storage_root / "mesa.db"

    # Seed canonical SQLite database
    conn = sqlite3.connect(db_path)
    conn.executescript("""
    CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY);
    INSERT INTO alembic_version VALUES ('fe5f6a7b8c9d');

    CREATE TABLE nodes (
        id TEXT PRIMARY KEY,
        entity_name TEXT NOT NULL,
        type TEXT NOT NULL DEFAULT 'ENTITY',
        content_payload TEXT NOT NULL DEFAULT '',
        is_consolidated INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        invalid_at TEXT DEFAULT NULL,
        deleted_at TEXT DEFAULT NULL,
        agent_id TEXT NOT NULL DEFAULT '__unset__',
        session_id TEXT NOT NULL DEFAULT '__unset__',
        confidence REAL DEFAULT 1.0,
        is_quarantined INTEGER DEFAULT 0
    );

    INSERT INTO nodes VALUES (
        'r8-canonical-node-1',
        'Quantum Computing Overview',
        'ENTITY',
        'Canonical state preserved across backup snapshot and restore',
        1,
        '2026-08-19T00:00:00Z',
        NULL,
        NULL,
        'r8-agent-primary',
        'r8-session-primary',
        1.0,
        0
    );
    """)
    conn.commit()
    conn.close()

    # Create dummy projection artifacts
    (storage_root / "vector.lance").mkdir()
    (storage_root / "vector.lance" / "metadata.bin").write_bytes(b"lance-vector-data")

    # Prove active writer lock fails closed
    backup_root = tmp_path / "production_backup"
    with StorageWriterLock.acquire(storage_root, owner="live-worker"):
        with pytest.raises(Exception, match="active writer"):
            create_backup(storage_root, backup_root, tmp_path, stores_stopped=True)

    # Perform supported production backup when quiescent
    backup_result = create_backup(storage_root, backup_root, tmp_path, stores_stopped=True)
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
    engine = AsyncEngine(str(restored_root / "mesa.db"))
    await engine.initialize()
    vec_mock = MagicMock()
    vec_mock.compute_embedding = AsyncMock(return_value=[0.0] * 384)
    vec_mock.search = AsyncMock(return_value=[])
    dao = MemoryDAO(sqlite_engine=engine, vector_engine=vec_mock)

    nodes = await dao.get_nodes_by_ids_batch("r8-agent-primary", ["r8-canonical-node-1"])
    assert "r8-canonical-node-1" in nodes
    node = nodes["r8-canonical-node-1"]
    assert node["entity_name"] == "Quantum Computing Overview"
    assert node["type"] == "ENTITY"
    assert node["content_payload"] == "Canonical state preserved across backup snapshot and restore"

    await engine.close()


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


@pytest.mark.asyncio
async def test_integrated_v3_cold_start_and_retrieval(tmp_path: Path) -> None:
    """Track C: Pre-V4 schema fixture cold-start -> legacy retrieval without V4 table assumption."""
    v3_db = tmp_path / "legacy_v3_store.db"
    conn = sqlite3.connect(v3_db)
    conn.executescript("""
    CREATE TABLE nodes (
        id TEXT PRIMARY KEY,
        entity_name TEXT NOT NULL,
        type TEXT NOT NULL DEFAULT 'ENTITY',
        content_payload TEXT NOT NULL DEFAULT '',
        is_consolidated INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        invalid_at TEXT DEFAULT NULL,
        deleted_at TEXT DEFAULT NULL,
        agent_id TEXT NOT NULL DEFAULT '__unset__',
        session_id TEXT NOT NULL DEFAULT '__unset__',
        confidence REAL DEFAULT 1.0,
        is_quarantined INTEGER DEFAULT 0
    );

    CREATE VIRTUAL TABLE nodes_fts USING fts5(
        entity_name,
        type,
        content='nodes',
        content_rowid='rowid'
    );

    INSERT INTO nodes VALUES (
        'v3-node-101',
        'European Union AI Act',
        'ENTITY',
        'Regulatory framework for artificial intelligence governance',
        1,
        '2026-01-15T12:00:00Z',
        NULL,
        NULL,
        'legal-agent-1',
        'session-legal-101',
        1.0,
        0
    );
    INSERT INTO nodes_fts (rowid, entity_name, type) VALUES (1, 'European Union AI Act', 'ENTITY');
    """)
    conn.commit()
    tables_before = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
    conn.close()

    assert "artifact_registry" not in tables_before

    engine = AsyncEngine(str(v3_db))
    await engine.initialize()
    vec_mock = MagicMock()
    vec_mock.compute_embedding = AsyncMock(return_value=[0.1] * 384)
    vec_mock.search = AsyncMock(return_value=[])
    dao = MemoryDAO(sqlite_engine=engine, vector_engine=vec_mock)

    rbac = AccessControl(policy_path=str(tmp_path / "rbac_v3.db"))
    await rbac.initialize()
    await rbac.grant_access("legal-agent-1", "session-legal-101", "READ")

    retriever = HybridRetriever(dao=dao, analyzer=QueryAnalyzer(), access_control=rbac)

    # Cold start count
    count = await dao.count_active_memories(tenant_id="legal-agent-1", agent_id="legal-agent-1")
    assert count == 1

    # Perform retrieval on legacy database
    results = await retriever.retrieve(
        "European Union AI Act",
        agent_id="legal-agent-1",
        session_id="session-legal-101",
    )
    assert results == ["v3-node-101"]

    await engine.close()

    # Reopen and repeat to verify persistence across restarts
    engine2 = AsyncEngine(str(v3_db))
    await engine2.initialize()
    dao2 = MemoryDAO(sqlite_engine=engine2, vector_engine=vec_mock)
    retriever2 = HybridRetriever(dao=dao2, analyzer=QueryAnalyzer(), access_control=rbac)
    results2 = await retriever2.retrieve(
        "European Union AI Act",
        agent_id="legal-agent-1",
        session_id="session-legal-101",
    )
    assert results2 == ["v3-node-101"]
    await engine2.close()

    # Verify no silent V4 schema mutation occurred
    conn = sqlite3.connect(v3_db)
    tables_after = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
    conn.close()
    assert set(tables_after) == set(tables_before)
