"""SQLite schema initialization and introspection for MESA V4 storage.

All DDL is managed by Alembic migrations under ``mesa_storage/alembic/``.
"""

from __future__ import annotations

import logging

from mesa_storage.sqlite_engine import AsyncEngine

logger = logging.getLogger("MESA_Storage")


async def initialize_schema(engine: AsyncEngine) -> None:
    """Run Alembic migrations programmatically to 'head'.

    Args:
        engine: An initialised AsyncEngine pointing to the target database.
    """
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    _MODULE_DIR = Path(__file__).parent.resolve()
    alembic_ini_path = _MODULE_DIR / "alembic.ini"
    alembic_cfg = Config(str(alembic_ini_path))
    db_url = f"sqlite+aiosqlite:///{engine.db_path}"
    alembic_cfg.set_main_option("sqlalchemy.url", db_url)
    alembic_cfg.attributes["mesa_require_head_postflight"] = True

    # Alembic DDL is synchronous.  It runs before the service accepts traffic,
    # and keeping it in this startup call avoids the thread/executor deadlock
    # that can occur when an async event loop launches the migration command.
    command.upgrade(alembic_cfg, "head")

    async with engine.connection() as db:
        # B-6 FIX: Recover orphaned jobs
        cursor = await db.execute(
            "UPDATE raw_logs SET status = 'DEFERRED' "
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
    """Introspect the database and verify all expected V4 objects exist.

    Returns:
        Dict mapping object names to booleans indicating presence.
    """
    expected_tables = {
        "raw_logs",
        "memory_mutations",
        "memory_artifacts",
        "projection_outbox",
        "projection_attempts",
        "tenants",
        "workspaces",
        "datasets",
        "documents",
        "document_revisions",
        "source_chunks",
        "v4_sessions",
        "v4_session_datasets",
        "v4_entities",
        "v4_entities_fts",
        "entity_aliases",
        "entity_external_ids",
        "v4_assertions",
        "v4_assertions_fts",
        "v4_assertion_links",
        "pipeline_runs",
        "pipeline_run_events",
        "artifact_registry",
        "artifact_sources",
        "artifact_cleanup_outbox",
        "system_operations",
        "system_operation_events",
        "projection_generations",
        "projection_runtime",
    }
    expected_indexes = {
        "idx_raw_logs_session",
        "idx_system_operations_claim",
        "uq_system_operations_active_rebuild",
        "idx_system_operation_events_sequence",
        "idx_projection_generations_lifecycle",
        "uq_active_document_revision",
        "idx_v4_assertions_legal_identity_scope",
    }
    expected_triggers = {
        "trg_v4_assertions_fts_insert",
        "trg_v4_assertions_fts_delete",
        "trg_v4_assertions_fts_update",
        "trg_system_operation_events_no_update",
        "trg_system_operation_events_no_delete",
    }

    result: dict = {"tables": {}, "indexes": {}, "triggers": {}, "valid": True}

    async with engine.connection() as db:
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
