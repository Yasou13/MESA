# MESA v0.3.0 — Phase 3: Graph & FTS5 Schema Initialization
# Creates and manages the `nodes` and `nodes_fts` (FTS5) tables
# for the MESA knowledge graph storage layer.
#
# Design:
#   - `nodes` table: UUID-identified graph vertices with soft-delete support
#   - Edge storage: Migrated to KùzuDB (see kuzu_provider.py)
#   - `nodes_fts`: FTS5 virtual table synchronised to nodes via triggers
#     for zero-VRAM lexical pre-filtering
#
# All DDL is idempotent (CREATE IF NOT EXISTS / CREATE TRIGGER IF NOT EXISTS).
"""
Graph schema and FTS5 virtual table initialization for MESA v0.3.0.

Provides idempotent schema creation that can be executed on every startup
without migration scaffolding.  The FTS5 table is kept in sync with the
``nodes`` table via INSERT/UPDATE/DELETE triggers — no application-level
sync logic required.
"""

from __future__ import annotations

import logging

import aiosqlite

from mesa_storage.sqlite_engine import AsyncEngine

logger = logging.getLogger("MESA_Storage")

# ---------------------------------------------------------------------------
# Migration state and LanceDB WAL
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Schema manager — public API
# ---------------------------------------------------------------------------


async def initialize_schema(engine: AsyncEngine) -> None:
    """Run Alembic migrations programmatically to 'head'.

    Args:
        engine: An initialised AsyncEngine pointing to the target database.
    """
    import asyncio
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    # Resolve alembic.ini relative to this file (mesa_storage package)
    _MODULE_DIR = Path(__file__).parent.resolve()
    # We moved alembic.ini into mesa_storage for PyPI packaging
    alembic_ini_path = _MODULE_DIR / "alembic.ini"
    alembic_cfg = Config(str(alembic_ini_path))
    # Inject the SQLite URL programmatically so we don't rely on the .ini
    db_url = f"sqlite+aiosqlite:///{engine.db_path}"
    alembic_cfg.set_main_option("sqlalchemy.url", db_url)

    loop = asyncio.get_running_loop()
    await asyncio.wait_for(
        loop.run_in_executor(None, command.upgrade, alembic_cfg, "head"),
        timeout=120.0,  # 2min ceiling — prevents indefinite startup hang
    )

    async with engine.connection() as db:
        # B-6 FIX: Recover orphaned jobs
        cursor = await db.execute(
            "UPDATE raw_logs SET status = 'queued' "
            "WHERE status = 'processing' "
            "AND created_at < datetime('now', '-5 minutes')"
        )
        recovered = cursor.rowcount
        await db.commit()

    if recovered:
        logger.warning(
            "SCHEMA_INIT | recovered %d orphaned raw_logs jobs "
            "(processing > 5 min → queued)",
            recovered,
        )

    logger.info(
        "SCHEMA_INIT | Alembic upgrade head completed for db=%s",
        engine.db_path,
    )


async def validate_schema(engine: AsyncEngine) -> dict:
    """Introspect the database and verify all expected objects exist.

    Returns:
        Dict mapping object names to booleans indicating presence.
    """
    expected_tables = {
        "nodes",
        "nodes_fts",
        "routing_telemetry",
        "raw_logs",
        "system_config",
        "lancedb_wal",
    }
    expected_indexes = {
        "idx_nodes_active",
        "idx_nodes_entity_name",
        "idx_nodes_agent",
        "idx_nodes_unconsolidated",
        "idx_nodes_soft_deleted",
        "idx_raw_logs_session",
    }
    expected_triggers = {
        "trg_nodes_fts_insert",
        "trg_nodes_fts_delete",
        "trg_nodes_fts_update",
    }

    result: dict = {"tables": {}, "indexes": {}, "triggers": {}, "valid": True}

    async with engine.connection() as db:
        # Tables
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
        ) as cursor:
            rows = await cursor.fetchall()
            found_tables = {row[0] for row in rows}

        for t in expected_tables:
            present = t in found_tables
            result["tables"][t] = present
            if not present:
                result["valid"] = False

        # Indexes
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ) as cursor:
            rows = await cursor.fetchall()
            found_indexes = {row[0] for row in rows}

        for i in expected_indexes:
            present = i in found_indexes
            result["indexes"][i] = present
            if not present:
                result["valid"] = False

        # Triggers
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'"
        ) as cursor:
            rows = await cursor.fetchall()
            found_triggers = {row[0] for row in rows}

        for tr in expected_triggers:
            present = tr in found_triggers
            result["triggers"][tr] = present
            if not present:
                result["valid"] = False

    return result


# ---------------------------------------------------------------------------
# FTS5 lexical pre-filtering — zero-VRAM search
# ---------------------------------------------------------------------------


async def fts5_search(
    engine: AsyncEngine,
    query: str,
    *,
    agent_id: str,
    limit: int = 20,
) -> list[dict]:
    """Execute an FTS5 MATCH query against entity names and types.

    This provides zero-VRAM lexical pre-filtering before expensive
    vector similarity or graph traversal operations.

    RLS: agent_id is **mandatory** and hardcoded into the JOIN predicate
    to prevent cross-tenant data leakage through full-text search.

    Args:
        query: FTS5 match expression (supports AND, OR, NOT, prefix*).
        agent_id: Mandatory tenant isolation key.
        limit: Maximum results to return.

    Returns:
        List of matching node dicts ranked by FTS5 relevance.
    """
    if not query or not query.strip():
        return []

    sql = (
        "SELECT n.*, rank "
        "FROM nodes_fts "
        "JOIN nodes n ON n.rowid = nodes_fts.rowid "
        "WHERE nodes_fts MATCH ? "
        "AND n.agent_id = ? "
        "AND n.invalid_at IS NULL "
        "AND n.deleted_at IS NULL "
        "ORDER BY rank "
        "LIMIT ?"
    )

    async with engine.connection() as db:
        try:
            async with db.execute(sql, (query, agent_id, limit)) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]
        except aiosqlite.OperationalError as exc:
            # Graceful degradation on malformed FTS5 queries
            logger.warning(
                "FTS5_QUERY_ERROR | agent_id=%s query=%r error=%s — returning empty",
                agent_id,
                query,
                exc,
            )
            return []


async def fts5_rebuild(engine: AsyncEngine) -> None:
    """Rebuild the FTS5 index from the current nodes table content.

    Use after bulk imports or if the index becomes inconsistent.
    """
    async with engine.connection() as db:
        await db.execute("INSERT INTO nodes_fts(nodes_fts) VALUES ('rebuild')")
        await db.commit()

    logger.info("FTS5_REBUILD | index rebuilt from nodes table")
