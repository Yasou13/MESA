# MESA — KùzuDB Graph Schema Initialization
# Disk-backed replacement for the in-memory NetworkX graph layer.
#
# Design:
#   - Entity node table: UUID-identified vertices with tenant isolation
#     and quarantine flag for self-healing graph operations
#   - Observed rel table: Weighted directed edges with temporal tracking
#     and epistemic uncertainty for hallucination detection
#   - All DDL is idempotent (CREATE ... IF NOT EXISTS)
#   - ALTER TABLE migrations handle column additions on existing DBs
#
# Index Strategy:
#   KùzuDB automatically creates a hash index on every PRIMARY KEY column.
#   Secondary indexes on non-PK columns (e.g. agent_id) are NOT supported
#   by KùzuDB's Cypher DDL — unlike SQL databases, graph engines rely on
#   label scans + property filters.  Zero-Trust tenant isolation is
#   therefore enforced at query time via mandatory WHERE agent_id = $id
#   predicates, mirroring the RLS strategy used in the SQLite layer.
"""
KùzuDB graph schema initialization for MESA.

Provides idempotent schema creation that can be executed on every startup
without migration scaffolding.  The ``initialize_schema`` function opens
a short-lived ``kuzu.Connection``, runs DDL, and closes it immediately
— no long-lived connection is held.

Usage::

    from mesa_storage.kuzu_setup import initialize_schema
    initialize_schema("./storage/kuzu_db")
"""

from __future__ import annotations

import logging
from pathlib import Path

import kuzu

logger = logging.getLogger("MESA_Storage")

# ---------------------------------------------------------------------------
# Cypher DDL — idempotent schema creation
# ---------------------------------------------------------------------------

_CREATE_ENTITY_NODE = (
    "CREATE NODE TABLE IF NOT EXISTS Entity ("
    "id STRING, "
    "name STRING, "
    "agent_id STRING, "
    "is_quarantined BOOLEAN DEFAULT false, "
    "PRIMARY KEY (id)"
    ")"
)

_CREATE_OBSERVED_REL = (
    "CREATE REL TABLE IF NOT EXISTS Observed ("
    "FROM Entity TO Entity, "
    "weight DOUBLE, "
    "updated_at TIMESTAMP, "
    "agent_id STRING, "
    "epistemic_uncertainty FLOAT DEFAULT 0.0"
    ")"
)

# ---------------------------------------------------------------------------
# Schema migrations — ALTER TABLE for existing databases
# ---------------------------------------------------------------------------
# CREATE TABLE IF NOT EXISTS only prevents table re-creation; it does NOT
# add new columns to tables that already exist.  These ALTER statements
# handle column additions on databases created before Phase 4.1.

_MIGRATIONS: list[tuple[str, str]] = [
    # (description, Cypher DDL)
    (
        "Entity.is_quarantined",
        "ALTER TABLE Entity ADD is_quarantined BOOLEAN DEFAULT false",
    ),
    (
        "Observed.epistemic_uncertainty",
        "ALTER TABLE Observed ADD epistemic_uncertainty FLOAT DEFAULT 0.0",
    ),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def initialize_schema_artifact(db_path: str) -> None:
    """Create the current schema in a new, non-live Kùzu artifact.

    This low-level helper deliberately has no migration policy. It is used by
    the offline coordinator to build a staging artifact. Runtime callers must
    use :func:`initialize_schema`, which validates a versioned journal first.

    Opens a temporary ``kuzu.Connection`` to execute DDL, then closes it
    immediately.  This avoids holding a long-lived connection at module
    scope, which would cause OS-level file-lock contention in the
    embedded C++ engine.

    After table creation, runs idempotent ``ALTER TABLE ... ADD`` migrations
    for columns introduced in Phase 4.1 (``is_quarantined``,
    ``epistemic_uncertainty``).  Each migration is wrapped in a
    try/except so that repeated runs on already-migrated databases
    are no-ops.

    Index Strategy
    ~~~~~~~~~~~~~~
    KùzuDB **automatically** indexes primary-key columns (``Entity.id``).
    Secondary indexes on arbitrary properties (e.g. ``agent_id``) are not
    supported by KùzuDB's current Cypher DDL.  Tenant isolation is
    enforced at query time via mandatory ``WHERE agent_id = $id``
    predicates in every Cypher read/write — consistent with the
    Zero-Trust RLS model already used in the SQLite layer.

    Args:
        db_path: Filesystem path to the KùzuDB database directory
                 (e.g. ``"./storage/kuzu_db"``).

    Raises:
        RuntimeError: If KùzuDB fails to execute any DDL statement.
    """
    db = kuzu.Database(db_path)
    conn = kuzu.Connection(db)

    try:
        # 1. Node table
        conn.execute(_CREATE_ENTITY_NODE)
        logger.info("KUZU_SCHEMA | Entity node table ready")

        # 2. Relationship table
        conn.execute(_CREATE_OBSERVED_REL)
        logger.info("KUZU_SCHEMA | Observed rel table ready")

        logger.info(
            "KUZU_SCHEMA | staging artifact initialized — tables=[Entity, Observed] db=%s",
            db_path,
        )
    finally:
        conn.close()
        db.close()


def initialize_schema(db_path: str) -> None:
    """Ensure a runtime graph is journaled at the supported schema version.

    Existing unjournaled artifacts are never altered during startup. Operators
    must run the offline schema migration command, which builds and validates
    a staging artifact before atomic promotion.
    """
    from mesa_storage.kuzu_schema_migration import ensure_schema_ready

    ensure_schema_ready(Path(db_path))


def _apply_migrations(conn: kuzu.Connection) -> None:
    """Run idempotent ALTER TABLE migrations for Phase 4.1+ columns.

    Each migration is individually wrapped so that a failure on one
    (e.g. column already exists) does not block subsequent migrations.
    """
    for description, ddl in _MIGRATIONS:
        try:
            conn.execute(ddl)
            logger.info("KUZU_MIGRATION | applied: %s", description)
        except RuntimeError as exc:
            # KùzuDB raises RuntimeError when a column already exists.
            # This is expected on repeated startups — log and continue.
            if "already exists" in str(exc).lower() or "exist" in str(exc).lower():
                logger.debug(
                    "KUZU_MIGRATION | skipped (already applied): %s",
                    description,
                )
            else:
                logger.warning(
                    "KUZU_MIGRATION | failed: %s — %s",
                    description,
                    exc,
                )
