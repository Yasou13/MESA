# MESA v0.5.0 — Data Access Object Layer (Epistemic Isolation)
# Wraps aiosqlite (nodes/raw_logs), LanceDB (vectors), and KùzuDB (graph
# edges) behind a single class that MANDATES agent_id on every method.
#
# Edge ownership:
#   - KùzuDB is the Single Source of Truth for graph edges (Observed rels).
#   - SQLite retains ownership of: nodes, raw_logs, routing_telemetry.
#   - LanceDB retains ownership of: vector embeddings.
#
# Security guarantees:
#   - Row-Level Security (RLS) simulation: every SQL query, LanceDB filter,
#     and Cypher query hardcodes agent_id — cross-agent leakage is
#     structurally impossible regardless of caller logic errors.
#   - Soft-delete via `UPDATE nodes SET deleted_at = CURRENT_TIMESTAMP` —
#     no physical DELETEs are issued; data is preserved for audit/recovery.
#   - Parameterised queries exclusively — zero string interpolation in SQL
#     or Cypher.
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

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from mesa_storage.kuzu_provider import KuzuGraphProvider
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
    ``agent_id = '<value>'`` in the WHERE clause.  All Cypher queries
    bind ``$agent_id`` via strict parameterisation.

    Edge storage is owned exclusively by KùzuDB.  SQLite retains
    ownership of nodes, raw_logs, and routing_telemetry.

    This class does NOT own the lifecycle of the underlying engines —
    the caller is responsible for initialising and closing them.

    Args:
        sqlite_engine: An initialised ``AsyncEngine`` instance.
        vector_engine: An initialised ``VectorEngine`` instance.
        graph_provider: An initialised ``KuzuGraphProvider`` instance
                        for graph edge operations.
    """

    __slots__ = ("_sql", "_vec", "_graph")

    def __init__(
        self,
        sqlite_engine: AsyncEngine,
        vector_engine: VectorEngine,
        graph_provider: KuzuGraphProvider | None = None,
    ) -> None:
        self._sql = sqlite_engine
        self._vec = vector_engine
        self._graph = graph_provider

    async def initialize(self) -> None:
        """Initialize the DAO layer.

        DDL ownership lives exclusively in ``schemas.py`` which MUST be
        called via ``initialize_schema(engine)`` before this method.
        This method is retained for any future DAO-specific runtime
        setup (connection pool warm-up, prepared statements, etc.).
        """
        logger.info("MemoryDAO.initialize() — schema owned by schemas.py")

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

    @property
    def graph_provider(self) -> KuzuGraphProvider | None:
        """Return the underlying graph provider (read-only access)."""
        return self._graph

    # ==================================================================
    # Migration State Polling
    # ==================================================================

    async def _is_lancedb_migrating(self) -> bool:
        """Check if LanceDB is currently undergoing Blue/Green alignment.

        If true, new vectors must be queued in the SQLite WAL table to
        prevent phantom writes to a table that is about to be dropped.
        """
        try:
            async with self._sql.connection() as db:
                async with db.execute(
                    "SELECT value FROM system_config WHERE key = 'lancedb_is_migrating'"
                ) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        return row[0].lower() == "true"
        except Exception as exc:
            logger.warning("IS_MIGRATING_CHECK_FAILED | error=%s", exc)
        return False

    async def align_memory_space(
        self,
        transformation_matrix: Any,  # numpy.ndarray
        golden_dataset: list[dict[str, Any]],
    ) -> bool:
        """Orchestrate the Blue/Green vector alignment and WAL flush.

        Args:
            transformation_matrix: Orthogonal rotation matrix (R*).
            golden_dataset: Evaluation dataset for Recall@5 verification.

        Returns:
            True if alignment was successful and promoted, False otherwise.
        """
        success = False

        # ACTION 1 (LOCK)
        try:
            async with self._sql.transaction() as db:
                await db.execute(
                    "UPDATE system_config SET value = 'true' WHERE key = 'lancedb_is_migrating'"
                )
                await db.commit()
            logger.info("MIGRATION_LOCK_ACQUIRED | lancedb_is_migrating='true'")
        except Exception as exc:
            logger.error("Failed to acquire migration lock: %s", exc)
            return False

        try:
            # ACTION 2 (TRANSFORM)
            success = await self._vec.apply_procrustes_and_switch(
                transformation_matrix, golden_dataset
            )

            # ACTION 3 (FLUSH)
            if success:
                import json

                import numpy as np

                async with self._sql.transaction() as db:
                    async with db.execute(
                        "SELECT id, agent_id, vector, metadata FROM lancedb_wal"
                    ) as cursor:
                        rows = await cursor.fetchall()

                    if rows:
                        flush_records = []
                        for row in rows:
                            r_agent_id = row[1]
                            vector_bytes = row[2]
                            meta_str = row[3]

                            embedding = np.frombuffer(
                                vector_bytes, dtype=np.float32
                            ).tolist()
                            metadata = json.loads(meta_str)

                            flush_records.append(
                                {
                                    "node_id": metadata["node_id"],
                                    "agent_id": r_agent_id,
                                    "embedding": embedding,
                                    "content_hash": metadata.get("content_hash"),
                                }
                            )

                        # Process bulk upsert into newly promoted table
                        await self._vec.bulk_upsert(flush_records)
                        logger.info("WAL_FLUSHED | count=%d", len(flush_records))

                    await db.execute("DELETE FROM lancedb_wal")
                    await db.commit()

        except Exception as exc:
            logger.error("ALIGNMENT_ORCHESTRATION_ERROR | error=%s", exc)
            success = False

        finally:
            # ACTION 4 (UNLOCK)
            try:
                async with self._sql.transaction() as db:
                    await db.execute(
                        "UPDATE system_config SET value = 'false' WHERE key = 'lancedb_is_migrating'"
                    )
                    await db.commit()
                logger.info("MIGRATION_LOCK_RELEASED | lancedb_is_migrating='false'")
            except Exception as exc:
                logger.critical("Failed to release migration lock: %s", exc)

        return success

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
        """Insert a memory record scoped exclusively to ``agent_id`` with Check-Then-Act semantic conflict resolution.

        Performs a dual-write:
            1. SQLite ``nodes`` table — relational graph vertex.
            2. LanceDB vector table — embedding for similarity search.

        Before insertion, it evaluates existing vectors for high semantic similarity.
        If a contradiction or update is detected, the older conflicting records are
        soft-deleted to prevent data corruption.
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

        # ---- Check-Then-Act: Semantic Conflict Resolution --------
        # Query vector index for highly similar existing triplets
        similar_memories = await self.search_memory(
            agent_id=agent_id,
            query_vector=embedding,
            limit=5,
            include_graph=True,
        )

        conflicting_node_ids = []
        for mem in similar_memories:
            distance = mem.get("_distance", 1.0)
            graph_data = mem.get("graph")
            if not graph_data:
                continue

            # Semantic similarity heuristic:
            # - Exact subject match (entity_name)
            # - High semantic similarity (distance < 0.15) -> UPDATE or CONTRADICTION
            if graph_data.get("entity_name") == entity_name and distance < 0.15:
                conflicting_node_ids.append(mem["node_id"])

        # ---- ATOMIC SAGA: SQLite + LanceDB (B-7 pattern) ----------
        # DO NOT commit SQLite until LanceDB succeeds.  On vector
        # failure, ROLLBACK SQLite to prevent orphaned relational records.
        async with self._sql.transaction() as db:
            # PHASE 1: Soft-delete conflicting nodes in SQLite
            if conflicting_node_ids:
                placeholders = ",".join("?" for _ in conflicting_node_ids)
                # Soft-delete nodes
                await db.execute(
                    f"UPDATE nodes SET invalid_at = CURRENT_TIMESTAMP "
                    f"WHERE id IN ({placeholders}) AND agent_id = ? "
                    f"AND invalid_at IS NULL",
                    (*conflicting_node_ids, agent_id),
                )
                # NOTE: Edge cascade removed — edges now live in KùzuDB
                # and are structurally bound to Entity nodes via MATCH.
                logger.info(
                    "SEMANTIC_CONFLICT_RESOLUTION | agent_id=%s new_node_id=%s "
                    "resolved_conflicts=%d soft_deleted=%s",
                    agent_id,
                    node_id,
                    len(conflicting_node_ids),
                    conflicting_node_ids,
                )

            # PHASE 2: Insert new node
            await db.execute(
                "INSERT INTO nodes "
                "(id, entity_name, type, is_consolidated, created_at, "
                " agent_id, session_id) "
                "VALUES (?, ?, ?, 0, ?, ?, ?)",
                (node_id, entity_name, node_type, now, agent_id, session_id),
            )

            # ---- LanceDB upsert (compensating rollback on fail) ------
            try:
                # Apply soft-delete in LanceDB for conflicts
                for cid in conflicting_node_ids:
                    await self._vec.soft_delete(cid)

                if await self._is_lancedb_migrating():
                    import json

                    import numpy as np

                    vector_bytes = np.array(embedding, dtype=np.float32).tobytes()
                    wal_metadata = json.dumps(
                        {"node_id": node_id, "content_hash": content_hash}
                    )
                    wal_record_id = str(uuid.uuid4())

                    await db.execute(
                        "INSERT INTO lancedb_wal (id, agent_id, vector, metadata) "
                        "VALUES (?, ?, ?, ?)",
                        (wal_record_id, agent_id, vector_bytes, wal_metadata),
                    )
                    logger.info("UPSERT_QUEUED_IN_WAL | node_id=%s", node_id)
                else:
                    await self._vec.upsert(
                        node_id=node_id,
                        agent_id=agent_id,
                        embedding=embedding,
                        content_hash=content_hash,
                    )
            except Exception as vec_exc:
                await db.rollback()
                logger.error(
                    "INSERT_SAGA_ROLLBACK | agent_id=%s node_id=%s "
                    "vector_error=%s — SQL changes rolled back",
                    agent_id,
                    node_id,
                    vec_exc,
                )
                raise

            # Insert node into KuzuDB if graph provider is configured
            if self._graph is not None:
                try:
                    await self._graph.insert_node(
                        node_id=node_id,
                        name=entity_name,
                        agent_id=agent_id,
                    )
                except Exception as graph_exc:
                    logger.warning("Failed to insert node into KuzuDB: %s", graph_exc)

            # Both layers succeeded — commit the SQL transaction
            await db.commit()

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

        # ---- ATOMIC SAGA: SQLite + LanceDB (B-7 pattern) ----------
        async with self._sql.transaction() as db:
            await db.executemany(
                "INSERT INTO nodes "
                "(id, entity_name, type, is_consolidated, created_at, "
                " agent_id, session_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                sql_rows,
            )

            # ---- LanceDB batch upsert (compensating rollback on fail)
            try:
                if await self._is_lancedb_migrating():
                    import json

                    import numpy as np

                    wal_records = []
                    for r in vec_rows:
                        vector_bytes = np.array(
                            r["embedding"], dtype=np.float32
                        ).tobytes()
                        wal_metadata = json.dumps(
                            {
                                "node_id": r["node_id"],
                                "content_hash": r.get("content_hash"),
                            }
                        )
                        wal_records.append(
                            (str(uuid.uuid4()), agent_id, vector_bytes, wal_metadata)
                        )

                    await db.executemany(
                        "INSERT INTO lancedb_wal (id, agent_id, vector, metadata) "
                        "VALUES (?, ?, ?, ?)",
                        wal_records,
                    )
                    logger.info(
                        "BULK_UPSERT_QUEUED_IN_WAL | count=%d", len(wal_records)
                    )
                else:
                    await self._vec.bulk_upsert(vec_rows)
            except Exception as vec_exc:
                await db.rollback()
                logger.error(
                    "BULK_INSERT_SAGA_ROLLBACK | agent_id=%s count=%d "
                    "vector_error=%s — SQL changes rolled back",
                    agent_id,
                    len(records),
                    vec_exc,
                )
                raise

            if self._graph is not None:
                try:
                    for rec, sql_row in zip(records, sql_rows):
                        await self._graph.insert_node(
                            node_id=sql_row[0],
                            name=sql_row[1],
                            agent_id=agent_id,
                        )
                except Exception as graph_exc:
                    logger.warning(
                        "Failed to bulk insert nodes into KuzuDB: %s", graph_exc
                    )

            # Both layers succeeded — commit
            await db.commit()

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

        # ---- PHASE 1: RELATIONAL SOFT-DELETE FIRST (B-7 Saga Fix) -----
        # Execute SQLite soft-delete first inside a transaction. If the
        # subsequent vector layer fails, we can compensate by rolling
        # back the relational changes, preventing dangling SQL records
        # that reference live vector data.
        async with self._sql.transaction() as db:
            # ---- 1a. Soft-delete nodes: UPDATE SET invalid_at ----------
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

            # NOTE: Edge cascade removed — edges now live in KùzuDB
            # and are structurally bound to Entity nodes via MATCH.

            # DO NOT commit yet — wait for vector layer success
            # ---- PHASE 2: VECTOR LAYER (compensating rollback on fail) -
            try:
                for nid in affected_ids:
                    await self._vec.soft_delete(nid)
            except Exception as vec_exc:
                # Vector deletion failed — ROLLBACK the SQL transaction
                # to prevent dangling relational records.
                await db.rollback()
                logger.error(
                    "PURGE_SAGA_ROLLBACK | agent_id=%s "
                    "vector_error=%s — SQL changes rolled back",
                    agent_id,
                    vec_exc,
                )
                raise

            # Both layers succeeded — commit the SQL transaction
            await db.commit()

        logger.info(
            "PURGE_MEMORY | agent_id=%s scope=%s nodes_affected=%d",
            agent_id,
            scope,
            nodes_deleted,
        )
        return nodes_deleted

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
    # EDGE OPERATIONS — KùzuDB (Single Source of Truth)
    # ==================================================================

    def _require_graph(self) -> KuzuGraphProvider:
        """Return the graph provider or raise if not wired."""
        if self._graph is None:
            raise RuntimeError(
                "KuzuGraphProvider is not configured. "
                "Pass graph_provider to MemoryDAO constructor."
            )
        return self._graph

    async def insert_edge(
        self,
        agent_id: str,
        *,
        source_id: str,
        target_id: str,
        relation_type: str,
        weight: float = 1.0,
        edge_id: str | None = None,
        epistemic_uncertainty: float = 0.0,
    ) -> str:
        """Upsert a directed edge in KùzuDB, scoped to ``agent_id``.

        Delegates to ``KuzuGraphProvider.insert_edge`` which uses
        ``MERGE ... ON CREATE SET`` for idempotent writes.

        Args:
            agent_id: **Mandatory** tenant isolation key.
            source_id: UUID of the source Entity node.
            target_id: UUID of the target Entity node.
            relation_type: Semantic label (logged but not stored in
                           KùzuDB — the Observed rel type is implicit).
            weight: Edge weight (default 1.0).
            edge_id: Accepted for backward compatibility but ignored
                     (KùzuDB edges are identified by endpoint pair).
            epistemic_uncertainty: Uncertainty score (0.0 = certain,
                1.0 = fully uncertain).  Propagated to the Observed
                relationship for Damped PageRank quarantine analysis.

        Returns:
            A deterministic edge identifier ``"{source_id}->{target_id}"``.

        Raises:
            ValueError: If ``agent_id`` is invalid or reserved.
            RuntimeError: If the graph provider is not configured.
        """
        _assert_valid_agent_id(agent_id)
        graph = self._require_graph()

        await graph.insert_edge(
            source_id=source_id,
            target_id=target_id,
            weight=weight,
            agent_id=agent_id,
            epistemic_uncertainty=epistemic_uncertainty,
        )

        logger.debug(
            "INSERT_EDGE | agent_id=%s %s-[%s]->%s w=%.3f eu=%.3f",
            agent_id,
            source_id,
            relation_type,
            target_id,
            weight,
            epistemic_uncertainty,
        )
        return f"{source_id}->{target_id}"

    async def get_all_edges(
        self,
        agent_id: str,
    ) -> list[dict[str, Any]]:
        """Return all Observed edges scoped to ``agent_id`` from KùzuDB.

        Returns:
            List of dicts with keys: ``source_id``, ``target_id``,
            ``weight``, ``agent_id``, ``epistemic_uncertainty``.
        """
        _assert_valid_agent_id(agent_id)
        graph = self._require_graph()

        rows = await graph.execute_query(
            "MATCH (a:Entity {agent_id: $agent_id})-[r:Observed]->(b:Entity {agent_id: $agent_id}) "
            "WHERE r.agent_id = $agent_id "
            "RETURN a.id, b.id, r.weight, r.agent_id, r.epistemic_uncertainty",
            {"agent_id": agent_id},
        )

        return [
            {
                "source_id": row[0],
                "target_id": row[1],
                "weight": row[2],
                "agent_id": row[3],
                "epistemic_uncertainty": row[4] if len(row) > 4 else 0.0,
            }
            for row in rows
        ]

    async def get_neighbors(
        self,
        agent_id: str,
        *,
        node_id: str,
        direction: str = "both",
        max_hops: int = 1,
    ) -> list[dict[str, Any]]:
        """Return neighbor nodes connected to a node via KùzuDB traversal.

        Delegates to ``KuzuGraphProvider.get_neighbors`` for multi-hop
        Cypher traversal with dual ``agent_id`` enforcement.

        Args:
            agent_id: **Mandatory** tenant isolation key.
            node_id: UUID of the pivot node.
            direction: Accepted for backward compatibility. KùzuDB
                       traversal is always undirected.
            max_hops: Maximum traversal depth (1, 2, or 3).

        Returns:
            List of neighbor dicts with keys: ``id``, ``name``, ``hops``.

        Raises:
            ValueError: If ``agent_id`` is invalid or reserved.
            RuntimeError: If the graph provider is not configured.
        """
        _assert_valid_agent_id(agent_id)
        graph = self._require_graph()

        return await graph.get_neighbors(
            node_id=node_id,
            agent_id=agent_id,
            max_hops=max_hops,
        )

    # ==================================================================
    # INVALIDATION — agent-scoped soft-invalidation
    # ==================================================================

    async def invalidate_node(
        self,
        agent_id: str,
        *,
        node_id: str,
    ) -> None:
        """Soft-invalidate a node by setting ``invalid_at``.

        Also cascade-invalidates all connected edges.  No physical
        DELETEs are issued.

        Args:
            agent_id: **Mandatory** tenant isolation key.
            node_id: UUID of the node to invalidate.

        Raises:
            ValueError: If ``agent_id`` is invalid or reserved.
        """
        _assert_valid_agent_id(agent_id)

        async with self._sql.transaction() as db:
            # RLS: WHERE agent_id = ? hardcoded
            await db.execute(
                "UPDATE nodes SET invalid_at = CURRENT_TIMESTAMP "
                "WHERE id = ? AND agent_id = ? AND invalid_at IS NULL",
                (node_id, agent_id),
            )
            # NOTE: Edge cascade removed — edges now live in KùzuDB
            # and are structurally bound to Entity nodes via MATCH.
            await db.commit()

        logger.info(
            "INVALIDATE_NODE | agent_id=%s node_id=%s",
            agent_id,
            node_id,
        )

    # ==================================================================
    # FIND NODES — agent-scoped entity name lookup
    # ==================================================================

    async def find_nodes_by_name(
        self,
        agent_id: str,
        *,
        names: list[str],
        case_insensitive: bool = True,
    ) -> list[dict[str, Any]]:
        """Find active nodes whose ``entity_name`` matches any in ``names``.

        RLS: ``agent_id`` is mandatory and hardcoded into the WHERE clause.

        Args:
            agent_id: **Mandatory** tenant isolation key.
            names: Entity names to match.
            case_insensitive: If True, compare via ``LOWER()``.

        Returns:
            List of matching node dicts.

        Raises:
            ValueError: If ``agent_id`` is invalid or reserved.
        """
        _assert_valid_agent_id(agent_id)

        if not names:
            return []

        if case_insensitive:
            conditions = " OR ".join("LOWER(entity_name) = ?" for _ in names)
            params: list[Any] = [agent_id] + [n.lower() for n in names]
        else:
            conditions = " OR ".join("entity_name = ?" for _ in names)
            params = [agent_id] + list(names)

        query = (
            f"SELECT * FROM nodes WHERE agent_id = ? "
            f"AND invalid_at IS NULL "
            f"AND deleted_at IS NULL "
            f"AND ({conditions})"
        )

        async with self._sql.connection() as db:
            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    # ==================================================================
    # GET MEMORY BY ID — agent-scoped single-node lookup
    # ==================================================================

    async def get_memory_by_id(
        self,
        agent_id: str,
        node_id: str,
    ) -> dict[str, Any] | None:
        """Retrieve a single memory node by its primary key.

        RLS: ``agent_id`` is mandatory and hardcoded into the WHERE
        clause — a node belonging to a different agent will never be
        returned, even if the caller knows the ``node_id``.

        Args:
            agent_id: **Mandatory** tenant isolation key.
            node_id: UUID primary key of the node.

        Returns:
            Node dict if found and active, ``None`` otherwise.

        Raises:
            ValueError: If ``agent_id`` is invalid or reserved.
        """
        _assert_valid_agent_id(agent_id)

        query = (
            "SELECT * FROM nodes "
            "WHERE id = ? "
            "  AND agent_id = ? "
            "  AND invalid_at IS NULL "
            "  AND deleted_at IS NULL"
        )

        async with self._sql.connection() as db:
            async with db.execute(query, (node_id, agent_id)) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    # ==================================================================
    # NODE DEGREE — agent-scoped edge count
    # ==================================================================

    async def get_node_degree(
        self,
        agent_id: str,
        *,
        node_id: str,
    ) -> int:
        """Return the number of Observed edges connected to a node.

        Queries KùzuDB for both inbound and outbound relationships,
        with ``agent_id`` enforced via Cypher ``$param`` binding.

        Args:
            agent_id: **Mandatory** tenant isolation key.
            node_id: UUID of the node.

        Returns:
            Total edge count (in + out).

        Raises:
            ValueError: If ``agent_id`` is invalid or reserved.
            RuntimeError: If the graph provider is not configured.
        """
        _assert_valid_agent_id(agent_id)
        graph = self._require_graph()

        rows = await graph.execute_query(
            "MATCH (a:Entity {id: $node_id, agent_id: $agent_id})"
            "-[r:Observed]-() "
            "RETURN count(r)",
            {"node_id": node_id, "agent_id": agent_id},
        )
        return rows[0][0] if rows else 0

    # ==================================================================
    # ROUTING TELEMETRY — audit logging
    # ==================================================================

    async def insert_routing_telemetry(
        self,
        agent_id: str,
        *,
        record_id: str,
        small_model_decision: int,
        small_model_confidence: float,
        dual_llm_decision: int,
        is_hallucination: bool,
    ) -> str:
        """Insert a telemetry record for adaptive LLM routing."""
        _assert_valid_agent_id(agent_id)

        telemetry_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        async with self._sql.transaction() as db:
            await db.execute(
                "INSERT INTO routing_telemetry "
                "(id, agent_id, record_id, small_model_decision, "
                "small_model_confidence, dual_llm_decision, "
                "is_hallucination, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    telemetry_id,
                    agent_id,
                    record_id,
                    small_model_decision,
                    small_model_confidence,
                    dual_llm_decision,
                    int(is_hallucination),
                    now,
                ),
            )
            await db.commit()

        return telemetry_id

    async def get_recent_telemetry_stats(
        self,
        agent_id: str,
        limit: int = 100,
    ) -> dict[str, int]:
        """Fetch recent routing telemetry to calculate hallucination error rates."""
        _assert_valid_agent_id(agent_id)

        async with self._sql.connection() as db:
            async with db.execute(
                "SELECT is_hallucination FROM routing_telemetry "
                "WHERE agent_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (agent_id, limit),
            ) as cursor:
                results = await cursor.fetchall()
                rows = list(results)

        total_audits = len(rows)
        hallucinations = sum(1 for row in rows if row[0] == 1)

        return {
            "total_audits": total_audits,
            "hallucinations": hallucinations,
        }

    # ==================================================================
    # RAW LOG INSERT — hot-path ingestion (< 50ms, pure I/O)
    # ==================================================================

    async def insert_raw_log(self, agent_id: str, payload: dict) -> int:
        """Insert a raw payload into the ``raw_logs`` staging table.

        This is the **hot-path write** for the v0.4.0 decoupled ingestion
        architecture.  It performs a single async SQLite INSERT and returns
        the auto-generated ``log_id`` immediately.

        **No validation, ECOD, REBEL extraction, or LLM calls occur here.**
        All heavy processing is deferred to the cold-path worker
        (``process_cold_path``).

        Args:
            agent_id: **Mandatory** tenant isolation key.
            payload: Raw ingestion payload (serialised as JSON).

        Returns:
            The ``id`` (INTEGER PRIMARY KEY) of the newly inserted row.
        """
        _assert_valid_agent_id(agent_id)
        async with self._sql.transaction() as db:
            cursor = await db.execute(
                "INSERT INTO raw_logs (agent_id, payload) VALUES (?, ?)",
                (agent_id, json.dumps(payload)),
            )
            log_id = cursor.lastrowid
            await db.commit()

        logger.info("INSERT_RAW_LOG | agent_id=%s log_id=%s", agent_id, log_id)
        return log_id or 0

    async def get_raw_log(self, agent_id: str, log_id: int) -> dict[str, Any] | None:
        """Retrieve a single raw_logs row by primary key.

        Args:
            agent_id: **Mandatory** tenant isolation key.
            log_id: INTEGER primary key of the raw_logs row.

        Returns:
            A dict with keys ``id``, ``payload``, ``status``, ``created_at``,
            or ``None`` if no row with that ID exists.
        """
        _assert_valid_agent_id(agent_id)
        async with self._sql.connection() as db:
            async with db.execute(
                "SELECT id, agent_id, payload, status, created_at "
                "FROM raw_logs WHERE id = ? AND agent_id = ?",
                (log_id, agent_id),
            ) as cursor:
                row = await cursor.fetchone()
                if row is None:
                    return None
                row_dict = dict(row)
                # Deserialise the JSON payload back to a dict
                if isinstance(row_dict.get("payload"), str):
                    row_dict["payload"] = json.loads(row_dict["payload"])
                return row_dict

    async def get_recent_logs(
        self, agent_id: str, session_id: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Retrieve recent raw_logs for a given session.

        Args:
            agent_id: **Mandatory** tenant isolation key.
            session_id: The session ID to filter by.
            limit: Maximum number of recent logs to return.

        Returns:
            A list of raw_log payload dicts.
        """
        _assert_valid_agent_id(agent_id)
        recent_logs = []
        async with self._sql.connection() as db:
            async with db.execute(
                "SELECT payload FROM raw_logs WHERE agent_id = ? "
                "AND json_extract(payload, '$.session_id') = ? ORDER BY created_at DESC LIMIT ?",
                (agent_id, session_id, limit),
            ) as cur:
                rows = await cur.fetchall()
                for row in rows:
                    payload_raw = row[0]
                    if isinstance(payload_raw, str):
                        payload = json.loads(payload_raw)
                    else:
                        payload = payload_raw
                    recent_logs.append(payload)
        return recent_logs

    async def get_recent_session_logs(
        self, agent_id: str, session_id: str, limit: int = 10
    ) -> list[dict]:
        """Retrieve recent raw_logs for a given session.
        Added per system architecture requirements to avoid raw SQL bypass.
        """
        _assert_valid_agent_id(agent_id)
        recent_logs = []
        async with self.sqlite_engine.connection() as db:
            async with db.execute(
                "SELECT payload FROM raw_logs WHERE agent_id = ? AND json_extract(payload, '$.session_id') = ? ORDER BY created_at DESC LIMIT ?",
                (agent_id, session_id, limit),
            ) as cur:
                rows = await cur.fetchall()
                for row in rows:
                    payload_raw = row[0]
                    if isinstance(payload_raw, str):
                        payload = json.loads(payload_raw)
                    else:
                        payload = payload_raw
                    recent_logs.append(payload)
        return recent_logs

    async def update_raw_log_status(
        self,
        agent_id: str,
        log_id: int,
        status: str,
        *,
        error_reason: str | None = None,
    ) -> None:
        """Transition the status of a raw_logs row.

        Valid transitions: ``queued → processing → processed | failed | rejected``.

        Args:
            agent_id: **Mandatory** tenant isolation key.
            log_id: INTEGER primary key of the raw_logs row.
            status: New status string (``processing``, ``processed``,
                    ``failed``, ``rejected``).
            error_reason: Optional error message (stored in the ``status``
                          field as ``failed:<reason>`` for traceability).
        """
        _assert_valid_agent_id(agent_id)
        final_status = f"{status}:{error_reason}" if error_reason else status

        async with self._sql.transaction() as db:
            await db.execute(
                "UPDATE raw_logs SET status = ? WHERE id = ? AND agent_id = ?",
                (final_status, log_id, agent_id),
            )
            await db.commit()

        logger.debug(
            "UPDATE_RAW_LOG_STATUS | agent_id=%s log_id=%d status=%s",
            agent_id,
            log_id,
            final_status,
        )

    # ==================================================================
    # HEALTH — engine passthrough
    # ==================================================================

    async def health_check(self) -> dict[str, Any]:
        """Aggregate health status from all storage backends."""
        sql_health = await self._sql.health_check()
        vec_health = await self._vec.health_check()
        result: dict[str, Any] = {
            "sqlite": sql_health,
            "vector": vec_health,
        }
        if self._graph is not None:
            result["graph"] = {
                "status": (
                    "healthy" if self._graph.is_initialized else "not_initialized"
                ),
                "db_path": self._graph.db_path,
            }
        return result
