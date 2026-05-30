# MESA v0.3.0 — Phase 3: Graph & FTS5 Schema Initialization
# Creates and manages the `nodes`, `edges`, and `nodes_fts` (FTS5) tables
# for the MESA knowledge graph storage layer.
#
# Design:
#   - `nodes` table: UUID-identified graph vertices with soft-delete support
#   - `edges` table: Weighted directed edges with FK integrity to nodes
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
from datetime import datetime, timezone

import aiosqlite

from mesa_storage.sqlite_engine import AsyncEngine

logger = logging.getLogger("MESA_Storage")

# ---------------------------------------------------------------------------
# DDL statements — idempotent schema creation
# ---------------------------------------------------------------------------

_CREATE_NODES_TABLE = """\
CREATE TABLE IF NOT EXISTS nodes (
    id               TEXT    PRIMARY KEY,
    entity_name      TEXT    NOT NULL,
    type             TEXT    NOT NULL DEFAULT 'ENTITY',
    is_consolidated  INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT    NOT NULL,
    invalid_at       TEXT    DEFAULT NULL,
    deleted_at       TEXT    DEFAULT NULL,
    agent_id         TEXT    NOT NULL DEFAULT '__unset__',
    session_id       TEXT    NOT NULL DEFAULT '__unset__'
);
"""

_CREATE_NODES_INDEXES = [
    """\
    CREATE INDEX IF NOT EXISTS idx_nodes_active
    ON nodes(invalid_at) WHERE invalid_at IS NULL;
    """,
    """\
    CREATE INDEX IF NOT EXISTS idx_nodes_entity_name
    ON nodes(entity_name COLLATE NOCASE) WHERE invalid_at IS NULL;
    """,
    """\
    CREATE INDEX IF NOT EXISTS idx_nodes_agent
    ON nodes(agent_id, session_id) WHERE invalid_at IS NULL;
    """,
    """\
    CREATE INDEX IF NOT EXISTS idx_nodes_unconsolidated
    ON nodes(is_consolidated) WHERE is_consolidated = 0 AND invalid_at IS NULL;
    """,
    """\
    CREATE INDEX IF NOT EXISTS idx_nodes_soft_deleted
    ON nodes(deleted_at) WHERE deleted_at IS NOT NULL;
    """,
]

_CREATE_EDGES_TABLE = """\
CREATE TABLE IF NOT EXISTS edges (
    id            TEXT    PRIMARY KEY,
    source_id     TEXT    NOT NULL,
    target_id     TEXT    NOT NULL,
    relation_type TEXT    NOT NULL,
    weight        REAL    NOT NULL DEFAULT 1.0,
    created_at    TEXT    NOT NULL,
    invalid_at    TEXT    DEFAULT NULL,
    agent_id      TEXT    NOT NULL DEFAULT '__unset__',
    FOREIGN KEY (source_id) REFERENCES nodes(id),
    FOREIGN KEY (target_id) REFERENCES nodes(id)
);
"""

_CREATE_EDGES_INDEXES = [
    """\
    CREATE INDEX IF NOT EXISTS idx_edges_active
    ON edges(invalid_at) WHERE invalid_at IS NULL;
    """,
    """\
    CREATE INDEX IF NOT EXISTS idx_edges_source
    ON edges(source_id) WHERE invalid_at IS NULL;
    """,
    """\
    CREATE INDEX IF NOT EXISTS idx_edges_target
    ON edges(target_id) WHERE invalid_at IS NULL;
    """,
    """\
    CREATE INDEX IF NOT EXISTS idx_edges_relation
    ON edges(relation_type) WHERE invalid_at IS NULL;
    """,
    """\
    CREATE UNIQUE INDEX IF NOT EXISTS idx_edges_unique_active
    ON edges(source_id, target_id, relation_type)
    WHERE invalid_at IS NULL;
    """,
]

# ---------------------------------------------------------------------------
# FTS5 virtual table — zero-VRAM lexical pre-filtering
# ---------------------------------------------------------------------------

_CREATE_FTS5_TABLE = """\
CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts
USING fts5(
    entity_name,
    type,
    content='nodes',
    content_rowid='rowid'
);
"""

_FTS5_TRIGGER_INSERT = """\
CREATE TRIGGER IF NOT EXISTS trg_nodes_fts_insert
AFTER INSERT ON nodes
BEGIN
    INSERT INTO nodes_fts(rowid, entity_name, type)
    VALUES (NEW.rowid, NEW.entity_name, NEW.type);
END;
"""

_FTS5_TRIGGER_DELETE = """\
CREATE TRIGGER IF NOT EXISTS trg_nodes_fts_delete
AFTER DELETE ON nodes
BEGIN
    INSERT INTO nodes_fts(nodes_fts, rowid, entity_name, type)
    VALUES ('delete', OLD.rowid, OLD.entity_name, OLD.type);
END;
"""

_FTS5_TRIGGER_UPDATE = """\
CREATE TRIGGER IF NOT EXISTS trg_nodes_fts_update
AFTER UPDATE ON nodes
BEGIN
    INSERT INTO nodes_fts(nodes_fts, rowid, entity_name, type)
    VALUES ('delete', OLD.rowid, OLD.entity_name, OLD.type);
    INSERT INTO nodes_fts(rowid, entity_name, type)
    VALUES (NEW.rowid, NEW.entity_name, NEW.type);
END;
"""

_CREATE_ROUTING_TELEMETRY_TABLE = """\
CREATE TABLE IF NOT EXISTS routing_telemetry (
    id                     TEXT    PRIMARY KEY,
    agent_id               TEXT    NOT NULL,
    record_id              TEXT    NOT NULL,
    small_model_decision   INTEGER NOT NULL,
    small_model_confidence REAL    NOT NULL,
    dual_llm_decision      INTEGER NOT NULL,
    is_hallucination       INTEGER NOT NULL,
    created_at             TEXT    NOT NULL
);
"""

_CREATE_RAW_LOGS_TABLE = """\
CREATE TABLE IF NOT EXISTS raw_logs (
    id         INTEGER PRIMARY KEY,
    payload    JSON    NOT NULL,
    status     TEXT    NOT NULL DEFAULT 'queued',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

_CREATE_RAW_LOGS_INDEXES = [
    """\
    CREATE INDEX IF NOT EXISTS idx_raw_logs_session
    ON raw_logs(json_extract(payload, '$.agent_id'), json_extract(payload, '$.session_id'));
    """,
]


# ---------------------------------------------------------------------------
# Schema manager — public API
# ---------------------------------------------------------------------------


async def initialize_schema(engine: AsyncEngine) -> None:
    """Create all graph tables, indexes, FTS5 virtual table, and triggers.

    Idempotent — safe to call on every application startup.  Executes all
    DDL within a single connection to minimise WAL checkpoint overhead.

    Args:
        engine: An initialised AsyncEngine pointing to the target database.
    """
    async with engine.connection() as db:
        # 1. Core tables
        await db.execute(_CREATE_NODES_TABLE)
        await db.execute(_CREATE_EDGES_TABLE)
        await db.execute(_CREATE_ROUTING_TELEMETRY_TABLE)
        await db.execute(_CREATE_RAW_LOGS_TABLE)

        # 2. Indexes
        for idx_sql in _CREATE_NODES_INDEXES:
            await db.execute(idx_sql)
        for idx_sql in _CREATE_EDGES_INDEXES:
            await db.execute(idx_sql)
        for idx_sql in _CREATE_RAW_LOGS_INDEXES:
            await db.execute(idx_sql)

        # 3. FTS5 virtual table
        await db.execute(_CREATE_FTS5_TABLE)

        # 4. FTS5 sync triggers (idempotent via IF NOT EXISTS)
        await db.execute(_FTS5_TRIGGER_INSERT)
        await db.execute(_FTS5_TRIGGER_DELETE)
        await db.execute(_FTS5_TRIGGER_UPDATE)

        # 5. B-6 FIX: Recover orphaned jobs — any raw_logs entries stuck
        #    in 'processing' for >5 minutes are reset to 'queued'.
        #    This handles unclean shutdowns where workers died mid-flight.
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
        "SCHEMA_INIT | tables=[nodes, edges, nodes_fts, routing_telemetry, raw_logs] "
        "indexes=10 triggers=3 db=%s",
        engine.db_path,
    )


async def validate_schema(engine: AsyncEngine) -> dict:
    """Introspect the database and verify all expected objects exist.

    Returns:
        Dict mapping object names to booleans indicating presence.
    """
    expected_tables = {"nodes", "edges", "nodes_fts", "routing_telemetry", "raw_logs"}
    expected_indexes = {
        "idx_nodes_active",
        "idx_nodes_entity_name",
        "idx_nodes_agent",
        "idx_nodes_unconsolidated",
        "idx_nodes_soft_deleted",
        "idx_edges_active",
        "idx_edges_source",
        "idx_edges_target",
        "idx_edges_relation",
        "idx_edges_unique_active",
    }
    expected_triggers = {
        "trg_nodes_fts_insert",
        "trg_nodes_fts_delete",
        "trg_nodes_fts_update",
    }

    expected_indexes.add("idx_raw_logs_session")

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
# Node operations
# ---------------------------------------------------------------------------


async def insert_node(
    engine: AsyncEngine,
    node_id: str,
    entity_name: str,
    node_type: str = "ENTITY",
    agent_id: str = "__unset__",
    session_id: str = "__unset__",
    is_consolidated: bool = False,
) -> str:
    """Insert a new node into the graph.

    Returns:
        The node_id of the inserted node.
    """
    now = datetime.now(timezone.utc).isoformat()

    async with engine.transaction() as db:
        await db.execute(
            "INSERT INTO nodes (id, entity_name, type, is_consolidated, "
            "created_at, agent_id, session_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                node_id,
                entity_name,
                node_type,
                int(is_consolidated),
                now,
                agent_id,
                session_id,
            ),
        )
        await db.commit()

    return node_id


async def bulk_insert_nodes(
    engine: AsyncEngine,
    nodes: list[dict],
) -> int:
    """Insert multiple nodes in a single transaction.

    Each dict must contain: id, entity_name.
    Optional keys: type, agent_id, session_id, is_consolidated.

    Returns:
        Number of nodes inserted.
    """
    if not nodes:
        return 0

    now = datetime.now(timezone.utc).isoformat()

    async with engine.transaction() as db:
        await db.executemany(
            "INSERT INTO nodes (id, entity_name, type, is_consolidated, "
            "created_at, agent_id, session_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    n["id"],
                    n["entity_name"],
                    n.get("type", "ENTITY"),
                    int(n.get("is_consolidated", False)),
                    now,
                    n.get("agent_id", "__unset__"),
                    n.get("session_id", "__unset__"),
                )
                for n in nodes
            ],
        )
        await db.commit()

    return len(nodes)


async def soft_delete_node(engine: AsyncEngine, node_id: str, *, agent_id: str) -> None:
    """Soft-delete a node by setting its invalid_at timestamp.

    Also soft-deletes all edges connected to this node (source or target).
    RLS: agent_id is mandatory and hardcoded into every WHERE clause.
    """
    now = datetime.now(timezone.utc).isoformat()

    async with engine.transaction() as db:
        await db.execute(
            "UPDATE nodes SET invalid_at = ? "
            "WHERE id = ? AND agent_id = ? AND invalid_at IS NULL",
            (now, node_id, agent_id),
        )
        # Cascade soft-delete to connected edges
        await db.execute(
            "UPDATE edges SET invalid_at = ? "
            "WHERE agent_id = ? "
            "AND (source_id = ? OR target_id = ?) AND invalid_at IS NULL",
            (now, agent_id, node_id, node_id),
        )
        await db.commit()


async def mark_consolidated(
    engine: AsyncEngine, node_id: str, *, agent_id: str
) -> None:
    """Mark a node as consolidated (processed by batch orchestrator).

    RLS: agent_id is mandatory and hardcoded into the WHERE clause.
    """
    async with engine.connection() as db:
        await db.execute(
            "UPDATE nodes SET is_consolidated = 1 "
            "WHERE id = ? AND agent_id = ? AND invalid_at IS NULL",
            (node_id, agent_id),
        )
        await db.commit()


async def get_active_nodes(
    engine: AsyncEngine,
    agent_id: str,
    limit: int | None = None,
) -> list[dict]:
    """Return all active (non-invalidated) nodes.

    RLS: agent_id is **mandatory** — no unscoped global reads allowed.

    Args:
        agent_id: Mandatory tenant isolation key.
        limit: Optional maximum number of rows.

    Returns:
        List of node dicts with keys: id, entity_name, type,
        is_consolidated, created_at, agent_id, session_id.
    """
    query = "SELECT * FROM nodes WHERE agent_id = ? AND invalid_at IS NULL"
    params: list = [agent_id]

    query += " ORDER BY created_at ASC"

    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)

    async with engine.connection() as db:
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def find_nodes_by_name(
    engine: AsyncEngine,
    names: list[str],
    *,
    agent_id: str,
    case_insensitive: bool = True,
) -> list[dict]:
    """Find active nodes whose entity_name matches any in names.

    RLS: agent_id is mandatory and hardcoded into the WHERE clause.
    """
    if not names:
        return []

    if case_insensitive:
        conditions = " OR ".join("LOWER(entity_name) = ?" for _ in names)
        params: list = [agent_id] + [n.lower() for n in names]
    else:
        conditions = " OR ".join("entity_name = ?" for _ in names)
        params = [agent_id] + list(names)

    query = (
        f"SELECT * FROM nodes WHERE agent_id = ? "
        f"AND invalid_at IS NULL AND ({conditions})"
    )

    async with engine.connection() as db:
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Edge operations
# ---------------------------------------------------------------------------


async def insert_edge(
    engine: AsyncEngine,
    edge_id: str,
    source_id: str,
    target_id: str,
    relation_type: str,
    weight: float = 1.0,
    agent_id: str = "__unset__",
) -> str:
    """Insert a directed edge between two nodes.

    Returns:
        The edge_id of the inserted edge.
    """
    now = datetime.now(timezone.utc).isoformat()

    async with engine.transaction() as db:
        await db.execute(
            "INSERT INTO edges (id, source_id, target_id, relation_type, "
            "weight, created_at, agent_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (edge_id, source_id, target_id, relation_type, weight, now, agent_id),
        )
        await db.commit()

    return edge_id


async def upsert_edge(
    engine: AsyncEngine,
    edge_id: str,
    source_id: str,
    target_id: str,
    relation_type: str,
    weight: float = 1.0,
    agent_id: str = "__unset__",
) -> str:
    """Insert or update an edge's weight if the active triple already exists.

    Uses the unique index on (source_id, target_id, relation_type) WHERE
    invalid_at IS NULL to detect duplicates.  On conflict, the weight is
    updated additively.

    Returns:
        The edge_id used.
    """
    now = datetime.now(timezone.utc).isoformat()

    async with engine.transaction() as db:
        # Check for existing active edge with same triple
        # RLS: agent_id hardcoded into SELECT to prevent cross-tenant reads
        async with db.execute(
            "SELECT id, weight FROM edges "
            "WHERE source_id = ? AND target_id = ? AND relation_type = ? "
            "AND agent_id = ? AND invalid_at IS NULL",
            (source_id, target_id, relation_type, agent_id),
        ) as cursor:
            existing = await cursor.fetchone()

        if existing:
            # Merge: update weight additively
            # RLS: agent_id hardcoded into UPDATE
            await db.execute(
                "UPDATE edges SET weight = weight + ? " "WHERE id = ? AND agent_id = ?",
                (weight, existing[0], agent_id),
            )
            await db.commit()
            return str(existing[0])
        else:
            await db.execute(
                "INSERT INTO edges (id, source_id, target_id, relation_type, "
                "weight, created_at, agent_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (edge_id, source_id, target_id, relation_type, weight, now, agent_id),
            )
            await db.commit()
            return edge_id


async def soft_delete_edge(engine: AsyncEngine, edge_id: str, *, agent_id: str) -> None:
    """Soft-delete a single edge by its ID.

    RLS: agent_id is mandatory and hardcoded into the WHERE clause.
    """
    now = datetime.now(timezone.utc).isoformat()

    async with engine.connection() as db:
        await db.execute(
            "UPDATE edges SET invalid_at = ? "
            "WHERE id = ? AND agent_id = ? AND invalid_at IS NULL",
            (now, edge_id, agent_id),
        )
        await db.commit()


async def get_neighbors(
    engine: AsyncEngine,
    node_id: str,
    *,
    agent_id: str,
    direction: str = "both",
) -> list[dict]:
    """Return edges connected to a node.

    RLS: agent_id is mandatory and hardcoded into every WHERE clause.

    Args:
        agent_id: Mandatory tenant isolation key.
        direction: "out" (outgoing), "in" (incoming), or "both".

    Returns:
        List of edge dicts with target/source node info.
    """
    results: list[dict] = []

    async with engine.connection() as db:
        if direction in ("out", "both"):
            async with db.execute(
                "SELECT e.*, n.entity_name AS target_name, n.type AS target_type "
                "FROM edges e "
                "JOIN nodes n ON n.id = e.target_id AND n.invalid_at IS NULL "
                "WHERE e.source_id = ? AND e.agent_id = ? AND e.invalid_at IS NULL",
                (node_id, agent_id),
            ) as cursor:
                rows = await cursor.fetchall()
                results.extend(dict(row) for row in rows)

        if direction in ("in", "both"):
            async with db.execute(
                "SELECT e.*, n.entity_name AS source_name, n.type AS source_type "
                "FROM edges e "
                "JOIN nodes n ON n.id = e.source_id AND n.invalid_at IS NULL "
                "WHERE e.target_id = ? AND e.agent_id = ? AND e.invalid_at IS NULL",
                (node_id, agent_id),
            ) as cursor:
                rows = await cursor.fetchall()
                results.extend(dict(row) for row in rows)

    return results


async def get_active_edges(engine: AsyncEngine, *, agent_id: str) -> list[dict]:
    """Return all active (non-invalidated) edges.

    RLS: agent_id is mandatory and hardcoded into the WHERE clause.
    """
    async with engine.connection() as db:
        async with db.execute(
            "SELECT * FROM edges WHERE agent_id = ? "
            "AND invalid_at IS NULL ORDER BY created_at ASC",
            (agent_id,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def k_hop_neighbors(
    engine: AsyncEngine,
    node_id: str,
    *,
    agent_id: str,
    k: int = 2,
    direction: str = "both",
) -> list[dict]:
    """Return all nodes reachable within k hops via BFS traversal.

    RLS: agent_id is mandatory and hardcoded into every query.

    Args:
        node_id: Starting node UUID.
        agent_id: Mandatory tenant isolation key.
        k: Maximum hop depth (default 2).
        direction: "out", "in", or "both".

    Returns:
        List of node dicts with an added 'depth' key.
    """
    visited: set[str] = {node_id}
    frontier: set[str] = {node_id}
    results: list[dict] = []

    for depth in range(1, k + 1):
        next_frontier: set[str] = set()

        for fid in frontier:
            neighbors = await get_neighbors(
                engine, fid, agent_id=agent_id, direction=direction
            )
            for edge in neighbors:
                # Determine the neighbor ID based on direction
                if direction == "out":
                    neighbor_id = edge["target_id"]
                elif direction == "in":
                    neighbor_id = edge["source_id"]
                else:
                    neighbor_id = (
                        edge["target_id"]
                        if edge["source_id"] == fid
                        else edge["source_id"]
                    )

                if neighbor_id not in visited:
                    visited.add(neighbor_id)
                    next_frontier.add(neighbor_id)

        if not next_frontier:
            break

        # Fetch node details for this depth layer
        # RLS: agent_id hardcoded into WHERE clause
        async with engine.connection() as db:
            placeholders = ",".join("?" for _ in next_frontier)
            async with db.execute(
                f"SELECT * FROM nodes WHERE id IN ({placeholders}) "
                "AND agent_id = ? AND invalid_at IS NULL",
                list(next_frontier) + [agent_id],
            ) as cursor:
                rows = await cursor.fetchall()
                for row in rows:
                    node_dict = dict(row)
                    node_dict["depth"] = depth
                    results.append(node_dict)

        frontier = next_frontier

    return results


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
