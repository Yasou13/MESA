# MESA — KùzuDB Graph Schema Initialization
# Disk-backed replacement for the in-memory NetworkX graph layer.
#
# Design:
#   - Entity node table: UUID-identified vertices with tenant isolation
#   - Observed rel table: Weighted directed edges with temporal tracking
#   - All DDL is idempotent (CREATE ... IF NOT EXISTS)
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
    "PRIMARY KEY (id)"
    ")"
)

_CREATE_OBSERVED_REL = (
    "CREATE REL TABLE IF NOT EXISTS Observed ("
    "FROM Entity TO Entity, "
    "weight DOUBLE, "
    "updated_at TIMESTAMP, "
    "agent_id STRING"
    ")"
)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def initialize_schema(db_path: str) -> None:
    """Create the KùzuDB graph schema if it does not already exist.

    Opens a temporary ``kuzu.Connection`` to execute DDL, then closes it
    immediately.  This avoids holding a long-lived connection at module
    scope, which would cause OS-level file-lock contention in the
    embedded C++ engine.

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
            "KUZU_SCHEMA | initialization complete — "
            "tables=[Entity, Observed] db=%s",
            db_path,
        )
    finally:
        conn.close()
        db.close()
