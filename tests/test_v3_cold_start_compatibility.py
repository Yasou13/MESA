import sqlite3
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from mesa_memory.retrieval.core import QueryAnalyzer
from mesa_memory.retrieval.hybrid import HybridRetriever
from mesa_memory.security.rbac import AccessControl
from mesa_storage.dao import MemoryDAO
from mesa_storage.sqlite_engine import AsyncEngine


def _create_v3_schema_database(db_path: Path) -> list[str]:
    """Create the supported predecessor at the repository's real Alembic revision."""
    project_root = Path(__file__).resolve().parents[1]
    migration_script = (
        "from alembic import command; from alembic.config import Config; "
        "import sys; "
        "config = Config(sys.argv[1]); "
        "config.set_main_option('sqlalchemy.url', f'sqlite+pysqlite:///{sys.argv[2]}'); "
        "command.upgrade(config, 'bb2355d0cdd4')"
    )
    subprocess.run(
        [
            sys.executable,
            "-c",
            migration_script,
            str(project_root / "mesa_storage" / "alembic.ini"),
            db_path.as_posix(),
        ],
        cwd=project_root,
        check=True,
    )

    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == (
        "bb2355d0cdd4",
    )
    tables = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    ]
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
    count = await dao.count_active_memories(
        tenant_id="agent-empty", agent_id="agent-empty"
    )
    assert count == 0

    has_mem = await dao.has_active_memories(tenant_id="agent-empty")
    assert has_mem is False

    # Retrieval on empty V3 DB must succeed without missing table exception
    results = await retriever.retrieve(
        "NonExistent", agent_id="agent-empty", session_id="session-empty"
    )
    assert results == []

    await engine.close()

    # Verify no silent V4 schema was created during retrieval
    conn = sqlite3.connect(db_path)
    after_tables = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    ]
    conn.close()
    assert set(after_tables) == set(initial_tables)
    assert "artifact_registry" not in after_tables


@pytest.mark.asyncio
async def test_v3_cold_start_counts_remain_tenant_scoped(tmp_path: Path):
    db_path = tmp_path / "tenant_scoped_v3.db"
    _create_v3_schema_database(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            "INSERT INTO nodes (id, entity_name, type, content_payload, created_at, agent_id, session_id) "
            "VALUES (?, ?, 'ENTITY', ?, '2026-01-01T00:00:00Z', ?, ?)",
            [
                (
                    "tenant-a-node",
                    "AlphaUnique",
                    "tenant A known memory",
                    "tenant-a",
                    "session-a",
                ),
                (
                    "tenant-b-node-1",
                    "BetaUnique",
                    "tenant B first memory",
                    "tenant-b",
                    "session-b",
                ),
                (
                    "tenant-b-node-2",
                    "BetaSecond",
                    "tenant B second memory",
                    "tenant-b",
                    "session-b",
                ),
            ],
        )

    engine = AsyncEngine(str(db_path))
    await engine.initialize()
    vec_mock = MagicMock()
    vec_mock.compute_embedding = AsyncMock(return_value=[0.1] * 384)
    vec_mock.search = AsyncMock(return_value=[])
    dao = MemoryDAO(sqlite_engine=engine, vector_engine=vec_mock)

    rbac = AccessControl(policy_path=str(tmp_path / "rbac_tenant_v3.db"))
    await rbac.initialize()
    await rbac.grant_access("tenant-a", "session-a", "READ")
    await rbac.grant_access("tenant-b", "session-b", "READ")
    retriever = HybridRetriever(dao=dao, analyzer=QueryAnalyzer(), access_control=rbac)

    assert await dao.count_active_memories(tenant_id="tenant-a") == 1
    assert await dao.has_active_memories(tenant_id="tenant-a")
    assert await dao.count_active_memories(tenant_id="tenant-b") == 2
    assert await dao.has_active_memories(tenant_id="tenant-b")
    assert await retriever.retrieve(
        "AlphaUnique", agent_id="tenant-a", session_id="session-a"
    ) == ["tenant-a-node"]
    assert await retriever.retrieve(
        "BetaUnique", agent_id="tenant-b", session_id="session-b"
    ) == ["tenant-b-node-1"]

    await engine.close()


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
    results = await retriever.retrieve(
        "Alice", agent_id="agent-v3", session_id="session-v3"
    )
    assert results == ["node-v3-alice"]

    await engine.close()

    # Reopen DB and repeat retrieval to prove persistent correctness across processes
    engine2 = AsyncEngine(str(db_path))
    await engine2.initialize()
    dao2 = MemoryDAO(sqlite_engine=engine2, vector_engine=vec_mock)
    retriever2 = HybridRetriever(
        dao=dao2, analyzer=QueryAnalyzer(), access_control=rbac
    )
    results2 = await retriever2.retrieve(
        "Alice", agent_id="agent-v3", session_id="session-v3"
    )
    assert results2 == ["node-v3-alice"]
    await engine2.close()

    # Verify schema before and after remain identical
    conn = sqlite3.connect(db_path)
    after_tables = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    ]
    conn.close()
    assert set(after_tables) == set(initial_tables)
