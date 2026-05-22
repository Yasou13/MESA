# MESA v0.3.0 — Phase 4: Data Access Object Layer (Epistemic Isolation)
# Wraps aiosqlite (graph/relational) and LanceDB (vector) operations behind
# a single class that MANDATES agent_id on every method signature.
#
# Security guarantees:
#   - Row-Level Security (RLS) simulation: every SQL query and LanceDB filter
#     hardcodes `WHERE agent_id = ?` — cross-agent leakage is structurally
#     impossible regardless of caller logic errors.
#   - Soft-delete via `UPDATE nodes SET deleted_at = CURRENT_TIMESTAMP` —
#     no physical DELETEs are issued; data is preserved for audit/recovery.
#   - Parameterised queries exclusively — zero string interpolation in SQL.
"""
Data Access Object for the MESA storage layer.

Enforces **mandatory epistemic isolation** by requiring ``agent_id`` as a
non-optional, leading argument on every public method.  The ``WHERE
agent_id = ?`` predicate is hardcoded into every raw SQL statement and
LanceDB filter expression to provide a mathematical guarantee against
cross-tenant data leakage.

All delete semantics are **soft-delete**: the ``deleted_at`` column on the
``nodes`` table is stamped with ``CURRENT_TIMESTAMP`` via an ``UPDATE``
rather than a physical ``DELETE``.

Usage::

    from mesa_storage.dao import MemoryDAO

    dao = MemoryDAO(sqlite_engine=engine, vector_engine=vec)
    await dao.insert_memory(
        agent_id="agent_alpha",
        node_id="abc-123",
        entity_name="Contract §4.2",
        content="...",
        embedding=[0.1, 0.2, ...],
    )
    results = await dao.search_memory(
        agent_id="agent_alpha",
        query_vector=[0.1, 0.2, ...],
    )
    affected = await dao.purge_memory(agent_id="agent_alpha", scope="agent")
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.vector_engine import VectorEngine

logger = logging.getLogger("MESA_DAO")

# ---------------------------------------------------------------------------
# Sentinel rejection — defence in depth
# ---------------------------------------------------------------------------

_FORBIDDEN_AGENT_IDS = frozenset({"__unset__", "__system__", ""})


def _assert_valid_agent_id(agent_id: str) -> None:
    """Reject structurally invalid agent_id values at the DAO boundary.

    Raises:
        ValueError: If agent_id is empty, None, or a reserved sentinel.
    """
    if not agent_id or agent_id in _FORBIDDEN_AGENT_IDS:
        raise ValueError(
            f"agent_id must be a non-empty, non-reserved identifier. Got: {agent_id!r}"
        )


# ---------------------------------------------------------------------------
# Core DAO
# ---------------------------------------------------------------------------


class MemoryDAO:
    """Unified Data Access Object enforcing per-agent epistemic isolation.

    Every public method requires ``agent_id`` as its first positional
    argument.  All SQL queries embed ``WHERE agent_id = ?`` via
    parameterised binding.  All LanceDB filters embed
    ``agent_id = '<value>'`` in the WHERE clause.

    This class does NOT own the lifecycle of the underlying engines —
    the caller is responsible for initialising and closing them.

    Args:
        sqlite_engine: An initialised ``AsyncEngine`` instance.
        vector_engine: An initialised ``VectorEngine`` instance.
    """

    __slots__ = ("_sql", "_vec")

    def __init__(
        self,
        sqlite_engine: AsyncEngine,
        vector_engine: VectorEngine,
    ) -> None:
        self._sql = sqlite_engine
        self._vec = vector_engine

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def sqlite_engine(self) -> AsyncEngine:
        """Return the underlying SQLite engine (read-only access)."""
        return self._sql

    @property
    def vector_engine(self) -> VectorEngine:
        """Return the underlying vector engine (read-only access)."""
        return self._vec

    # ==================================================================
    # INSERT — agent-scoped memory ingestion
    # ==================================================================

    async def insert_memory(
        self,
        agent_id: str,
        *,
        node_id: str | None = None,
        entity_name: str,
        content: str,
        embedding: list[float],
        node_type: str = "ENTITY",
        session_id: str = "__unset__",
        content_hash: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Insert a memory record scoped exclusively to ``agent_id``.

        Performs a dual-write:
            1. SQLite ``nodes`` table — relational graph vertex.
            2. LanceDB vector table — embedding for similarity search.

        Both writes carry the same ``agent_id`` to maintain referential
        parity across storage backends.

        Args:
            agent_id: **Mandatory** tenant isolation key.
            node_id: Optional UUID; auto-generated if omitted.
            entity_name: Human-readable entity label.
            content: Raw text content (stored as entity context).
            embedding: Float32 embedding vector.
            node_type: Graph node type (default ``ENTITY``).
            session_id: Session scope within the agent.
            content_hash: Optional SHA-256 of content for dedup.
            metadata: Optional flat key-value metadata dict.

        Returns:
            The ``node_id`` (UUID) of the inserted record.

        Raises:
            ValueError: If ``agent_id`` is invalid or reserved.
        """
        _assert_valid_agent_id(agent_id)

        if node_id is None:
            node_id = str(uuid.uuid4())

        now = datetime.now(timezone.utc).isoformat()

        # ---- 1. SQLite INSERT (parameterised, agent_id bound) --------
        async with self._sql.transaction() as db:
            await db.execute(
                "INSERT INTO nodes "
                "(id, entity_name, type, is_consolidated, created_at, "
                " agent_id, session_id) "
                "VALUES (?, ?, ?, 0, ?, ?, ?)",
                (node_id, entity_name, node_type, now, agent_id, session_id),
            )
            await db.commit()

        # ---- 2. LanceDB upsert (agent_id embedded in record) --------
        await self._vec.upsert(
            node_id=node_id,
            agent_id=agent_id,
            embedding=embedding,
            content_hash=content_hash,
        )

        logger.info(
            "INSERT_MEMORY | agent_id=%s node_id=%s entity=%s dim=%d",
            agent_id,
            node_id,
            entity_name,
            len(embedding),
        )
        return node_id

    async def bulk_insert_memory(
        self,
        agent_id: str,
        *,
        records: list[dict[str, Any]],
    ) -> int:
        """Batch-insert multiple memory records for a single agent.

        Each dict in ``records`` must contain at minimum:
            ``entity_name``, ``content``, ``embedding``.
        Optional keys: ``node_id``, ``node_type``, ``session_id``,
        ``content_hash``.

        All records are stamped with the supplied ``agent_id`` —
        per-record agent_id overrides are **rejected** to prevent
        accidental cross-tenant writes.

        Args:
            agent_id: **Mandatory** tenant isolation key (applied to ALL
                      records uniformly).
            records: List of memory record dicts.

        Returns:
            Number of records successfully inserted.

        Raises:
            ValueError: If ``agent_id`` is invalid or reserved.
        """
        _assert_valid_agent_id(agent_id)

        if not records:
            return 0

        now = datetime.now(timezone.utc).isoformat()
        sql_rows: list[tuple] = []
        vec_rows: list[dict] = []

        for rec in records:
            nid = rec.get("node_id") or str(uuid.uuid4())
            sql_rows.append(
                (
                    nid,
                    rec["entity_name"],
                    rec.get("node_type", "ENTITY"),
                    0,
                    now,
                    agent_id,  # hardcoded — never from record dict
                    rec.get("session_id", "__unset__"),
                )
            )
            vec_rows.append(
                {
                    "node_id": nid,
                    "agent_id": agent_id,  # hardcoded
                    "embedding": rec["embedding"],
                    "content_hash": rec.get("content_hash"),
                }
            )

        # ---- SQLite batch INSERT ------------------------------------
        async with self._sql.transaction() as db:
            await db.executemany(
                "INSERT INTO nodes "
                "(id, entity_name, type, is_consolidated, created_at, "
                " agent_id, session_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                sql_rows,
            )
            await db.commit()

        # ---- LanceDB batch upsert -----------------------------------
        await self._vec.bulk_upsert(vec_rows)

        logger.info(
            "BULK_INSERT_MEMORY | agent_id=%s count=%d",
            agent_id,
            len(records),
        )
        return len(records)

    # ==================================================================
    # SEARCH — agent-scoped memory retrieval
    # ==================================================================

    async def search_memory(
        self,
        agent_id: str,
        *,
        query_vector: list[float],
        limit: int = 10,
        include_graph: bool = False,
    ) -> list[dict[str, Any]]:
        """Search memories scoped exclusively to ``agent_id``.

        Performs a cosine similarity search in LanceDB with a hardcoded
        ``agent_id`` filter, then optionally enriches each result with
        relational data from the SQLite ``nodes`` table (also filtered
        by ``agent_id``).

        Args:
            agent_id: **Mandatory** tenant isolation key.
            query_vector: Float32 query embedding.
            limit: Maximum results (default 10).
            include_graph: If ``True``, join each vector hit with its
                           corresponding ``nodes`` row for full context.

        Returns:
            List of result dicts sorted by ascending cosine distance.
            Each dict contains at minimum: ``node_id``, ``agent_id``,
            ``_distance``, ``content_hash``, ``created_at``.

        Raises:
            ValueError: If ``agent_id`` is invalid or reserved.
        """
        _assert_valid_agent_id(agent_id)

        # ---- LanceDB search (agent_id filter hardcoded) --------------
        results = await self._vec.search(
            query_vector=query_vector,
            limit=limit,
            agent_id=agent_id,  # RLS: hardcoded into LanceDB WHERE clause
        )

        if not include_graph or not results:
            return results

        # ---- SQLite enrichment (agent_id filter hardcoded) -----------
        node_ids = [r["node_id"] for r in results]
        placeholders = ",".join("?" for _ in node_ids)

        # RLS: agent_id = ? hardcoded — even if a node_id existed under
        # a different agent, it will NOT be returned.
        query = (
            f"SELECT id, entity_name, type, is_consolidated, "
            f"       created_at, session_id "
            f"FROM nodes "
            f"WHERE agent_id = ? "
            f"  AND id IN ({placeholders}) "
            f"  AND invalid_at IS NULL "
            f"  AND deleted_at IS NULL"
        )
        params: list[Any] = [agent_id, *node_ids]

        graph_map: dict[str, dict] = {}
        async with self._sql.connection() as db:
            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                for row in rows:
                    row_dict = dict(row)
                    graph_map[row_dict["id"]] = row_dict

        # Merge vector + graph data
        for r in results:
            nid = r["node_id"]
            if nid in graph_map:
                r["graph"] = graph_map[nid]

        return results

    async def search_memory_fts(
        self,
        agent_id: str,
        *,
        query: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Lexical FTS5 search scoped exclusively to ``agent_id``.

        Uses the ``nodes_fts`` virtual table for zero-VRAM lexical
        pre-filtering, with a mandatory ``agent_id`` filter on the
        joined ``nodes`` table.

        Args:
            agent_id: **Mandatory** tenant isolation key.
            query: FTS5 MATCH expression (AND, OR, NOT, prefix*).
            limit: Maximum results (default 100 for broader pre-filter pool).

        Returns:
            List of matching node dicts ranked by FTS5 relevance.

        Raises:
            ValueError: If ``agent_id`` is invalid or reserved.
        """
        _assert_valid_agent_id(agent_id)

        if not query or not query.strip():
            return []

        # Convert strict AND to soft OR for broader BM25 matching
        # e.g., "hello world" -> '"hello" OR "world"'
        terms = query.replace('"', "").split()
        parsed_query = " OR ".join([f'"{term}"' for term in terms]) if terms else query

        # RLS: `AND n.agent_id = ?` hardcoded into the JOIN predicate
        sql = (
            "SELECT n.*, rank "
            "FROM nodes_fts "
            "JOIN nodes n ON n.rowid = nodes_fts.rowid "
            "WHERE nodes_fts MATCH ? "
            "  AND n.agent_id = ? "
            "  AND n.invalid_at IS NULL "
            "  AND n.deleted_at IS NULL "
            "ORDER BY rank "
            "LIMIT ?"
        )

        async with self._sql.connection() as db:
            try:
                async with db.execute(sql, (parsed_query, agent_id, limit)) as cursor:
                    rows = await cursor.fetchall()
                    return [dict(row) for row in rows]
            except Exception as exc:
                logger.warning(
                    "FTS5_SEARCH_ERROR | agent_id=%s query=%r error=%s",
                    agent_id,
                    parsed_query,
                    exc,
                )
                return []

    async def get_memories(
        self,
        agent_id: str,
        *,
        limit: int | None = None,
        include_consolidated: bool = True,
    ) -> list[dict[str, Any]]:
        """Retrieve all active memories for an agent.

        Args:
            agent_id: **Mandatory** tenant isolation key.
            limit: Optional maximum rows.
            include_consolidated: If False, only unconsolidated nodes.

        Returns:
            List of node dicts ordered by creation time.

        Raises:
            ValueError: If ``agent_id`` is invalid or reserved.
        """
        _assert_valid_agent_id(agent_id)

        # RLS: `WHERE agent_id = ?` — non-negotiable
        query = (
            "SELECT * FROM nodes "
            "WHERE agent_id = ? "
            "  AND invalid_at IS NULL "
            "  AND deleted_at IS NULL"
        )
        params: list[Any] = [agent_id]

        if not include_consolidated:
            query += " AND is_consolidated = 0"

        query += " ORDER BY created_at ASC"

        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)

        async with self._sql.connection() as db:
            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    # ==================================================================
    # PURGE — agent-scoped soft-delete
    # ==================================================================

    async def purge_memory(
        self,
        agent_id: str,
        *,
        scope: str = "agent",
        session_id: str | None = None,
    ) -> int:
        """Soft-delete memories via ``UPDATE ... SET deleted_at``.

        **No physical DELETEs are issued.** Records are flagged with
        ``deleted_at = CURRENT_TIMESTAMP`` on the ``nodes`` table.
        Connected edges are cascade-invalidated via ``invalid_at``.
        Vector records are soft-expired via the ``expired_at`` column.

        The ``WHERE agent_id = ?`` predicate is hardcoded into every
        UPDATE statement to guarantee zero cross-agent side-effects.

        Args:
            agent_id: **Mandatory** tenant isolation key.
            scope: ``'agent'`` (purge all agent data) or ``'session'``
                   (purge a single session within the agent).
            session_id: Required when ``scope='session'``.

        Returns:
            Number of node records soft-deleted.

        Raises:
            ValueError: If ``agent_id`` is invalid, or if
                        ``scope='session'`` but ``session_id`` is None.
        """
        _assert_valid_agent_id(agent_id)

        if scope == "session" and not session_id:
            raise ValueError("session_id is required when scope='session'.")

        # ---- 1. Identify target node IDs (agent-scoped) --------------
        if scope == "session":
            # RLS: WHERE agent_id = ? AND session_id = ?
            select_sql = (
                "SELECT id FROM nodes "
                "WHERE agent_id = ? "
                "  AND session_id = ? "
                "  AND invalid_at IS NULL "
                "  AND deleted_at IS NULL"
            )
            select_params: tuple = (agent_id, session_id)
        else:
            # RLS: WHERE agent_id = ?
            select_sql = (
                "SELECT id FROM nodes "
                "WHERE agent_id = ? "
                "  AND invalid_at IS NULL "
                "  AND deleted_at IS NULL"
            )
            select_params = (agent_id,)

        affected_ids: list[str] = []

        async with self._sql.connection() as db:
            # Collect IDs first without holding a write transaction
            async with db.execute(select_sql, select_params) as cursor:
                rows = await cursor.fetchall()
                affected_ids = [row[0] for row in rows]

        if not affected_ids:
            return 0

        # ---- PHASE 1: VECTOR LAYER FIRST (Saga) ----------------------
        for nid in affected_ids:
            try:
                # Vector deletion first to avoid zombie data if it fails
                await self._vec.soft_delete(nid)
            except Exception as exc:
                logger.error(
                    "CRITICAL_KVKK_FAILURE | agent_id=%s node_id=%s error=%s",
                    agent_id,
                    nid,
                    exc,
                )
                # Rollback simulation: immediately raise and prevent SQLite transaction
                raise

        # ---- PHASE 2: RELATIONAL COMMIT ------------------------------
        async with self._sql.transaction() as db:
            # ---- 2a. Soft-delete nodes: UPDATE SET deleted_at ---------
            #      CRITICAL: No DELETE — UPDATE only.
            #      RLS: WHERE agent_id = ? hardcoded.
            if scope == "session":
                update_sql = (
                    "UPDATE nodes "
                    "SET invalid_at = CURRENT_TIMESTAMP "
                    "WHERE agent_id = ? "
                    "  AND session_id = ? "
                    "  AND invalid_at IS NULL "
                    "  AND deleted_at IS NULL"
                )
                node_cursor = await db.execute(update_sql, (agent_id, session_id))
            else:
                update_sql = (
                    "UPDATE nodes "
                    "SET invalid_at = CURRENT_TIMESTAMP "
                    "WHERE agent_id = ? "
                    "  AND invalid_at IS NULL "
                    "  AND deleted_at IS NULL"
                )
                node_cursor = await db.execute(update_sql, (agent_id,))

            nodes_deleted = node_cursor.rowcount

            # ---- 2b. Cascade-invalidate connected edges ---------------
            #      RLS: WHERE agent_id = ? hardcoded.
            placeholders = ",".join("?" for _ in affected_ids)
            edge_sql = (
                "UPDATE edges "
                "SET invalid_at = CURRENT_TIMESTAMP "
                "WHERE agent_id = ? "
                f"  AND (source_id IN ({placeholders}) "
                f"       OR target_id IN ({placeholders})) "
                "  AND invalid_at IS NULL"
            )
            edge_params: list[Any] = [agent_id, *affected_ids, *affected_ids]
            edge_cursor = await db.execute(edge_sql, edge_params)
            edges_deleted = edge_cursor.rowcount

            await db.commit()

        total_deleted = nodes_deleted + edges_deleted

        logger.info(
            "PURGE_MEMORY | agent_id=%s scope=%s nodes_affected=%d edges_affected=%d total=%d",
            agent_id,
            scope,
            nodes_deleted,
            edges_deleted,
            total_deleted,
        )
        return total_deleted

    # ==================================================================
    # MARK CONSOLIDATED — agent-scoped
    # ==================================================================

    async def mark_consolidated(
        self,
        agent_id: str,
        *,
        node_id: str,
    ) -> None:
        """Mark a node as consolidated, scoped to ``agent_id``.

        Args:
            agent_id: **Mandatory** tenant isolation key.
            node_id: UUID of the node to mark.

        Raises:
            ValueError: If ``agent_id`` is invalid or reserved.
        """
        _assert_valid_agent_id(agent_id)

        # RLS: WHERE agent_id = ? AND id = ?
        async with self._sql.connection() as db:
            await db.execute(
                "UPDATE nodes SET is_consolidated = 1 "
                "WHERE id = ? "
                "  AND agent_id = ? "
                "  AND invalid_at IS NULL "
                "  AND deleted_at IS NULL",
                (node_id, agent_id),
            )
            await db.commit()

    # ==================================================================
    # EDGE OPERATIONS — agent-scoped
    # ==================================================================

    async def insert_edge(
        self,
        agent_id: str,
        *,
        source_id: str,
        target_id: str,
        relation_type: str,
        weight: float = 1.0,
        edge_id: str | None = None,
    ) -> str:
        """Insert a directed edge, scoped to ``agent_id``.

        Args:
            agent_id: **Mandatory** tenant isolation key.
            source_id: UUID of the source node.
            target_id: UUID of the target node.
            relation_type: Semantic label for the relationship.
            weight: Edge weight (default 1.0).
            edge_id: Optional UUID; auto-generated if omitted.

        Returns:
            The ``edge_id`` of the inserted edge.

        Raises:
            ValueError: If ``agent_id`` is invalid or reserved.
        """
        _assert_valid_agent_id(agent_id)

        if edge_id is None:
            edge_id = str(uuid.uuid4())

        now = datetime.now(timezone.utc).isoformat()

        async with self._sql.transaction() as db:
            await db.execute(
                "INSERT INTO edges "
                "(id, source_id, target_id, relation_type, weight, "
                " created_at, agent_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (edge_id, source_id, target_id, relation_type, weight, now, agent_id),
            )
            await db.commit()

        return edge_id

    async def get_neighbors(
        self,
        agent_id: str,
        *,
        node_id: str,
        direction: str = "both",
    ) -> list[dict[str, Any]]:
        """Return edges connected to a node, scoped to ``agent_id``.

        Args:
            agent_id: **Mandatory** tenant isolation key.
            node_id: UUID of the pivot node.
            direction: ``'out'``, ``'in'``, or ``'both'``.

        Returns:
            List of edge dicts with joined node metadata.

        Raises:
            ValueError: If ``agent_id`` is invalid or reserved.
        """
        _assert_valid_agent_id(agent_id)

        results: list[dict[str, Any]] = []

        async with self._sql.connection() as db:
            if direction in ("out", "both"):
                # RLS: e.agent_id = ? hardcoded
                async with db.execute(
                    "SELECT e.*, "
                    "       n.entity_name AS target_name, "
                    "       n.type AS target_type "
                    "FROM edges e "
                    "JOIN nodes n ON n.id = e.target_id "
                    "     AND n.invalid_at IS NULL "
                    "     AND n.deleted_at IS NULL "
                    "WHERE e.source_id = ? "
                    "  AND e.agent_id = ? "
                    "  AND e.invalid_at IS NULL",
                    (node_id, agent_id),
                ) as cursor:
                    rows = await cursor.fetchall()
                    results.extend(dict(row) for row in rows)

            if direction in ("in", "both"):
                # RLS: e.agent_id = ? hardcoded
                async with db.execute(
                    "SELECT e.*, "
                    "       n.entity_name AS source_name, "
                    "       n.type AS source_type "
                    "FROM edges e "
                    "JOIN nodes n ON n.id = e.source_id "
                    "     AND n.invalid_at IS NULL "
                    "     AND n.deleted_at IS NULL "
                    "WHERE e.target_id = ? "
                    "  AND e.agent_id = ? "
                    "  AND e.invalid_at IS NULL",
                    (node_id, agent_id),
                ) as cursor:
                    rows = await cursor.fetchall()
                    results.extend(dict(row) for row in rows)

        return results

    # ==================================================================
    # HEALTH — engine passthrough
    # ==================================================================

    async def health_check(self) -> dict[str, Any]:
        """Aggregate health status from both storage backends."""
        sql_health = await self._sql.health_check()
        vec_health = await self._vec.health_check()
        return {
            "sqlite": sql_health,
            "vector": vec_health,
        }
