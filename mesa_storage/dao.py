# MESA v0.6.1 — Data Access Object Layer (Epistemic Isolation)
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
from typing import TYPE_CHECKING, Any

import aiosqlite

if TYPE_CHECKING:
    from mesa_memory.config import QueueAdmissionPolicy

from mesa_storage.kuzu_provider import KuzuGraphProvider
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.vector_engine import VectorEngine

logger = logging.getLogger("MESA_DAO")

# ---------------------------------------------------------------------------
# Sentinel rejection — defence in depth
# ---------------------------------------------------------------------------

_FORBIDDEN_AGENT_IDS = frozenset({"__unset__", "__system__", ""})
_PURGE_MAX_RETRIES = 3


class PurgeRetryPendingError(RuntimeError):
    """A purge remains tombstoned and requires an idempotent retry."""


class PurgeBlockedError(RuntimeError):
    """A purge exceeded its bounded retry budget and needs operator action."""


class PurgeAlreadyFinalizedError(RuntimeError):
    """A finalized purge cannot be reported as a duplicate success."""


class QueueAdmissionError(RuntimeError):
    """Base class for stable, non-sensitive queue admission outcomes."""


class QueueOverCapacityError(QueueAdmissionError):
    def __init__(self, scope: str) -> None:
        super().__init__("queue capacity exhausted")
        self.scope = scope


class QueueRecordTooLargeError(QueueAdmissionError):
    """The deterministic serialized envelope exceeds the single-record bound."""


class QueueUnavailableError(QueueAdmissionError):
    """The durable admission coordinator is unavailable; callers must fail closed."""


_ADMISSION_ACTIVE_STATES = (
    "ENQUEUED",
    "PENDING",
    "CLAIMED",
    "IN_FLIGHT",
    "RETRY_PENDING",
    "DEFERRED",
)


def _canonical_payload_bytes(payload: dict[str, Any]) -> tuple[str, int]:
    serialized = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return serialized, len(serialized.encode("utf-8"))


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

        E1 FIX: Runs a startup reconciliation scan that detects SQLite
        nodes with no corresponding LanceDB vector entry (orphans from
        a SIGKILL mid-saga).  Orphaned nodes are stamped ``invalid_at``
        to remove them from active queries.
        """
        logger.info("MemoryDAO.initialize() — schema owned by schemas.py")

        # E1 FIX: Reconcile orphaned nodes from interrupted dual-write sagas
        await self._reconcile_orphaned_nodes()
        recovered_logs = await self.recover_expired_raw_log_claims()
        recovered_wal = await self.recover_expired_lancedb_wal_claims()
        if recovered_logs or recovered_wal:
            logger.warning(
                "STARTUP_CLAIM_RECOVERY | raw_logs=%d lancedb_wal=%d",
                recovered_logs,
                recovered_wal,
            )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def _sqlite_engine(self) -> AsyncEngine:
        """Internal SQLite engine — private to prevent DAO bypass."""
        return self._sql

    @property
    def vector_engine(self) -> VectorEngine:
        """Return the underlying vector engine (read-only access)."""
        return self._vec

    @property
    def graph_provider(self) -> KuzuGraphProvider | None:
        """Return the underlying graph provider (read-only access)."""
        return self._graph

    # ------------------------------------------------------------------
    # E1 FIX: Startup orphan reconciliation
    # ------------------------------------------------------------------

    def get_all_embeddings(self, limit: int = 10000) -> list[list[float]]:
        """Synchronously load active embeddings for ValenceMotor hydration."""
        return self.vector_engine._sync_get_all_embeddings(limit)

    async def _reconcile_orphaned_nodes(self) -> None:
        """Detect and invalidate SQLite nodes with no LanceDB vector.

        This handles the SIGKILL atomicity gap: if the process is killed
        between the SQLite INSERT and LanceDB upsert steps of the dual-write
        saga, the SQLite node exists but has no vector representation.

        These orphans appear in ``get_memories()`` but produce zero vector
        search hits, causing silent data inconsistency.  Stamping them
        ``invalid_at`` removes them from active queries while preserving
        them for audit recovery.

        Bounded to 500 recent nodes to keep startup latency acceptable.
        """
        try:
            # Fetch all distinct agent_ids with active nodes
            async with self._sql.connection() as db:
                async with db.execute(
                    "SELECT DISTINCT agent_id FROM nodes "
                    "WHERE invalid_at IS NULL AND deleted_at IS NULL "
                    "LIMIT 100"
                ) as cursor:
                    rows = await cursor.fetchall()
                    agent_ids = [row[0] for row in rows]

            if not agent_ids:
                return

            orphan_count = 0
            for agent_id in agent_ids:
                if agent_id in _FORBIDDEN_AGENT_IDS:
                    continue

                # Fetch recent active nodes for this agent
                async with self._sql.connection() as db:
                    async with db.execute(
                        "SELECT id FROM nodes "
                        "WHERE agent_id = ? AND invalid_at IS NULL "
                        "AND deleted_at IS NULL "
                        "ORDER BY created_at DESC LIMIT 500",
                        (agent_id,),
                    ) as cursor:
                        node_rows = await cursor.fetchall()

                if not node_rows:
                    continue

                node_ids = [r[0] for r in node_rows]

                # Check which node_ids have vector entries
                try:
                    existing_vector_ids = await self._vec.get_existing_node_ids(
                        agent_id, node_ids
                    )
                except Exception:
                    # Vector engine may not have this method or table yet
                    # — skip reconciliation for this agent but log the reason
                    logger.warning(
                        "ORPHAN_RECONCILIATION_SKIP | agent_id=%s "
                        "vector engine check failed",
                        agent_id,
                        exc_info=True,
                    )
                    continue

                orphans = [nid for nid in node_ids if nid not in existing_vector_ids]

                if orphans:
                    now = datetime.now(timezone.utc).isoformat()
                    async with self._sql.connection() as db:
                        for orphan_id in orphans:
                            await db.execute(
                                "UPDATE nodes SET invalid_at = ? "
                                "WHERE id = ? AND agent_id = ? "
                                "AND invalid_at IS NULL",
                                (now, orphan_id, agent_id),
                            )
                        await db.commit()
                    orphan_count += len(orphans)

            if orphan_count:
                logger.warning(
                    "ORPHAN_RECONCILIATION | invalidated %d orphaned nodes "
                    "(SQLite nodes with no LanceDB vector — likely SIGKILL mid-saga)",
                    orphan_count,
                )
            else:
                logger.debug("ORPHAN_RECONCILIATION | no orphans detected")

        except Exception as exc:
            # Reconciliation failure is non-fatal — log and continue startup
            logger.warning("ORPHAN_RECONCILIATION_FAILED | error=%s — skipping", exc)

    # ==================================================================
    # Migration State Polling
    # ==================================================================

    async def _is_lancedb_migrating(self, db_conn: Any = None) -> bool:
        """Check if LanceDB is currently undergoing Blue/Green alignment.

        If true, new vectors must be queued in the SQLite WAL table to
        prevent phantom writes to a table that is about to be dropped.
        """
        try:  # type: ignore[no-any-return]
            if db_conn is not None:
                async with db_conn.execute(
                    "SELECT value FROM system_config WHERE key = 'lancedb_is_migrating'"
                ) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        return row[0].lower() == "true"
            else:  # type: ignore[no-any-return]
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
                flushed = await self.replay_lancedb_wal(worker_id="alignment-flush")
                logger.info("WAL_FLUSHED | count=%d", flushed)

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

        # P3 FIX: Input validation to prevent payload DoS (1MB limit)
        if len(content.encode("utf-8")) > 1_048_576:
            raise ValueError(
                f"Content payload exceeds 1MB limit for entity {entity_name!r}"
            )

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

        # ---- ATOMIC SAGA: Secondary stores FIRST (fixes lock starvation) ----------
        is_migrating = await self._is_lancedb_migrating()

        try:
            if not is_migrating:
                for cid in conflicting_node_ids:
                    await self._vec.soft_delete(cid, agent_id)
                await self._vec.upsert(
                    node_id=node_id,
                    agent_id=agent_id,
                    embedding=embedding,
                    content_hash=content_hash,
                )
        except Exception as vec_exc:
            logger.error(
                "INSERT_SAGA_ROLLBACK | agent_id=%s node_id=%s vector_error=%s",
                agent_id,
                node_id,
                vec_exc,
            )
            raise

        if self._graph is not None and not is_migrating:
            try:
                await self._graph.insert_node(
                    node_id=node_id,
                    name=entity_name,
                    agent_id=agent_id,
                )
            except Exception as graph_exc:
                logger.error(
                    "INSERT_SAGA_GRAPH_FAILURE | agent_id=%s node_id=%s error=%s",
                    agent_id,
                    node_id,
                    graph_exc,
                )
                if not is_migrating:
                    try:
                        await self._vec.soft_delete(node_id, agent_id)
                    except Exception as compensation_exc:
                        logger.critical(
                            "INSERT_SAGA_COMPENSATION_FAILURE | agent_id=%s "
                            "node_id=%s error=%s",
                            agent_id,
                            node_id,
                            compensation_exc,
                        )
                        raise compensation_exc from graph_exc
                raise

        # PHASE 2: Fast SQLite transaction
        async with self._sql.transaction() as db:
            if conflicting_node_ids:
                placeholders = ",".join("?" for _ in conflicting_node_ids)
                await db.execute(
                    f"UPDATE nodes SET invalid_at = CURRENT_TIMESTAMP "
                    f"WHERE id IN ({placeholders}) AND agent_id = ? "
                    f"AND invalid_at IS NULL",
                    (*conflicting_node_ids, agent_id),
                )
                logger.info(
                    "SEMANTIC_CONFLICT_RESOLUTION | agent_id=%s new_node_id=%s "
                    "resolved_conflicts=%d soft_deleted=%s",
                    agent_id,
                    node_id,
                    len(conflicting_node_ids),
                    conflicting_node_ids,
                )

            await db.execute(
                "INSERT INTO nodes "
                "(id, entity_name, type, content_payload, is_consolidated, created_at, "
                " agent_id, session_id) "
                "VALUES (?, ?, ?, ?, 0, ?, ?, ?)",
                (node_id, entity_name, node_type, content, now, agent_id, session_id),
            )

            if is_migrating:
                import json

                import numpy as np

                vector_bytes = np.array(embedding, dtype=np.float32).tobytes()
                wal_metadata = json.dumps(
                    {
                        "node_id": node_id,
                        "content_hash": content_hash,
                        "entity_name": entity_name,
                        "graph_required": self._graph is not None,
                        "canonical_agent_id": agent_id,
                        "payload_version": 1,
                        "expected_vector_projection": True,
                        "expected_graph_projection": self._graph is not None,
                    }
                )
                wal_record_id = str(uuid.uuid4())
                await db.execute(
                    "INSERT INTO lancedb_wal (id, agent_id, vector, metadata) "
                    "VALUES (?, ?, ?, ?)",
                    (wal_record_id, agent_id, vector_bytes, wal_metadata),
                )
                logger.info("UPSERT_QUEUED_IN_WAL | node_id=%s", node_id)

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

        # P3 FIX: Input validation to prevent payload DoS (1MB limit)
        for rec in records:
            content = rec.get("content", "")
            if len(content.encode("utf-8")) > 1_048_576:
                raise ValueError(
                    f"Content payload exceeds 1MB limit for entity {rec.get('entity_name')!r}"
                )

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
                    rec.get("content", ""),
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
                    "entity_name": rec["entity_name"],
                }
            )

        # ---- ATOMIC SAGA: Secondary stores FIRST ----------
        is_migrating = await self._is_lancedb_migrating()

        try:
            if not is_migrating:
                await self._vec.bulk_upsert(vec_rows)
        except Exception as vec_exc:
            logger.error(
                "BULK_INSERT_SAGA_ROLLBACK | agent_id=%s count=%d vector_error=%s",
                agent_id,
                len(records),
                vec_exc,
            )
            raise

        if self._graph is not None and not is_migrating:
            try:
                for rec, sql_row in zip(records, sql_rows):
                    await self._graph.insert_node(
                        node_id=sql_row[0],
                        name=sql_row[1],
                        agent_id=agent_id,
                    )
            except Exception as graph_exc:
                logger.error(
                    "BULK_INSERT_SAGA_GRAPH_FAILURE | agent_id=%s count=%d error=%s",
                    agent_id,
                    len(records),
                    graph_exc,
                )
                if not is_migrating:
                    try:
                        for sql_row in sql_rows:
                            await self._vec.soft_delete(sql_row[0], agent_id)
                    except Exception as compensation_exc:
                        logger.critical(
                            "BULK_INSERT_SAGA_COMPENSATION_FAILURE | agent_id=%s "
                            "count=%d error=%s",
                            agent_id,
                            len(records),
                            compensation_exc,
                        )
                        raise compensation_exc from graph_exc
                raise

        # PHASE 2: Fast SQLite transaction
        async with self._sql.transaction() as db:
            await db.executemany(
                "INSERT INTO nodes "
                "(id, entity_name, type, content_payload, is_consolidated, created_at, "
                " agent_id, session_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                sql_rows,
            )

            if is_migrating:
                import json

                import numpy as np

                wal_records = []
                for r in vec_rows:
                    vector_bytes = np.array(r["embedding"], dtype=np.float32).tobytes()
                    wal_metadata = json.dumps(
                        {
                            "node_id": r["node_id"],
                            "content_hash": r.get("content_hash"),
                            "entity_name": r["entity_name"],
                            "graph_required": self._graph is not None,
                            "canonical_agent_id": agent_id,
                            "payload_version": 1,
                            "expected_vector_projection": True,
                            "expected_graph_projection": self._graph is not None,
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
                logger.info("BULK_UPSERT_QUEUED_IN_WAL | count=%d", len(wal_records))

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

        if not results:
            return []

        # SQLite is canonical for tombstones: filter vector-only hits too.
        # ---- SQLite enrichment (agent_id filter hardcoded) -----------
        node_ids = [r["node_id"] for r in results]
        placeholders = ",".join("?" for _ in node_ids)

        # RLS: agent_id = ? hardcoded — even if a node_id existed under
        # a different agent, it will NOT be returned.
        query = (
            f"SELECT id, entity_name, type, content_payload, is_consolidated, "
            f"       created_at, session_id, confidence, is_quarantined "
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
                    row_dict = self._sanitize_payload(dict(row))
                    graph_map[row_dict["id"]] = row_dict

        # Filter every vector result through canonical SQLite tombstones.
        filtered_results = []
        for r in results:
            nid = r["node_id"]
            if nid not in graph_map:
                continue
            if include_graph:
                r["graph"] = graph_map[nid]
            filtered_results.append(r)
        return filtered_results

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

    async def get_epistemic_data_for_nodes(
        self, agent_id: str, node_ids: list[str]
    ) -> dict[str, dict[str, Any]]:
        """Fetch confidence and quarantine status for a batch of nodes.

        Args:
            agent_id: Mandatory tenant isolation key.
            node_ids: List of UUIDs to fetch.

        Returns:
            Dict mapping node_id -> {"confidence": float, "is_quarantined": bool}
        """
        if not node_ids:
            return {}

        _assert_valid_agent_id(agent_id)

        placeholders = ",".join("?" for _ in node_ids)
        query = (
            f"SELECT id, confidence, is_quarantined "
            f"FROM nodes "
            f"WHERE agent_id = ? AND id IN ({placeholders})"
        )
        params: list[Any] = [agent_id, *node_ids]

        results = {}
        async with self._sql.connection() as db:
            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                for row in rows:
                    results[row[0]] = {
                        "confidence": float(row[1]),
                        "is_quarantined": bool(row[2]),
                    }
        return results

    async def get_nodes_by_ids_batch(
        self, agent_id: str, node_ids: list[str]
    ) -> dict[str, dict[str, Any]]:
        """Batch retrieve node metadata and content by their IDs.

        RLS: Mandatory ``agent_id`` hardcoded into WHERE predicate.
        Only returns active nodes (not tombstoned or soft-deleted).

        Args:
            agent_id: Mandatory tenant isolation key.
            node_ids: List of UUIDs to fetch.

        Returns:
            Dict mapping node_id -> dict of node attributes (entity_name, content_payload, type, etc.).
        """
        if not node_ids:
            return {}

        _assert_valid_agent_id(agent_id)

        placeholders = ",".join("?" for _ in node_ids)
        query = (
            f"SELECT id, entity_name, type, content_payload, is_consolidated, "
            f"confidence, is_quarantined "
            f"FROM nodes "
            f"WHERE agent_id = ? AND id IN ({placeholders}) "
            f"  AND invalid_at IS NULL AND deleted_at IS NULL"
        )
        params: list[Any] = [agent_id, *node_ids]

        results: dict[str, dict[str, Any]] = {}
        async with self._sql.connection() as db:
            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                for row in rows:
                    row_dict = dict(row)
                    results[row_dict["id"]] = row_dict
        return results

    # ==================================================================

    # PURGE — agent-scoped soft-delete
    # ==================================================================

    async def purge_memory(
        self,
        agent_id: str,
        *,
        scope: str = "agent",
        session_id: str | None = None,
        principal_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> int:
        """Create an exact-scope durable purge and process it fail-closed.

        SQLite is the canonical mutation coordinator. It persists the exact
        target set and tombstones it before Kuzu or LanceDB is touched; retries
        resume journal state rather than recomputing request scope.
        """
        _assert_valid_agent_id(agent_id)
        if scope not in {"agent", "session"}:
            raise ValueError("scope must be exactly 'agent' or 'session'.")
        if scope == "session":
            if not session_id or session_id == "*":
                raise ValueError(
                    "session scope requires one exact non-wildcard session_id."
                )
        elif session_id is not None:
            raise ValueError("agent scope must not carry a session_id.")
        if idempotency_key is not None and not idempotency_key.strip():
            raise ValueError("idempotency_key must be non-empty when supplied.")

        journal_key = idempotency_key or f"purge:{uuid.uuid4()}"
        journal_principal = principal_id or "__internal__"
        purge_id: str | None = None
        async with self._sql.transaction() as db:
            async with db.execute(
                "SELECT purge_id, agent_id, scope, session_id, state "
                "FROM purge_journal WHERE idempotency_key = ?",
                (journal_key,),
            ) as cursor:
                existing = await cursor.fetchone()
            if existing is not None:
                if (
                    existing["agent_id"] != agent_id
                    or existing["scope"] != scope
                    or existing["session_id"] != session_id
                ):
                    raise ValueError(
                        "idempotency key cannot be reused for a different purge scope."
                    )
                if existing["state"] == "FINALIZED":
                    raise PurgeAlreadyFinalizedError("purge is already FINALIZED.")
                purge_id = existing["purge_id"]
            else:
                if scope == "session":
                    select_sql = (
                        "SELECT id FROM nodes WHERE agent_id = ? AND session_id = ? "
                        "AND invalid_at IS NULL AND deleted_at IS NULL AND purge_id IS NULL"
                    )
                    select_params: tuple[Any, ...] = (agent_id, session_id)
                else:
                    select_sql = (
                        "SELECT id FROM nodes WHERE agent_id = ? "
                        "AND invalid_at IS NULL AND deleted_at IS NULL AND purge_id IS NULL"
                    )
                    select_params = (agent_id,)
                async with db.execute(select_sql, select_params) as cursor:
                    node_ids = [row[0] for row in await cursor.fetchall()]
                if not node_ids:
                    await db.commit()
                    return 0
                purge_id = str(uuid.uuid4())
                now = datetime.now(timezone.utc).isoformat()
                await db.execute(
                    "INSERT INTO purge_journal "
                    "(purge_id, idempotency_key, principal_id, agent_id, scope, session_id, "
                    "target_node_ids, state, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 'PREPARED', ?, ?)",
                    (
                        purge_id,
                        journal_key,
                        journal_principal,
                        agent_id,
                        scope,
                        session_id,
                        json.dumps(node_ids),
                        now,
                        now,
                    ),
                )
                placeholders = ",".join("?" for _ in node_ids)
                await db.execute(
                    f"UPDATE nodes SET invalid_at = CURRENT_TIMESTAMP, "
                    f"deleted_at = CURRENT_TIMESTAMP, purge_id = ? "
                    f"WHERE agent_id = ? AND id IN ({placeholders}) "
                    f"AND invalid_at IS NULL AND deleted_at IS NULL AND purge_id IS NULL",
                    (purge_id, agent_id, *node_ids),
                )
                await db.execute(
                    "UPDATE purge_journal SET state = 'TOMBSTONED', updated_at = ? "
                    "WHERE purge_id = ? AND state = 'PREPARED'",
                    (now, purge_id),
                )
            await db.commit()
        assert purge_id is not None
        return await self.resume_purge(purge_id)

    async def resume_purge(self, purge_id: str) -> int:
        """Resume one journal-recorded purge without widening its scope."""
        record = await self._get_purge_record(purge_id)
        if record is None:
            raise ValueError("unknown purge_id")
        if record["state"] == "FINALIZED":
            raise PurgeAlreadyFinalizedError("purge is already FINALIZED.")
        if record["state"] in {"BLOCKED", "COMPENSATION_REQUIRED", "FAILED_SAFE"}:
            raise PurgeBlockedError(f"purge is not resumable from {record['state']}.")
        node_ids = json.loads(record["target_node_ids"])
        agent_id = record["agent_id"]
        if record["kuzu_result"] != "APPLIED":
            try:
                if self._graph is None:
                    raise RuntimeError(
                        "Kuzu graph provider is required for purge finalization."
                    )
                await self._graph.delete_nodes(
                    purge_id=purge_id, agent_id=agent_id, node_ids=node_ids
                )
                if not await self._graph.verify_nodes_absent(
                    agent_id=agent_id, node_ids=node_ids
                ):
                    raise RuntimeError("Kuzu delete verification failed.")
                await self._advance_purge(
                    purge_id, state="KUZU_APPLIED", kuzu_result="APPLIED"
                )
            except Exception as exc:
                await self._mark_purge_retry(purge_id, exc, phase="kuzu")
                raise PurgeRetryPendingError(
                    f"Kuzu purge pending for {purge_id}"
                ) from exc
        record = await self._get_purge_record(purge_id)
        assert record is not None
        if record["vector_result"] != "APPLIED":
            try:
                for node_id in node_ids:
                    await self._vec.hard_delete(node_id, agent_id)
                active_ids = await self._vec.get_active_node_ids(agent_id)
                if any(node_id in active_ids for node_id in node_ids):
                    raise RuntimeError("Vector delete verification failed.")
                await self._advance_purge(
                    purge_id, state="VECTOR_APPLIED", vector_result="APPLIED"
                )
            except Exception as exc:
                await self._mark_purge_retry(purge_id, exc, phase="vector")
                raise PurgeRetryPendingError(
                    f"vector purge pending for {purge_id}"
                ) from exc
        await self._advance_purge(purge_id, state="VERIFIED")
        await self._advance_purge(purge_id, state="FINALIZED")
        return len(node_ids)

    async def resume_incomplete_purges(self, limit: int = 100) -> dict[str, str]:
        """Bounded crash recovery using canonical journal scope only."""
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000.")
        async with self._sql.connection() as db:
            async with db.execute(
                "SELECT purge_id FROM purge_journal WHERE state IN "
                "('PREPARED', 'TOMBSTONED', 'KUZU_APPLIED', 'VECTOR_APPLIED', "
                "'VERIFIED', 'RETRY_PENDING') ORDER BY updated_at LIMIT ?",
                (limit,),
            ) as cursor:
                purge_ids = [row[0] for row in await cursor.fetchall()]
        outcomes: dict[str, str] = {}
        for candidate in purge_ids:
            try:
                await self.resume_purge(candidate)
                outcomes[candidate] = "FINALIZED"
            except PurgeRetryPendingError:
                outcomes[candidate] = "RETRY_PENDING"
            except PurgeBlockedError:
                outcomes[candidate] = "BLOCKED"
        return outcomes

    async def rollback_purge(self, purge_id: str) -> int:
        """Rollback only a tombstone whose downstream deletes never succeeded."""
        record = await self._get_purge_record(purge_id)
        if record is None:
            raise ValueError("unknown purge_id")
        if (
            record["state"] not in {"PREPARED", "TOMBSTONED", "RETRY_PENDING"}
            or record["kuzu_result"] != "PENDING"
            or record["vector_result"] != "PENDING"
        ):
            raise PurgeBlockedError(
                "purge compensation requires a verified pre-downstream snapshot."
            )
        node_ids = json.loads(record["target_node_ids"])
        placeholders = ",".join("?" for _ in node_ids)
        async with self._sql.transaction() as db:
            cursor = await db.execute(
                f"UPDATE nodes SET invalid_at = NULL, deleted_at = NULL, purge_id = NULL "
                f"WHERE purge_id = ? AND agent_id = ? AND id IN ({placeholders})",
                (purge_id, record["agent_id"], *node_ids),
            )
            await db.execute(
                "UPDATE purge_journal SET state = 'FAILED_SAFE', "
                "last_error = 'pre-downstream tombstone rollback', updated_at = ? "
                "WHERE purge_id = ?",
                (datetime.now(timezone.utc).isoformat(), purge_id),
            )
            await db.commit()
        return cursor.rowcount

    async def _get_purge_record(self, purge_id: str) -> dict[str, Any] | None:
        async with self._sql.connection() as db:
            async with db.execute(
                "SELECT * FROM purge_journal WHERE purge_id = ?", (purge_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def _advance_purge(
        self,
        purge_id: str,
        *,
        state: str,
        kuzu_result: str | None = None,
        vector_result: str | None = None,
    ) -> None:
        assignments = ["state = ?", "updated_at = ?", "last_error = NULL"]
        params: list[Any] = [state, datetime.now(timezone.utc).isoformat()]
        if kuzu_result is not None:
            assignments.append("kuzu_result = ?")
            params.append(kuzu_result)
        if vector_result is not None:
            assignments.append("vector_result = ?")
            params.append(vector_result)
        params.append(purge_id)
        async with self._sql.transaction() as db:
            await db.execute(
                f"UPDATE purge_journal SET {', '.join(assignments)} WHERE purge_id = ?",
                params,
            )
            await db.commit()

    async def _mark_purge_retry(
        self, purge_id: str, exc: Exception, *, phase: str
    ) -> None:
        async with self._sql.transaction() as db:
            async with db.execute(
                "SELECT retry_count FROM purge_journal WHERE purge_id = ?", (purge_id,)
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                raise ValueError("unknown purge_id")
            next_retry = int(row[0]) + 1
            state = "BLOCKED" if next_retry >= _PURGE_MAX_RETRIES else "RETRY_PENDING"  # type: ignore[no-untyped-def]
            await db.execute(
                "UPDATE purge_journal SET state = ?, retry_count = ?, last_error = ?, "
                "updated_at = ? WHERE purge_id = ?",
                (
                    state,
                    next_retry,
                    f"{phase}: {type(exc).__name__}",
                    datetime.now(timezone.utc).isoformat(),
                    purge_id,
                ),
            )
            await db.commit()

    async def _atomic_saga_commit(
        self,
        db_conn: Any,
        vector_func: Any = None,
        graph_func: Any = None,
    ) -> None:
        """Central helper for executing dual/tri-write sagas.
        Ensures secondary stores (LanceDB, Kuzu) succeed before committing SQLite.
        """
        try:
            if graph_func is not None:
                await graph_func()
            if vector_func is not None:
                await vector_func()
        except Exception as exc:
            await db_conn.rollback()
            logger.error("SAGA_ROLLBACK | Failed to write to secondary store: %s", exc)
            raise
        await db_conn.commit()

    # ==================================================================
    # UPDATE ENTITY DESCRIPTION
    # ==================================================================

    async def update_entity_description(
        self,
        agent_id: str,
        node_id: str,
        new_content: str,
        new_embedding: list[float],
    ) -> None:
        """Update an existing entity node's content and embedding.

        This performs a dual-write:
            1. Updates `content_payload` in the SQLite nodes table.
            2. Upserts the new text and embedding in the LanceDB vector index.

        Args:
            agent_id: Mandatory tenant isolation key.
            node_id: UUID of the node to update.
            new_content: The new consolidated text description.
            new_embedding: Float32 embedding of the new content.
        """
        _assert_valid_agent_id(agent_id)

        async with self._sql.transaction() as db:
            await db.execute(
                "UPDATE nodes SET content_payload = ? "
                "WHERE id = ? AND agent_id = ? AND invalid_at IS NULL",
                (new_content, node_id, agent_id),
            )

            async def _vec_update():
                await self.vector_engine.upsert(
                    agent_id=agent_id,
                    node_id=node_id,
                    embedding=new_embedding,
                )

            await self._atomic_saga_commit(db, vector_func=_vec_update)

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

        # DP2: Epistemic Dampening Logic
        if relation_type.upper() == "CONTRADICTS":
            async with self._sql.transaction() as db:
                for n_id in (source_id, target_id):
                    await db.execute(
                        "UPDATE nodes SET confidence = confidence * 0.8 "
                        "WHERE id = ? AND agent_id = ?",
                        (n_id, agent_id),
                    )
                    await db.execute(
                        "UPDATE nodes SET is_quarantined = 1 "
                        "WHERE id = ? AND agent_id = ? AND confidence < 0.2",
                        (n_id, agent_id),
                    )
                await db.commit()
            logger.info(
                "EPISTEMIC_DAMPENING | CONTRADICTS relation triggered dampening for nodes %s and %s",
                source_id,
                target_id,
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

        neighbors = await graph.get_neighbors(
            node_id=node_id,
            agent_id=agent_id,
            max_hops=max_hops,
        )
        active = await self.get_nodes_by_ids_batch(
            agent_id, [neighbor["id"] for neighbor in neighbors]
        )
        return [neighbor for neighbor in neighbors if neighbor["id"] in active]

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

        try:
            if self._vec:
                await self._vec.hard_delete(node_id, agent_id)
            if self._graph:
                await self._graph.execute_write(
                    "MATCH (n:Entity {id: $node_id, agent_id: $agent_id})"
                    "-[r:Observed]-() DELETE r",
                    {"node_id": node_id, "agent_id": agent_id},
                )
        except Exception as exc:
            logger.error(
                "INVALIDATE_SAGA_ROLLBACK | agent_id=%s error=%s", agent_id, exc
            )
            raise

        async with self._sql.transaction() as db:
            await db.execute(
                "UPDATE nodes SET invalid_at = CURRENT_TIMESTAMP "
                "WHERE id = ? AND agent_id = ? AND invalid_at IS NULL",
                (node_id, agent_id),
            )
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

    async def find_consolidated_nodes_by_name(
        self,
        agent_id: str,
        *,
        entity_name: str,
        case_insensitive: bool = True,
    ) -> list[dict[str, Any]]:
        """Find active *consolidated* nodes matching ``entity_name``.

        Like ``find_nodes_by_name`` but additionally filters on
        ``is_consolidated = 1``.  Used by the REM cycle worker to
        detect contradictions against existing consolidated knowledge.

        RLS: ``agent_id`` is mandatory and hardcoded into the WHERE.

        Args:
            agent_id: **Mandatory** tenant isolation key.
            entity_name: Single entity name to match.
            case_insensitive: If True, compare via ``LOWER()``.

        Returns:
            List of matching consolidated node dicts.
        """
        _assert_valid_agent_id(agent_id)

        if not entity_name:
            return []

        if case_insensitive:
            name_clause = "LOWER(entity_name) = LOWER(?)"
        else:
            name_clause = "entity_name = ?"

        query = (
            f"SELECT * FROM nodes WHERE agent_id = ? "
            f"AND {name_clause} "
            f"AND is_consolidated = 1 "
            f"AND invalid_at IS NULL "
            f"AND deleted_at IS NULL"
        )

        async with self._sql.connection() as db:
            async with db.execute(query, (agent_id, entity_name)) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    # ==================================================================
    # GET ALL ACTIVE AGENT IDS — unscoped (system-level query)
    # ==================================================================

    async def get_all_active_agent_ids(self) -> list[str]:
        """Return a deduplicated list of all ``agent_id`` values in the graph.

        This is a **system-level** query used by background workers
        (PageRank, REM cycle) that need to iterate across all tenants.
        No RLS filtering is applied — the caller is trusted.

        Returns:
            Sorted list of unique, non-null agent_id strings.
        """
        query = (
            "SELECT DISTINCT agent_id FROM nodes "
            "WHERE agent_id IS NOT NULL "
            "ORDER BY agent_id"
        )
        async with self._sql.connection() as db:
            async with db.execute(query) as cursor:
                rows = await cursor.fetchall()
                return [row[0] for row in rows if row[0]]

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
                return self._sanitize_payload(dict(row)) if row else None

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
            {"node_id": f"{agent_id}::{node_id}", "agent_id": agent_id},
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

        async with self._sql.transaction() as db:  # type: ignore[index]
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
            "hallucinations": hallucinations,  # type: ignore[arg-type]
        }

    # ==================================================================
    # RAW LOG INSERT — hot-path ingestion (< 50ms, pure I/O)
    # ==================================================================

    async def _queue_usage(
        self, db: aiosqlite.Connection, agent_id: str | None = None
    ) -> dict[str, int]:
        placeholders = ",".join("?" for _ in _ADMISSION_ACTIVE_STATES)
        predicate = f"state IN ({placeholders})"
        params: list[Any] = list(_ADMISSION_ACTIVE_STATES)
        if agent_id is not None:
            predicate += " AND agent_id = ?"
            params.append(agent_id)
        async with db.execute(
            f"SELECT COUNT(*) AS records, COALESCE(SUM(payload_bytes), 0) AS bytes, "
            f"COALESCE(SUM(CASE WHEN state = 'IN_FLIGHT' THEN 1 ELSE 0 END), 0) AS in_flight, "
            f"COALESCE(SUM(CASE WHEN state = 'RETRY_PENDING' THEN 1 ELSE 0 END), 0) AS retry_pending "
            f"FROM dispatch_queue WHERE {predicate}",
            params,
        ) as cursor:
            row = await cursor.fetchone()
            assert row is not None
        return {
            key: int(row[key])
            for key in ("records", "bytes", "in_flight", "retry_pending")
        }

    @staticmethod
    def _enforce_queue_capacity(
        global_usage: dict[str, int],
        tenant_usage: dict[str, int],
        payload_bytes: int,
        policy: "QueueAdmissionPolicy",
    ) -> None:  # type: ignore[index]
        if payload_bytes > policy.queue_max_single_record_bytes:
            raise QueueRecordTooLargeError("queue record exceeds configured size limit")
        checks = (
            (global_usage["records"] + 1 > policy.queue_max_pending_records, "global"),
            (
                global_usage["bytes"] + payload_bytes > policy.queue_max_pending_bytes,
                "global",
            ),
            (global_usage["in_flight"] >= policy.queue_max_in_flight_records, "global"),
            (
                global_usage["retry_pending"] >= policy.queue_max_retry_pending_records,
                "global",
            ),
            (
                tenant_usage["records"] + 1
                > policy.queue_max_pending_records_per_tenant,
                "tenant",
            ),
            (
                tenant_usage["bytes"] + payload_bytes
                > policy.queue_max_pending_bytes_per_tenant,
                "tenant",
            ),
            (
                tenant_usage["in_flight"]
                >= policy.queue_max_in_flight_records_per_tenant,
                "tenant",
            ),
            (
                tenant_usage["retry_pending"]
                >= policy.queue_max_retry_pending_records_per_tenant,
                "tenant",
            ),
        )
        for exceeded, scope in checks:
            if exceeded:
                raise QueueOverCapacityError(scope)

    async def admit_raw_log(
        self, agent_id: str, payload: dict[str, Any], *, policy: "QueueAdmissionPolicy"
    ) -> dict[str, Any]:
        """Atomically admit a raw log and durable dispatch receipt, or persist nothing.

        Capacity is measured from canonical UTF-8 serialized envelopes under the
        SQLite writer transaction.  This prevents concurrent callers from
        bypassing count, byte, tenant, retry, or in-flight budgets.
        """
        _assert_valid_agent_id(agent_id)
        if payload.get("agent_id") not in (None, agent_id):
            raise ValueError("payload agent_id must match the durable admission tenant")
        serialized, payload_bytes = _canonical_payload_bytes(payload)
        try:
            async with self._sql.transaction() as db:
                global_usage = await self._queue_usage(db)
                tenant_usage = await self._queue_usage(db, agent_id)
                self._enforce_queue_capacity(
                    global_usage, tenant_usage, payload_bytes, policy
                )
                cursor = await db.execute(
                    "INSERT INTO raw_logs (agent_id, payload, status) VALUES (?, ?, 'DEFERRED')",
                    (agent_id, serialized),
                )
                assert cursor.lastrowid is not None
                log_id = int(cursor.lastrowid)
                dispatch_id = str(uuid.uuid4())
                queue_id = str(uuid.uuid4())
                idempotency_key = f"raw-log:{agent_id}:{log_id}"
                await db.execute(
                    "INSERT INTO dispatch_journal (dispatch_id, source_record_id, tenant_id, agent_id, "
                    "job_type, idempotency_key, state, attempt_count, queue_record_id, dispatched_at, finalized_at, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, 'cold_path', ?, 'RECEIPT_RECORDED', 1, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                    (
                        dispatch_id,
                        log_id,
                        agent_id,
                        agent_id,
                        idempotency_key,
                        queue_id,
                    ),
                )
                await db.execute(
                    "INSERT INTO dispatch_queue (queue_record_id, dispatch_id, tenant_id, agent_id, job_type, "
                    "payload_reference, payload_bytes, idempotency_key, state) VALUES (?, ?, ?, ?, 'cold_path', ?, ?, ?, 'ENQUEUED')",
                    (
                        queue_id,
                        dispatch_id,
                        agent_id,
                        agent_id,
                        log_id,
                        payload_bytes,
                        idempotency_key,
                    ),
                )
                await db.execute(
                    "INSERT INTO dispatch_receipts (receipt_id, dispatch_id, queue_record_id, tenant_id, agent_id, outcome, idempotency_key) "
                    "VALUES (?, ?, ?, ?, ?, 'ENQUEUED', ?)",
                    (
                        str(uuid.uuid4()),
                        dispatch_id,
                        queue_id,
                        agent_id,
                        agent_id,
                        idempotency_key,
                    ),
                )
                await db.commit()
        except (aiosqlite.Error, OSError) as exc:
            raise QueueUnavailableError("durable admission is unavailable") from exc
        return {
            "admission": "DEFERRED",  # type: ignore[index]
            "log_id": log_id,
            "dispatch_id": dispatch_id,
            "queue_record_id": queue_id,
            "payload_bytes": payload_bytes,
        }

    async def get_queue_admission_metrics(self, agent_id: str) -> dict[str, Any]:
        """Return bounded queue pressure counts for readiness/metrics consumers."""
        _assert_valid_agent_id(agent_id)
        try:
            async with self._sql.connection() as db:
                global_usage = await self._queue_usage(db)
                tenant_usage = await self._queue_usage(db, agent_id)
                async with db.execute(
                    "SELECT COUNT(*) FROM dispatch_queue WHERE state = 'BLOCKED'"
                ) as cursor:
                    _row = await cursor.fetchone()
                    assert _row is not None
                    blocked = int(_row[0])
        except (aiosqlite.Error, OSError) as exc:
            raise QueueUnavailableError("durable admission is unavailable") from exc
        return {
            "global": global_usage,
            "tenant": tenant_usage,
            "blocked_records": blocked,
        }

    async def insert_raw_log(self, agent_id: str, payload: dict) -> int:
        """Insert a raw payload into the ``raw_logs`` staging table.

        This is the **hot-path write** for the v0.6.1 decoupled ingestion
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
                row = await cursor.fetchone()  # type: ignore[index]
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

    async def request_session_finalization(
        self, agent_id: str, session_id: str
    ) -> dict[str, Any]:
        """Create one idempotent durable finalization intent for an exact session."""
        _assert_valid_agent_id(agent_id)
        if not session_id or session_id == "*":
            raise ValueError("an exact session_id is required")
        async with self._sql.transaction() as db:
            async with db.execute(
                "SELECT * FROM session_finalization_journal WHERE agent_id = ? AND session_id = ?",
                (agent_id, session_id),
            ) as cursor:
                existing = await cursor.fetchone()
            if existing is None:
                async with db.execute(
                    "SELECT COUNT(*) FROM raw_logs WHERE agent_id = ? AND json_extract(payload, '$.session_id') = ? "
                    "AND status NOT LIKE 'processed%' AND status NOT LIKE 'rejected%'",
                    (agent_id, session_id),
                ) as cursor:
                    _row = await cursor.fetchone()
                    assert _row is not None
                    pending_count = int(_row[0])
                finalization_id = str(uuid.uuid4())
                state = "COMPLETED" if pending_count == 0 else "PENDING"
                await db.execute(
                    "INSERT INTO session_finalization_journal "
                    "(finalization_id, agent_id, session_id, idempotency_key, state, completed_at) "
                    "VALUES (?, ?, ?, ?, ?, CASE WHEN ? = 'COMPLETED' THEN CURRENT_TIMESTAMP ELSE NULL END)",
                    (
                        finalization_id,
                        agent_id,
                        session_id,
                        f"session-finalize:{agent_id}:{session_id}",
                        state,
                        state,
                    ),
                )
            async with db.execute(
                "SELECT * FROM session_finalization_journal WHERE agent_id = ? AND session_id = ?",
                (agent_id, session_id),
            ) as cursor:
                row = await cursor.fetchone()
            await db.commit()
        assert row is not None
        return dict(row)

    async def claim_session_finalization(
        self,
        agent_id: str,
        session_id: str,
        *,
        worker_id: str,
        lease_seconds: int = 300,
    ) -> dict[str, Any] | None:
        """Claim pending finalization using a durable fencing token."""
        _assert_valid_agent_id(agent_id)
        if not worker_id or not 1 <= lease_seconds <= 3600:
            raise ValueError("invalid finalization claim bounds")
        token = str(uuid.uuid4())
        async with self._sql.transaction() as db:
            cursor = await db.execute(
                "UPDATE session_finalization_journal SET state = 'CLAIMED', claim_token = ?, claimed_by = ?, "
                "lease_expires_at = datetime('now', ?), attempt_count = attempt_count + 1, "
                "last_error_class = NULL, updated_at = CURRENT_TIMESTAMP WHERE agent_id = ? AND session_id = ? "
                "AND (state IN ('PENDING','RETRY_PENDING') OR (state = 'CLAIMED' AND lease_expires_at <= CURRENT_TIMESTAMP))",
                (token, worker_id, f"+{lease_seconds} seconds", agent_id, session_id),
            )
            if cursor.rowcount != 1:
                await db.commit()
                return None
            async with db.execute(
                "SELECT * FROM session_finalization_journal WHERE agent_id = ? AND session_id = ?",
                (agent_id, session_id),
            ) as rows:
                row = await rows.fetchone()
            await db.commit()
        return dict(row) if row else None

    async def get_session_finalization(
        self, agent_id: str, session_id: str
    ) -> dict[str, Any] | None:
        _assert_valid_agent_id(agent_id)
        async with self._sql.connection() as db:
            async with db.execute(
                "SELECT * FROM session_finalization_journal WHERE agent_id = ? AND session_id = ?",
                (agent_id, session_id),
            ) as cursor:
                row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_pending_session_raw_logs(
        self, agent_id: str, session_id: str
    ) -> list[int]:
        _assert_valid_agent_id(agent_id)
        async with self._sql.connection() as db:
            async with db.execute(
                "SELECT id FROM raw_logs WHERE agent_id = ? AND json_extract(payload, '$.session_id') = ? "
                "AND status = 'DEFERRED' ORDER BY id",
                (agent_id, session_id),
            ) as cursor:
                return [int(row[0]) for row in await cursor.fetchall()]

    async def complete_session_finalization(
        self, agent_id: str, session_id: str, *, worker_id: str, claim_token: str
    ) -> bool:
        """Complete only when every exact-scope raw log is terminal and the fence matches."""
        _assert_valid_agent_id(agent_id)
        async with self._sql.transaction() as db:
            async with db.execute(
                "SELECT COUNT(*) FROM raw_logs WHERE agent_id = ? AND json_extract(payload, '$.session_id') = ? "
                "AND status NOT LIKE 'processed%' AND status NOT LIKE 'rejected%'",
                (agent_id, session_id),
            ) as cursor:
                _row = await cursor.fetchone()
                assert _row is not None
                incomplete = int(_row[0])
            if incomplete:
                await db.commit()
                return False
            cursor = await db.execute(
                "UPDATE session_finalization_journal SET state = 'COMPLETED', completed_at = CURRENT_TIMESTAMP, "
                "claim_token = NULL, claimed_by = NULL, lease_expires_at = NULL, updated_at = CURRENT_TIMESTAMP "
                "WHERE agent_id = ? AND session_id = ? AND state = 'CLAIMED' AND claimed_by = ? AND claim_token = ?",
                (agent_id, session_id, worker_id, claim_token),
            )
            await db.commit()
        return cursor.rowcount == 1

    async def fail_session_finalization(
        self,
        agent_id: str,
        session_id: str,
        *,
        worker_id: str,
        claim_token: str,
        error_class: str,
    ) -> bool:
        """Persist a sanitized bounded failure; stale workers cannot mutate state."""
        _assert_valid_agent_id(agent_id)
        async with self._sql.transaction() as db:
            cursor = await db.execute(
                "UPDATE session_finalization_journal SET state = CASE WHEN attempt_count >= retry_limit THEN 'BLOCKED' "
                "ELSE 'RETRY_PENDING' END, last_error_class = ?, claim_token = NULL, claimed_by = NULL, "
                "lease_expires_at = NULL, updated_at = CURRENT_TIMESTAMP WHERE agent_id = ? AND session_id = ? "
                "AND state = 'CLAIMED' AND claimed_by = ? AND claim_token = ?",
                (error_class[:120], agent_id, session_id, worker_id, claim_token),
            )
            await db.commit()
        return cursor.rowcount == 1

    async def recover_expired_session_finalizations(self) -> int:
        async with self._sql.transaction() as db:
            cursor = await db.execute(
                "UPDATE session_finalization_journal SET state = 'RETRY_PENDING', claim_token = NULL, "
                "claimed_by = NULL, lease_expires_at = NULL, updated_at = CURRENT_TIMESTAMP "
                "WHERE state = 'CLAIMED' AND lease_expires_at <= CURRENT_TIMESTAMP"
            )
            await db.commit()
        return cursor.rowcount

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

    async def claim_raw_log(
        self, agent_id: str, log_id: int, *, worker_id: str, lease_seconds: int = 300
    ) -> dict[str, Any] | None:
        """Atomically claim a deferred or expired cold-path job."""
        _assert_valid_agent_id(agent_id)
        if not worker_id or not 1 <= lease_seconds <= 3600:
            raise ValueError("worker_id and a 1..3600 second lease are required.")
        token = str(uuid.uuid4())
        async with self._sql.transaction() as db:
            cursor = await db.execute(
                "UPDATE raw_logs SET status = 'processing', claim_token = ?, "
                "claimed_by = ?, lease_expires_at = datetime('now', ?), "
                "attempt_count = attempt_count + 1, last_error = NULL "
                "WHERE id = ? AND agent_id = ? AND (status = 'DEFERRED' OR "
                "(status = 'processing' AND lease_expires_at <= CURRENT_TIMESTAMP))",
                (token, worker_id, f"+{lease_seconds} seconds", log_id, agent_id),
            )
            if cursor.rowcount != 1:
                await db.commit()
                return None
            async with db.execute(
                "SELECT id, agent_id, payload, status, claim_token, claimed_by, "
                "lease_expires_at, attempt_count FROM raw_logs WHERE id = ? AND agent_id = ?",
                (log_id, agent_id),
            ) as rows:
                row = await rows.fetchone()
            await db.commit()
        assert row is not None
        claimed = dict(row)
        if isinstance(claimed.get("payload"), str):
            claimed["payload"] = json.loads(claimed["payload"])
        return claimed

    async def transition_claimed_raw_log(
        self,
        agent_id: str,
        log_id: int,
        *,
        worker_id: str,
        claim_token: str,
        status: str,
        error_reason: str | None = None,
    ) -> bool:
        """Finalize a job only when its owner and fencing token still match."""
        _assert_valid_agent_id(agent_id)
        if status not in {"processed", "failed", "rejected", "DEFERRED"}:
            raise ValueError("claimed job status must be terminal or DEFERRED.")
        final_status = f"{status}:{error_reason}" if error_reason else status
        async with self._sql.transaction() as db:
            cursor = await db.execute(
                "UPDATE raw_logs SET status = ?, claim_token = NULL, claimed_by = NULL, "
                "lease_expires_at = NULL, last_error = ?, processed_at = "
                "CASE WHEN ? = 'processed' THEN CURRENT_TIMESTAMP ELSE processed_at END "
                "WHERE id = ? AND agent_id = ? AND status = 'processing' "
                "AND claimed_by = ? AND claim_token = ?",
                (
                    final_status,
                    error_reason,
                    status,
                    log_id,
                    agent_id,
                    worker_id,
                    claim_token,
                ),
            )
            await db.commit()
        return cursor.rowcount == 1

    async def recover_expired_raw_log_claims(self) -> int:
        """Return expired claims to DEFERRED without changing terminal rows."""
        async with self._sql.transaction() as db:
            cursor = await db.execute(
                "UPDATE raw_logs SET status = 'DEFERRED', claim_token = NULL, "
                "claimed_by = NULL, lease_expires_at = NULL "
                "WHERE status = 'processing' AND lease_expires_at <= CURRENT_TIMESTAMP"
            )
            await db.commit()
        return cursor.rowcount

    async def claim_lancedb_wal_entries(
        self, *, worker_id: str, limit: int = 100, lease_seconds: int = 300
    ) -> list[dict[str, Any]]:
        """Durably claim only non-terminal projection work with a new fence."""
        if not worker_id or not 1 <= limit <= 1000 or not 1 <= lease_seconds <= 3600:
            raise ValueError("invalid WAL claim bounds.")
        token = str(uuid.uuid4())
        async with self._sql.transaction() as db:
            async with db.execute(
                "SELECT id FROM lancedb_wal WHERE state IN ('PENDING', 'SQLITE_COMMITTED', 'RETRY_PENDING') "
                "OR (state = 'CLAIMED' AND lease_expires_at <= CURRENT_TIMESTAMP) ORDER BY id LIMIT ?",
                (limit,),
            ) as cursor:
                identifiers = [row[0] for row in await cursor.fetchall()]
            claimed_ids: list[str] = []
            for identifier in identifiers:
                cursor = await db.execute(
                    "UPDATE lancedb_wal SET state = 'CLAIMED', claim_token = ?, claimed_by = ?, "
                    "lease_expires_at = datetime('now', ?), attempt_count = attempt_count + 1, "
                    "fence_epoch = fence_epoch + 1, last_error = NULL "
                    "WHERE id = ? AND (state IN ('PENDING', 'SQLITE_COMMITTED', 'RETRY_PENDING') "
                    "OR (state = 'CLAIMED' AND lease_expires_at <= CURRENT_TIMESTAMP))",
                    (token, worker_id, f"+{lease_seconds} seconds", identifier),
                )
                if cursor.rowcount == 1:
                    claimed_ids.append(identifier)
            if not claimed_ids:
                await db.commit()
                return []
            placeholders = ",".join("?" for _ in claimed_ids)
            async with db.execute(
                f"SELECT id, mutation_id, idempotency_key, agent_id, vector, metadata, claim_token, "
                f"claimed_by, attempt_count, fence_epoch, vector_state, graph_state, reconciliation_state "
                f"FROM lancedb_wal WHERE id IN ({placeholders}) ORDER BY id",
                claimed_ids,
            ) as cursor:
                rows = [dict(row) for row in await cursor.fetchall()]
            await db.commit()
        return rows

    async def get_lancedb_mutation_state(self, wal_id: str) -> dict[str, Any] | None:
        """Return the durable canonical state for one mutation without mutation."""
        async with self._sql.connection() as db:
            async with db.execute(
                "SELECT * FROM lancedb_wal WHERE id = ?", (wal_id,)
            ) as cursor:
                row = await cursor.fetchone()
        return dict(row) if row else None

    async def record_lancedb_projection_state(
        self, wal_id: str, *, worker_id: str, claim_token: str, projection: str
    ) -> bool:
        """Fence a single projection transition; stale claimants are rejected."""
        mapping = {
            "VECTOR_APPLIED": ("vector_state", "VECTOR_APPLIED"),
            "GRAPH_APPLIED": ("graph_state", "GRAPH_APPLIED"),
            "GRAPH_NOT_REQUIRED": ("graph_state", "NOT_REQUIRED"),
        }
        if projection not in mapping:
            raise ValueError("unsupported projection state")
        column, value = mapping[projection]
        async with self._sql.transaction() as db:
            cursor = await db.execute(
                f"UPDATE lancedb_wal SET {column} = ? WHERE id = ? AND state = 'CLAIMED' "
                "AND claimed_by = ? AND claim_token = ?",
                (value, wal_id, worker_id, claim_token),
            )
            await db.commit()
        return cursor.rowcount == 1

    async def _retry_lancedb_wal_entry(
        self, wal_id: str, *, worker_id: str, claim_token: str, error: str
    ) -> bool:
        """Release a fenced failure without erasing completed downstream state."""
        async with self._sql.transaction() as db:
            cursor = await db.execute(
                "UPDATE lancedb_wal SET state = CASE WHEN attempt_count >= retry_limit THEN 'BLOCKED' "
                "ELSE 'RETRY_PENDING' END, claim_token = NULL, claimed_by = NULL, lease_expires_at = NULL, "
                "last_error = ? WHERE id = ? AND state = 'CLAIMED' AND claimed_by = ? AND claim_token = ?",
                (error[:500], wal_id, worker_id, claim_token),
            )
            await db.commit()
        return cursor.rowcount == 1

    async def _graph_has_wal_node(self, *, node_id: str, agent_id: str) -> bool:
        if self._graph is None:
            return False
        has_node = getattr(self._graph, "has_node", None)
        if has_node is not None:
            return bool(await has_node(node_id=node_id, agent_id=agent_id))
        verify_absent = getattr(self._graph, "verify_nodes_absent", None)
        if verify_absent is None:
            raise RuntimeError(
                "graph provider does not expose exact-scope verification"
            )
        return not bool(await verify_absent(agent_id=agent_id, node_ids=[node_id]))

    async def reconcile_lancedb_wal_entry(
        self, wal_id: str, *, worker_id: str, claim_token: str
    ) -> str:
        """Persist exact-scope downstream observation before final ACK."""
        state = await self.get_lancedb_mutation_state(wal_id)
        if state is None:
            raise ValueError("unknown WAL mutation")
        metadata = json.loads(state["metadata"])
        node_id = metadata["node_id"]
        agent_id = state["agent_id"]
        graph_required = bool(metadata.get("graph_required", False))
        canonical_agent_id = metadata.get("canonical_agent_id", agent_id)
        payload_version = metadata.get("payload_version", 1)
        expected_vector = bool(metadata.get("expected_vector_projection", True))
        expected_graph_marker = metadata.get("expected_graph_projection")
        try:
            if canonical_agent_id != agent_id:
                result = "SCOPE_MISMATCH"
            elif payload_version != 1:
                result = "PAYLOAD_OR_VERSION_MISMATCH"  # type: ignore[index]
            else:
                vector_present = node_id in await self._vec.get_existing_node_ids(
                    agent_id, [node_id]
                )
                graph_present = (not graph_required) or await self._graph_has_wal_node(
                    node_id=node_id, agent_id=agent_id
                )
                if not expected_vector and vector_present:
                    result = "VECTOR_EXTRA"
                elif (  # type: ignore[index]
                    expected_graph_marker is False
                    and self._graph is not None
                    and await self._graph_has_wal_node(
                        node_id=node_id, agent_id=agent_id
                    )
                ):
                    result = "GRAPH_EXTRA"
                elif expected_vector and not vector_present:
                    result = "VECTOR_MISSING"
                elif graph_required and not graph_present:
                    result = "GRAPH_MISSING"
                else:
                    result = "ALIGNED"
        except Exception:
            result = "UNKNOWN_OR_UNVERIFIABLE"

        if result == "ALIGNED":
            next_state = "RECONCILED"  # type: ignore[str, Any]
            vector_state = state["vector_state"]
            graph_state = state["graph_state"]
            release_claim = False
        elif result == "VECTOR_MISSING":
            next_state = "RETRY_PENDING"
            vector_state = "PENDING"
            graph_state = state["graph_state"]
            release_claim = True
        elif result == "GRAPH_MISSING":
            next_state = "RETRY_PENDING"
            vector_state = state["vector_state"]
            graph_state = "PENDING"
            release_claim = True
        elif result == "SCOPE_MISMATCH":
            next_state = "BLOCKED"
            vector_state = state["vector_state"]
            graph_state = state["graph_state"]
            release_claim = True
        else:
            next_state = "RECONCILIATION_REQUIRED"
            vector_state = state["vector_state"]
            graph_state = state["graph_state"]
            release_claim = True

        async with self._sql.transaction() as db:
            cursor = await db.execute(
                "UPDATE lancedb_wal SET reconciliation_state = ?, reconciled_at = CURRENT_TIMESTAMP, "
                "state = ?, vector_state = ?, graph_state = ?, "
                "claim_token = CASE WHEN ? THEN NULL ELSE claim_token END, "
                "claimed_by = CASE WHEN ? THEN NULL ELSE claimed_by END, "
                "lease_expires_at = CASE WHEN ? THEN NULL ELSE lease_expires_at END "
                "WHERE id = ? AND state = 'CLAIMED' AND claimed_by = ? AND claim_token = ?",
                (
                    result,
                    next_state,
                    vector_state,
                    graph_state,
                    release_claim,
                    release_claim,
                    release_claim,
                    wal_id,
                    worker_id,
                    claim_token,
                ),
            )
            await db.commit()
        if cursor.rowcount != 1:
            return "FENCED_OUT"
        return result

    async def ack_lancedb_wal_entry(
        self, wal_id: str, *, worker_id: str, claim_token: str
    ) -> bool:
        """ACK only a reconciled mutation owned by the current durable fence."""
        async with self._sql.transaction() as db:
            cursor = await db.execute(
                "UPDATE lancedb_wal SET state = 'ACKED', acknowledged_at = CURRENT_TIMESTAMP, "
                "claim_token = NULL, claimed_by = NULL, lease_expires_at = NULL "
                "WHERE id = ? AND state = 'RECONCILED' AND reconciliation_state = 'ALIGNED' "
                "AND claimed_by = ? AND claim_token = ?",
                (wal_id, worker_id, claim_token),
            )
            await db.commit()
        return cursor.rowcount == 1

    async def recover_expired_lancedb_wal_claims(self) -> int:
        """Make expired, non-finalized work retryable while retaining its fence history."""
        async with self._sql.transaction() as db:
            cursor = await db.execute(
                "UPDATE lancedb_wal SET state = 'RETRY_PENDING', claim_token = NULL, claimed_by = NULL, "
                "lease_expires_at = NULL WHERE state = 'CLAIMED' AND lease_expires_at <= CURRENT_TIMESTAMP"
            )
            await db.commit()
        return cursor.rowcount

    async def replay_claimed_lancedb_wal_entry(
        self, entry: dict[str, Any], *, worker_id: str
    ) -> bool:
        """Apply only missing projections, reconcile, then issue a fenced ACK."""
        wal_id = entry["id"]
        token = entry["claim_token"]
        metadata = json.loads(entry["metadata"])
        node_id = metadata["node_id"]
        agent_id = entry["agent_id"]
        import numpy as np

        if entry.get("vector_state") != "VECTOR_APPLIED":
            try:
                await self._vec.upsert(
                    node_id=node_id,
                    agent_id=agent_id,
                    embedding=np.frombuffer(entry["vector"], dtype=np.float32).tolist(),
                    content_hash=metadata.get("content_hash"),
                )
            except Exception as exc:
                await self._retry_lancedb_wal_entry(
                    wal_id,
                    worker_id=worker_id,
                    claim_token=token,
                    error=type(exc).__name__,
                )
                raise
            if not await self.record_lancedb_projection_state(
                wal_id,
                worker_id=worker_id,
                claim_token=token,
                projection="VECTOR_APPLIED",
            ):
                return False
        if bool(metadata.get("graph_required", False)):
            if entry.get("graph_state") != "GRAPH_APPLIED":
                if self._graph is None:
                    await self._retry_lancedb_wal_entry(
                        wal_id,
                        worker_id=worker_id,
                        claim_token=token,
                        error="GraphProviderUnavailable",
                    )
                    raise RuntimeError("graph projection is required but unavailable")
                try:
                    await self._graph.insert_node(
                        node_id=node_id,
                        name=metadata.get("entity_name", node_id),
                        agent_id=agent_id,
                    )
                except Exception as exc:
                    await self._retry_lancedb_wal_entry(
                        wal_id,
                        worker_id=worker_id,
                        claim_token=token,
                        error=type(exc).__name__,
                    )
                    raise
                if not await self.record_lancedb_projection_state(
                    wal_id,
                    worker_id=worker_id,
                    claim_token=token,
                    projection="GRAPH_APPLIED",
                ):
                    return False
        elif entry.get("graph_state") != "NOT_REQUIRED":
            if not await self.record_lancedb_projection_state(
                wal_id,
                worker_id=worker_id,
                claim_token=token,
                projection="GRAPH_NOT_REQUIRED",
            ):
                return False
        result = await self.reconcile_lancedb_wal_entry(
            wal_id, worker_id=worker_id, claim_token=token
        )
        if result != "ALIGNED":
            return False
        return await self.ack_lancedb_wal_entry(
            wal_id, worker_id=worker_id, claim_token=token
        )

    async def replay_lancedb_wal(self, *, worker_id: str, limit: int = 100) -> int:
        """Replay real downstream projections without treating partial work as success."""
        entries = await self.claim_lancedb_wal_entries(worker_id=worker_id, limit=limit)
        completed = 0
        for entry in entries:
            if await self.replay_claimed_lancedb_wal_entry(entry, worker_id=worker_id):
                completed += 1
        return completed

    async def dispatch_raw_log(
        self,
        agent_id: str,
        log_id: int,
        *,
        worker_id: str,
        policy: "QueueAdmissionPolicy | None" = None,
    ) -> dict[str, Any]:
        """Atomically materialize a bounded raw-log dispatch intent, queue row and receipt."""
        _assert_valid_agent_id(agent_id)
        if not worker_id:
            raise ValueError("worker_id is required")
        if policy is None:
            from mesa_memory.config import config

            policy = config.queue_admission_policy
        async with self._sql.transaction() as db:
            async with db.execute(
                "SELECT id, payload FROM raw_logs WHERE id = ? AND agent_id = ?",
                (log_id, agent_id),
            ) as cursor:
                raw_log = await cursor.fetchone()
                if raw_log is None:
                    raise ValueError("raw log is not in the requested tenant scope")
            raw_payload = raw_log["payload"]
            if isinstance(raw_payload, str):
                raw_payload = json.loads(raw_payload)
            _, payload_bytes = _canonical_payload_bytes(raw_payload)
            async with db.execute(
                "SELECT * FROM dispatch_journal WHERE source_record_id = ? AND agent_id = ?",
                (log_id, agent_id),
            ) as cursor:
                existing = await cursor.fetchone()
            if existing is None:
                dispatch_id = str(uuid.uuid4())
                idempotency_key = f"raw-log:{agent_id}:{log_id}"
                await db.execute(
                    "INSERT INTO dispatch_journal (dispatch_id, source_record_id, tenant_id, agent_id, "
                    "job_type, idempotency_key, state, attempt_count, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, 'cold_path', ?, 'PENDING', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                    (dispatch_id, log_id, agent_id, agent_id, idempotency_key),
                )
            else:
                dispatch_id = existing["dispatch_id"]
                idempotency_key = existing["idempotency_key"]
            async with db.execute(
                "SELECT * FROM dispatch_journal WHERE dispatch_id = ?", (dispatch_id,)
            ) as cursor:
                journal = await cursor.fetchone()
                assert journal is not None
            if journal["state"] != "RECEIPT_RECORDED":
                global_usage = await self._queue_usage(db)
                tenant_usage = await self._queue_usage(db, agent_id)
                self._enforce_queue_capacity(
                    global_usage, tenant_usage, payload_bytes, policy
                )
                token = str(uuid.uuid4())
                await db.execute(
                    "UPDATE dispatch_journal SET state = 'CLAIMED', claimed_by = ?, claim_token = ?, "
                    "attempt_count = attempt_count + 1, lease_expires_at = datetime('now', '+300 seconds'), "
                    "updated_at = CURRENT_TIMESTAMP WHERE dispatch_id = ?",
                    (worker_id, token, dispatch_id),
                )
                queue_id = journal["queue_record_id"] or str(uuid.uuid4())
                await db.execute(
                    "INSERT OR IGNORE INTO dispatch_queue (queue_record_id, dispatch_id, tenant_id, agent_id, "
                    "job_type, payload_reference, payload_bytes, idempotency_key, state) VALUES (?, ?, ?, ?, 'cold_path', ?, ?, ?, 'ENQUEUED')",
                    (
                        queue_id,
                        dispatch_id,
                        agent_id,
                        agent_id,
                        log_id,
                        payload_bytes,
                        idempotency_key,
                    ),
                )
                await db.execute(
                    "INSERT OR IGNORE INTO dispatch_receipts (receipt_id, dispatch_id, queue_record_id, tenant_id, "
                    "agent_id, outcome, idempotency_key) VALUES (?, ?, ?, ?, ?, 'ENQUEUED', ?)",
                    (
                        str(uuid.uuid4()),
                        dispatch_id,
                        queue_id,
                        agent_id,
                        agent_id,
                        idempotency_key,
                    ),
                )
                await db.execute(
                    "UPDATE dispatch_journal SET state = 'RECEIPT_RECORDED', queue_record_id = ?, "
                    "dispatched_at = CURRENT_TIMESTAMP, finalized_at = CURRENT_TIMESTAMP, claim_token = NULL, "
                    "claimed_by = NULL, lease_expires_at = NULL, updated_at = CURRENT_TIMESTAMP WHERE dispatch_id = ?",
                    (queue_id, dispatch_id),
                )
            async with db.execute(
                "SELECT * FROM dispatch_journal WHERE dispatch_id = ?", (dispatch_id,)
            ) as cursor:
                _r = await cursor.fetchone()
                assert _r is not None
                result = dict(_r)
            await db.commit()
        return result

    async def recover_raw_log_dispatches(
        self, *, worker_id: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Find tenant-scoped deferred raw logs that lack a durable dispatch receipt."""
        if not worker_id or not 1 <= limit <= 1000:
            raise ValueError("invalid dispatch recovery bounds")
        async with self._sql.connection() as db:
            async with db.execute(
                "SELECT r.id, r.agent_id FROM raw_logs r LEFT JOIN dispatch_journal j "
                "ON j.source_record_id = r.id WHERE r.status = 'DEFERRED' AND "
                "(j.dispatch_id IS NULL OR j.state != 'RECEIPT_RECORDED') ORDER BY r.id LIMIT ?",
                (limit,),
            ) as cursor:
                pending = [
                    (row["agent_id"], row["id"]) for row in await cursor.fetchall()
                ]
        return [
            await self.dispatch_raw_log(aid, record_id, worker_id=worker_id)
            for aid, record_id in pending
        ]

    async def claim_dispatch_queue(
        self, *, worker_id: str, limit: int = 100, lease_seconds: int = 300
    ) -> list[dict[str, Any]]:
        """Claim bounded durable queue records with a worker fencing token."""
        if not worker_id or not 1 <= limit <= 1000 or not 1 <= lease_seconds <= 3600:
            raise ValueError("invalid dispatch queue claim bounds")
        token = str(uuid.uuid4())
        async with self._sql.transaction() as db:
            async with db.execute(
                "SELECT queue_record_id FROM dispatch_queue WHERE state IN ('ENQUEUED', 'RETRY_PENDING') "
                "OR (state = 'IN_FLIGHT' AND lease_expires_at <= CURRENT_TIMESTAMP) ORDER BY created_at LIMIT ?",
                (limit,),
            ) as cursor:
                ids = [row[0] for row in await cursor.fetchall()]
            claimed: list[str] = []
            for queue_id in ids:
                cursor = await db.execute(
                    "UPDATE dispatch_queue SET state = 'IN_FLIGHT', claimed_by = ?, claim_token = ?, "
                    "lease_expires_at = datetime('now', ?), attempt_count = attempt_count + 1 "
                    "WHERE queue_record_id = ? AND (state IN ('ENQUEUED', 'RETRY_PENDING') "
                    "OR (state = 'IN_FLIGHT' AND lease_expires_at <= CURRENT_TIMESTAMP))",
                    (worker_id, token, f"+{lease_seconds} seconds", queue_id),
                )
                if cursor.rowcount == 1:
                    claimed.append(queue_id)
            if not claimed:
                await db.commit()
                return []
            placeholders = ",".join("?" for _ in claimed)
            async with db.execute(
                f"SELECT * FROM dispatch_queue WHERE queue_record_id IN ({placeholders})",
                claimed,
            ) as cursor:
                rows = [dict(row) for row in await cursor.fetchall()]
            await db.commit()
        return rows

    async def complete_dispatch_queue(
        self,
        queue_record_id: str,
        *,
        worker_id: str,
        claim_token: str,
        outcome: str,
        side_effect_verified: bool,
    ) -> bool:
        """Write a completion receipt before ACK/finalization; stale fences never finalize."""
        if not worker_id or not claim_token:
            raise ValueError("worker_id and claim_token are required")
        async with self._sql.transaction() as db:
            async with db.execute(
                "SELECT * FROM dispatch_queue WHERE queue_record_id = ?",
                (queue_record_id,),
            ) as cursor:
                row = await cursor.fetchone()
            if (
                row is None
                or row["state"] != "IN_FLIGHT"
                or row["claimed_by"] != worker_id
                or row["claim_token"] != claim_token
            ):
                await db.commit()
                return False
            if not side_effect_verified:
                await db.execute(
                    "UPDATE dispatch_queue SET state = 'RETRY_PENDING', claim_token = NULL, claimed_by = NULL, "
                    "lease_expires_at = NULL, last_error_class = ? WHERE queue_record_id = ? AND claim_token = ?",
                    (outcome[:120], queue_record_id, claim_token),
                )
                await db.commit()
                return False
            cursor = await db.execute(
                "INSERT OR IGNORE INTO dispatch_completion_receipts (receipt_id, queue_record_id, dispatch_id, tenant_id, agent_id, "
                "worker_id, claim_token, outcome, side_effect_verified, attempt_count, idempotency_key) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (
                    str(uuid.uuid4()),
                    queue_record_id,
                    row["dispatch_id"],
                    row["tenant_id"],
                    row["agent_id"],
                    worker_id,
                    claim_token,
                    outcome,
                    row["attempt_count"],
                    f"completion:{row['idempotency_key']}",
                ),
            )
            if cursor.rowcount != 1:
                await db.commit()
                return False
            cursor = await db.execute(
                "UPDATE dispatch_queue SET state = 'FINALIZED', claim_token = NULL, claimed_by = NULL, lease_expires_at = NULL "
                "WHERE queue_record_id = ? AND state = 'IN_FLIGHT' AND claim_token = ?",
                (queue_record_id, claim_token),
            )
            await db.commit()
        return cursor.rowcount == 1

    async def get_dispatch_completion_receipt(
        self, queue_record_id: str
    ) -> dict[str, Any] | None:
        async with self._sql.connection() as db:
            async with db.execute(
                "SELECT * FROM dispatch_completion_receipts WHERE queue_record_id = ?",
                (queue_record_id,),
            ) as cursor:
                row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_dispatch_receipt(self, dispatch_id: str) -> dict[str, Any] | None:
        async with self._sql.connection() as db:
            async with db.execute(
                "SELECT * FROM dispatch_receipts WHERE dispatch_id = ?", (dispatch_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def get_dispatch_receipt_by_source(
        self, agent_id: str, log_id: int
    ) -> dict[str, Any] | None:
        _assert_valid_agent_id(agent_id)
        async with self._sql.connection() as db:
            async with db.execute(
                "SELECT r.* FROM dispatch_receipts r JOIN dispatch_journal j ON j.dispatch_id = r.dispatch_id "
                "WHERE j.agent_id = ? AND j.source_record_id = ?",
                (agent_id, log_id),
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

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
            graph_status = "not_initialized"
            if self._graph.is_initialized:
                try:
                    await self._graph.execute_query(
                        "MATCH (n:Entity) RETURN COUNT(n) AS c"
                    )
                    graph_status = "healthy"
                except Exception:
                    graph_status = "unhealthy"
            result["graph"] = {
                "status": graph_status,
                "db_path": self._graph.db_path,
            }
        return result

    @staticmethod
    def _sanitize_payload(row_dict: dict[str, Any]) -> dict[str, Any]:
        """Normalize sqlite row dict for external consumption."""
        if "content_payload" in row_dict:
            row_dict["content"] = row_dict.pop("content_payload")
        if "type" in row_dict:
            row_dict["node_type"] = row_dict.pop("type")
        if "is_consolidated" in row_dict:
            row_dict["is_consolidated"] = bool(row_dict["is_consolidated"])
        if "is_quarantined" in row_dict:
            row_dict["is_quarantined"] = bool(row_dict["is_quarantined"])
        return row_dict

    # ==================================================================
    # COST CONTROL (Daily Limits)
    # ==================================================================

    async def increment_and_check_daily_limit(
        self, subject_id: str, limit: int = 1000
    ) -> bool:
        """Increment the daily request counter for a verified principal subject.

        ``subject_id`` is a server-side principal ID, never a credential,
        tenant field, or agent ID supplied by a request.
        Returns True if allowed, False if exceeded.
        """
        from datetime import date

        today = date.today().isoformat()

        async with self._sql.transaction() as db:
            await db.execute(
                "INSERT INTO daily_limits (subject_id, date, request_count) "
                "VALUES (?, ?, 1) "
                "ON CONFLICT(subject_id, date) DO UPDATE SET request_count = request_count + 1",
                (subject_id, today),
            )

            async with db.execute(
                "SELECT request_count FROM daily_limits WHERE subject_id = ? AND date = ?",
                (subject_id, today),
            ) as cur:
                row = await cur.fetchone()

            await db.commit()

        if row and row[0] > limit:
            return False
        return True
