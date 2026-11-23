"""P16-04 (D79): Kùzu removal regression tests (old-store migration, readiness, retired env vars)."""

from __future__ import annotations

import importlib
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from alembic import command
from alembic.config import Config

from mesa_storage.dao import MemoryDAO
from mesa_storage.sqlite_engine import AsyncEngine


def _alembic_config(database: Path) -> Config:
    root = Path(__file__).resolve().parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "mesa_storage" / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    return cfg


def test_graph_package_is_not_installed() -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("k" + "uzu")


@pytest.mark.asyncio
async def test_legacy_store_with_graph_outbox_rows_migrates_and_reaches_readiness(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "legacy_graph_store.db"
    cfg = _alembic_config(db_path)
    command.upgrade(cfg, "f8b9c0d1e2f3")

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO pipeline_runs (pipeline_run_id, tenant_id, session_id, agent_id, state) "
            "VALUES ('pipe-old', 'tenant-a', 'sess-a', 'agent-a', 'PROJECTING')"
        )
        conn.execute(
            "INSERT INTO memory_mutations (mutation_id, candidate_id, session_id, agent_id, tenant_id, content_payload, state) "
            "VALUES ('mut-old', 'cand-old', 'sess-a', 'agent-a', 'tenant-a', 'legacy payload', 'VECTOR_APPLIED')"
        )
        conn.execute(
            "INSERT INTO projection_outbox (projection_id, mutation_id, projection_name, state) "
            "VALUES ('p-sql', 'mut-old', 'SQL', 'COMPLETED'), "
            "('p-vec', 'mut-old', 'VECTOR', 'SKIPPED'), "
            "('p-graph', 'mut-old', 'GRAPH', 'DEAD_LETTER')"
        )
        conn.execute(
            "INSERT INTO projection_attempts (attempt_id, projection_id, attempt_number, outcome) "
            "VALUES ('att-graph-1', 'p-graph', 1, 'FAILED')"
        )
        conn.execute(
            "INSERT INTO artifact_registry (registry_id, tenant_id, agent_id, store_name, artifact_kind, physical_artifact_id, state) "
            "VALUES ('reg-graph-1', 'tenant-a', 'agent-a', 'GRAPH', 'ASSERTION', 'ast-old', 'ACTIVE')"
        )
        conn.execute(
            "INSERT INTO artifact_sources (source_ownership_id, registry_id, mutation_id, dataset_id, state) "
            "VALUES ('src-graph-1', 'reg-graph-1', 'mut-old', 'ds-old', 'ACTIVE')"
        )
        conn.execute(
            "INSERT INTO artifact_cleanup_outbox (cleanup_id, pipeline_run_id, registry_id, state) "
            "VALUES ('cln-graph-1', 'pipe-old', 'reg-graph-1', 'BLOCKED')"
        )
        conn.execute(
            "INSERT INTO memory_artifacts (artifact_row_id, mutation_id, store_name, artifact_kind, artifact_id, state) "
            "VALUES ('ma-graph-1', 'mut-old', 'GRAPH', 'ASSERTION', 'ast-old', 'ACTIVE')"
        )
        conn.commit()
    finally:
        conn.close()

    # Upgrade to head (f9c0d1e2f3a4)
    command.upgrade(cfg, "head")

    conn = sqlite3.connect(str(db_path))
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM projection_outbox WHERE projection_name = 'GRAPH'"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM projection_attempts WHERE projection_id = 'p-graph'"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM artifact_registry WHERE store_name = 'GRAPH'"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM artifact_sources WHERE registry_id = 'reg-graph-1'"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM artifact_cleanup_outbox WHERE registry_id = 'reg-graph-1'"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM memory_artifacts WHERE store_name = 'GRAPH'"
        ).fetchone()[0] == 0
        assert (
            conn.execute(
                "SELECT state FROM memory_mutations WHERE mutation_id = 'mut-old'"
            ).fetchone()[0]
            == "COMMITTED"
        )
        assert (
            conn.execute(
                "SELECT graph_relative_path FROM projection_generations WHERE generation_id = 'legacy'"
            ).fetchone()[0]
            == "graph_unused"
        )
    finally:
        conn.close()

    engine = AsyncEngine(str(db_path))
    await engine.initialize()
    try:
        vec = SimpleNamespace(
            health_check=AsyncMock(return_value={"status": "healthy"})
        )
        dao = MemoryDAO(sqlite_engine=engine, vector_engine=vec, embedding_enabled=False)
        health = await dao.health_check()
        proj = health["v4_projection"]
        own = health["v4_ownership"]
        assert proj["backlog"] == 0
        assert proj["dead_letter"] == 0
        assert proj["stuck_claims"] == 0
        assert own["cleanup_blocked"] == 0
        assert own["orphan_registry"] == 0
    finally:
        await engine.close()
