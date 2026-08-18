import asyncio
import sqlite3
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.dao import MemoryDAO
from mesa_memory.retrieval.hybrid import HybridRetriever
from mesa_memory.retrieval.core import QueryAnalyzer
from mesa_memory.security.rbac import AccessControl


def _create_v3_schema_database(db_path: Path) -> list[str]:
    """Create a truthful pre-V4 / V3 SQLite schema matching early MESA baseline."""
    conn = sqlite3.connect(db_path)
    conn.executescript("""
    CREATE TABLE alembic_version (
        version_num VARCHAR(32) NOT NULL,
        CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
    );
    INSERT INTO alembic_version (version_num) VALUES ('bb2355d0cdd4');

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

    CREATE TABLE routing_telemetry (
        id TEXT PRIMARY KEY,
        agent_id TEXT NOT NULL,
        record_id TEXT NOT NULL,
        small_model_decision INTEGER NOT NULL,
        small_model_confidence REAL NOT NULL,
        dual_llm_decision INTEGER NOT NULL,
        is_hallucination INTEGER NOT NULL,
        created_at TEXT NOT NULL
    );

    CREATE TABLE raw_logs (
        id TEXT PRIMARY KEY,
        agent_id TEXT NOT NULL DEFAULT '__unset__',
        session_id TEXT NOT NULL DEFAULT '__unset__',
        payload TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'queued',
        created_at TEXT NOT NULL
    );
    """)
    conn.commit()
    tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
    conn.close()
    return tables


@pytest.mark.asyncio
async def test_empty_v3_cold_start_no_v4_exception(tmp_path: Path):
    db_path = tmp_path / "empty_v3.db"
    initial_tables = _create_v3_schema_database(db_path)
    assert "artifact_registry" not in initial_tables
    assert "nodes" in initial_tables

    engine = AsyncEngine(str(db_path))
    await engine.initialize()
    vec_mock = MagicMock()
    vec_mock.compute_embedding = AsyncMock(return_value=[0.1] * 384)
    vec_mock.search = AsyncMock(return_value=[])
    dao = MemoryDAO(sqlite_engine=engine, vector_engine=vec_mock)

    rbac = AccessControl(policy_path=str(tmp_path / "rbac_empty.db"))
    await rbac.initialize()
    await rbac.grant_access("agent-empty", "session-empty", "READ")

    retriever = HybridRetriever(dao=dao, analyzer=QueryAnalyzer(), access_control=rbac)

    # Cold start count
    count = await dao.count_active_memories(tenant_id="agent-empty", agent_id="agent-empty")
    assert count == 0

    has_mem = await dao.has_active_memories(tenant_id="agent-empty")
    assert has_mem is False

    # Retrieval on empty V3 DB must succeed without missing table exception
    results = await retriever.retrieve("NonExistent", agent_id="agent-empty", session_id="session-empty")
    assert results == []

    await engine.close()

    # Verify no silent V4 schema was created during retrieval
    conn = sqlite3.connect(db_path)
    after_tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
    conn.close()
    assert set(after_tables) == set(initial_tables)
    assert "artifact_registry" not in after_tables


@pytest.mark.asyncio
async def test_non_empty_v3_cold_start_and_retrieval(tmp_path: Path):
    db_path = tmp_path / "populated_v3.db"
    initial_tables = _create_v3_schema_database(db_path)

    # Insert known legacy data into V3 schema
    conn = sqlite3.connect(db_path)
    conn.execute("""
    INSERT INTO nodes (id, entity_name, type, content_payload, created_at, agent_id, session_id, confidence, is_quarantined)
    VALUES ('node-v3-alice', 'Alice', 'ENTITY', 'Alice is an AI researcher at MESA', '2026-01-01T00:00:00Z', 'agent-v3', 'session-v3', 1.0, 0);
    """)
    conn.execute("INSERT INTO nodes_fts (rowid, entity_name, type) VALUES (1, 'Alice', 'ENTITY');")
    conn.commit()
    conn.close()

    engine = AsyncEngine(str(db_path))
    await engine.initialize()
    vec_mock = MagicMock()
    vec_mock.compute_embedding = AsyncMock(return_value=[0.1] * 384)
    vec_mock.search = AsyncMock(return_value=[])
    dao = MemoryDAO(sqlite_engine=engine, vector_engine=vec_mock)

    rbac = AccessControl(policy_path=str(tmp_path / "rbac_pop.db"))
    await rbac.initialize()
    await rbac.grant_access("agent-v3", "session-v3", "READ")

    retriever = HybridRetriever(dao=dao, analyzer=QueryAnalyzer(), access_control=rbac)

    # Verify active memory count
    count = await dao.count_active_memories(tenant_id="agent-v3", agent_id="agent-v3")
    assert count == 1

    has_mem = await dao.has_active_memories(tenant_id="agent-v3")
    assert has_mem is True

    # Retrieve known record
    results = await retriever.retrieve("Alice", agent_id="agent-v3", session_id="session-v3")
    assert results == ["node-v3-alice"]

    await engine.close()

    # Reopen DB and repeat retrieval to prove persistent correctness across processes
    engine2 = AsyncEngine(str(db_path))
    await engine2.initialize()
    dao2 = MemoryDAO(sqlite_engine=engine2, vector_engine=vec_mock)
    retriever2 = HybridRetriever(dao=dao2, analyzer=QueryAnalyzer(), access_control=rbac)
    results2 = await retriever2.retrieve("Alice", agent_id="agent-v3", session_id="session-v3")
    assert results2 == ["node-v3-alice"]
    await engine2.close()

    # Verify schema before and after remain identical
    conn = sqlite3.connect(db_path)
    after_tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
    conn.close()
    assert set(after_tables) == set(initial_tables)
