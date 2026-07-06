# MESA — Async-safe KùzuDB Graph Provider
# Bridges KùzuDB's synchronous C++ API into the async FastAPI event loop.
#
# Architecture:
#   - BaseGraphProvider ABC defines the async contract for graph storage
#   - KuzuGraphProvider wraps every synchronous kuzu.Connection call in
#     asyncio.run_in_executor to prevent event loop blocking
#   - Each provider instance owns its own kuzu.Connection for thread
#     safety (KùzuDB connections are NOT thread-safe)
#   - The global kuzu.Database handle is shared — only the Connection
#     is per-provider
#
# This mirrors the VectorEngine pattern already used in mesa_storage.
"""
Async-safe KùzuDB graph provider for the MESA knowledge graph.

Wraps KùzuDB's synchronous Python API in an executor-backed async
layer so that disk-bound C++ calls never block the ``asyncio`` event
loop.  Every provider instance creates its own ``kuzu.Connection``
from the shared ``kuzu.Database`` handle managed by the FastAPI
lifespan.

Usage::

    from mesa_storage.kuzu_provider import KuzuGraphProvider

    provider = KuzuGraphProvider(db_path="./storage/kuzu_db")
    await provider.initialize()

    rows = await provider.execute_query(
        "MATCH (e:Entity) WHERE e.agent_id = $agent_id RETURN e.*",
        {"agent_id": "agent_alpha"},
    )

    await provider.close()
"""

from __future__ import annotations

import abc
import asyncio
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import kuzu

logger = logging.getLogger("MESA_Storage")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_MAX_WORKERS = min(4, os.cpu_count() or 2)  # Cap to prevent over-subscription


# ---------------------------------------------------------------------------
# Abstract base class
# ---------------------------------------------------------------------------


class BaseGraphProvider(abc.ABC):
    """Async contract for graph storage backends.

    Any graph engine (NetworkX, KùzuDB, Neo4j, etc.) must implement
    this interface to be usable by the MESA storage layer.
    """

    @abc.abstractmethod
    async def initialize(self) -> None:
        """Prepare the provider for use (open connections, etc.)."""

    @abc.abstractmethod
    async def close(self) -> None:
        """Release resources and shut down gracefully."""

    @abc.abstractmethod
    async def execute_query(
        self,
        query: str,
        parameters: dict[str, Any] | None = None,
    ) -> list[list[Any]]:
        """Execute a Cypher query and return result rows."""

    @abc.abstractmethod
    async def execute_write(
        self,
        query: str,
        parameters: dict[str, Any] | None = None,
    ) -> None:
        """Execute a write-only Cypher statement (CREATE, MERGE, DELETE)."""

    @abc.abstractmethod
    async def insert_node(
        self,
        node_id: str,
        name: str,
        agent_id: str,
    ) -> None:
        """Upsert an Entity node with tenant isolation."""

    @abc.abstractmethod
    async def insert_edge(
        self,
        source_id: str,
        target_id: str,
        weight: float,
        agent_id: str,
        epistemic_uncertainty: float = 0.0,
    ) -> None:
        """Upsert an Observed relationship between two Entity nodes."""

    @abc.abstractmethod
    async def get_neighbors(
        self,
        node_id: str,
        agent_id: str,
        max_hops: int = 2,
    ) -> list[dict[str, Any]]:
        """Return distinct neighbor nodes within max_hops, scoped by agent_id."""

    @abc.abstractmethod
    async def get_cognitive_salience(
        self,
        seed_id: str,
        agent_id: str,
        max_hops: int = 3,
        limit: int = 15,
    ) -> list[dict[str, Any]]:
        """Compute cognitive salience via spreading activation from a seed node."""


# ---------------------------------------------------------------------------
# KùzuDB implementation
# ---------------------------------------------------------------------------


class KuzuGraphProvider(BaseGraphProvider):
    """Async-safe graph provider backed by KùzuDB.

    All synchronous ``kuzu.Connection`` calls are offloaded to a bounded
    ``ThreadPoolExecutor`` via ``asyncio.run_in_executor`` to guarantee
    the main event loop is never blocked during disk I/O.

    Thread Safety
    ~~~~~~~~~~~~~
    Each ``KuzuGraphProvider`` instance creates its **own**
    ``kuzu.Connection``.  KùzuDB connections are NOT thread-safe, so
    all calls through a single provider are serialised via the executor.
    Multiple providers can coexist safely because each holds a separate
    connection to the same underlying ``kuzu.Database``.

    Guarantees:
        1. Zero event-loop blocking — every C++ call is offloaded.
        2. Per-instance ``kuzu.Connection`` for thread isolation.
        3. Graceful shutdown with connection cleanup.
        4. Structured logging for observability.
    """

    @staticmethod
    def _composite_id(agent_id: str, node_id: str) -> str:
        return f"{agent_id}::{node_id}"

    def __init__(
        self,
        db_path: str,
        *,
        max_workers: int = _MAX_WORKERS,
    ) -> None:
        self._db_path = db_path
        self._max_workers = max_workers
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="mesa_kuzu",
        )
        self._db: kuzu.Database | None = None
        self._conn: kuzu.Connection | None = None
        self._conn_lock = threading.Lock()
        self._initialized = False
        self._init_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def db_path(self) -> str:
        return self._db_path

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    # ------------------------------------------------------------------
    # Async context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "KuzuGraphProvider":
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Open the KùzuDB database and create a thread-local connection.

        Idempotent — safe to call multiple times.
        """
        if self._initialized:
            return

        async with self._init_lock:
            if self._initialized:
                return

            loop = asyncio.get_running_loop()
            await loop.run_in_executor(self._executor, self._sync_connect)
            self._initialized = True
            logger.info(
                "KUZU_PROVIDER_INIT | db_path=%s workers=%d",
                self._db_path,
                self._max_workers,
            )

    def _sync_connect(self) -> None:
        """Synchronous connection setup (runs in executor thread).

        Opens the shared Database handle and creates a per-instance
        Connection.  The Database may already be open (managed by the
        FastAPI lifespan), so we open our own reference here — KùzuDB
        handles concurrent Database handles to the same path safely.

        Performs a health check probe after connection to verify the
        database is accessible and the schema is queryable.
        """
        self._db = kuzu.Database(self._db_path)
        self._conn = kuzu.Connection(self._db)

        # Startup health probe — verify connection is functional
        try:
            result = self._conn.execute("RETURN 1 AS probe;")
            if isinstance(result, list):
                result = result[0]  # pragma: no cover
            if hasattr(result, "has_next") and result.has_next():
                row = result.get_next()
                logger.debug(
                    "KUZU_HEALTH_PROBE | db_path=%s probe=%s — connection verified",
                    self._db_path,
                    row,
                )
            else:
                logger.warning(
                    "KUZU_HEALTH_PROBE | db_path=%s — probe returned no rows",
                    self._db_path,
                )
        except Exception as probe_exc:
            logger.error(
                "KUZU_HEALTH_PROBE_FAILED | db_path=%s error=%s — "
                "connection established but query failed",
                self._db_path,
                probe_exc,
                exc_info=True,
            )

    async def health_check(self) -> dict:
        """Perform a lightweight health check on the KùzuDB connection.

        Returns:
            Dict with health status and diagnostic information.
        """
        result: dict = {
            "status": "unknown",
            "db_path": self._db_path,
            "initialized": self._initialized,
            "max_workers": self._max_workers,
        }

        if not self._initialized:
            result["status"] = "not_initialized"
            return result

        try:
            rows = await self.execute_query("RETURN 1 AS probe")
            if rows:
                result["status"] = "healthy"
            else:
                result["status"] = "degraded"
        except Exception as exc:
            result["status"] = "unhealthy"
            result["error"] = str(exc)

        return result

    async def close(self) -> None:
        """Close the connection, release the database, and shut down the executor."""
        if self._initialized:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(self._executor, self._sync_close)
            logger.info("KUZU_PROVIDER_CLOSED | db_path=%s", self._db_path)

        self._executor.shutdown(wait=False)
        self._initialized = False

    def _sync_close(self) -> None:
        """Synchronous cleanup (runs in executor thread)."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        if self._db is not None:
            self._db.close()
            self._db = None

    # ------------------------------------------------------------------
    # Query execution — async wrappers
    # ------------------------------------------------------------------

    async def execute_query(
        self,
        query: str,
        parameters: dict[str, Any] | None = None,
    ) -> list[list[Any]]:
        """Execute a Cypher read query and return all result rows.

        Offloads the synchronous ``kuzu.Connection.execute`` call to
        the thread pool executor.

        Args:
            query: Cypher query string.  Use ``$param`` syntax for
                   parameterised values.
            parameters: Optional mapping of parameter names to values.

        Returns:
            List of rows, where each row is a list of column values.

        Raises:
            RuntimeError: If the provider has not been initialised.
        """
        self._ensure_initialized()

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor, self._sync_execute, query, parameters or {}
        )

    async def execute_write(
        self,
        query: str,
        parameters: dict[str, Any] | None = None,
    ) -> None:
        """Execute a Cypher write statement (CREATE, MERGE, DELETE, SET).

        Same executor-offloading strategy as ``execute_query``, but
        discards the result set.

        Args:
            query: Cypher mutation statement.
            parameters: Optional mapping of parameter names to values.

        Raises:
            RuntimeError: If the provider has not been initialised.
        """
        self._ensure_initialized()

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            self._executor, self._sync_execute_write, query, parameters or {}
        )

    # ------------------------------------------------------------------
    # Domain operations — node & edge ingestion
    # ------------------------------------------------------------------

    # Cypher templates use strict $param binding — never f-strings.
    # MERGE ensures idempotency; ON CREATE SET populates only on first
    # insert, preventing accidental overwrites on re-ingestion.

    _UPSERT_NODE_CYPHER = (
        "MERGE (n:Entity {id: $id, agent_id: $agent_id}) "
        "ON CREATE SET n.name = $name"
    )

    _UPSERT_EDGE_CYPHER = (
        "MATCH (a:Entity {id: $source_id, agent_id: $agent_id}), (b:Entity {id: $target_id, agent_id: $agent_id}) "
        "MERGE (a)-[r:Observed]->(b) "
        "ON CREATE SET r.weight = $weight, "
        "r.agent_id = $agent_id, "
        "r.updated_at = current_timestamp(), "
        "r.epistemic_uncertainty = $epistemic_uncertainty"
    )

    async def insert_node(
        self,
        node_id: str,
        name: str,
        agent_id: str,
    ) -> None:
        """Upsert an Entity node into the KùzuDB graph.

        Uses ``MERGE ... ON CREATE SET`` so that:
          - First call creates the node with all properties.
          - Subsequent calls with the same ``node_id`` are no-ops
            (idempotent re-ingestion).

        All values are bound via Cypher ``$param`` syntax to prevent
        injection.  ``agent_id`` is mandatory for Zero-Trust tenant
        isolation.

        Args:
            node_id: Unique entity identifier (UUID).
            name: Human-readable entity name.
            agent_id: Tenant isolation key (mandatory).
        """
        composite_id = self._composite_id(agent_id, node_id)
        await self.execute_write(
            self._UPSERT_NODE_CYPHER,
            {"id": composite_id, "name": name, "agent_id": agent_id},
        )

    async def insert_edge(
        self,
        source_id: str,
        target_id: str,
        weight: float,
        agent_id: str,
        epistemic_uncertainty: float = 0.0,
    ) -> None:
        """Upsert an Observed relationship between two Entity nodes.

        Uses ``MATCH`` + ``MERGE`` so that:
          - Both endpoints must already exist (no dangling edges).
          - First call creates the relationship with weight, agent_id,
            epistemic_uncertainty, and a server-side ``current_timestamp()``.
          - Subsequent calls with the same (source, target) pair are
            no-ops (idempotent).

        All values are bound via Cypher ``$param`` syntax to prevent
        injection.  ``agent_id`` is mandatory for Zero-Trust tenant
        isolation.

        Args:
            source_id: UUID of the source Entity node.
            target_id: UUID of the target Entity node.
            weight: Relationship strength / confidence score.
            agent_id: Tenant isolation key (mandatory).
            epistemic_uncertainty: Uncertainty score (0.0 = certain,
                1.0 = fully uncertain).  Calculated as
                ``1.0 - consensus_score`` during ingestion.  Used by
                the Damped PageRank quarantine scanner to penalise
                edges with low epistemic confidence.

        Note:
            If either ``source_id`` or ``target_id`` does not exist in
            the graph, the ``MATCH`` clause returns zero rows and the
            ``MERGE`` is silently skipped — no error is raised.
        """
        comp_source_id = self._composite_id(agent_id, source_id)
        comp_target_id = self._composite_id(agent_id, target_id)
        await self.execute_write(
            self._UPSERT_EDGE_CYPHER,
            {
                "source_id": comp_source_id,
                "target_id": comp_target_id,
                "weight": weight,
                "agent_id": agent_id,
                "epistemic_uncertainty": epistemic_uncertainty,
            },
        )

    # ------------------------------------------------------------------
    # Graph traversal — spreading activation
    # ------------------------------------------------------------------

    # KùzuDB does NOT support parameterised hop bounds in path patterns
    # (e.g. *1..$max_hops is a parse error).  We validate max_hops
    # against a strict integer allowlist and interpolate the literal.
    # This is injection-safe: the value is a Python int, never a
    # user-supplied string.
    _ALLOWED_MAX_HOPS = frozenset({1, 2, 3})

    async def get_neighbors(
        self,
        node_id: str,
        agent_id: str,
        max_hops: int = 2,
    ) -> list[dict[str, Any]]:
        """Return distinct neighbor nodes reachable within *max_hops*.

        Executes a variable-length Cypher path traversal with **dual
        agent_id enforcement** on both the source and destination nodes
        to guarantee Zero-Trust tenant isolation.

        The source node itself is excluded from results via
        ``WHERE b.id <> $node_id``.

        Args:
            node_id: UUID of the starting Entity node.
            agent_id: Tenant isolation key (mandatory on both endpoints).
            max_hops: Maximum traversal depth (1, 2, or 3).  Defaults
                      to 2 for standard cognitive spreading activation.

        Returns:
            List of neighbor dicts, each containing::

                {
                    "id":   str,   # Entity UUID
                    "name": str,   # Human-readable name
                    "hops": int,   # Shortest path length from source
                }

        Raises:
            ValueError: If *max_hops* is not in the allowed set {1, 2, 3}.
            RuntimeError: If the provider has not been initialised.
        """
        if max_hops not in self._ALLOWED_MAX_HOPS:
            raise ValueError(
                f"max_hops must be one of {sorted(self._ALLOWED_MAX_HOPS)}, "
                f"got {max_hops}"
            )
        self._ensure_initialized()

        comp_node_id = self._composite_id(agent_id, node_id)

        # Build Cypher with literal hop bound (safe — validated int).
        cypher = (
            f"MATCH (a:Entity {{id: $node_id, agent_id: $agent_id}})"
            f"-[r:Observed*1..{max_hops}]-"
            f"(b:Entity {{agent_id: $agent_id}}) "
            f"WHERE b.id <> $node_id "
            f"RETURN DISTINCT b.id, b.name, length(r)"
        )

        rows = await self.execute_query(
            cypher,
            {"node_id": comp_node_id, "agent_id": agent_id},
        )

        prefix = f"{agent_id}::"
        result = []
        for row in rows:
            raw_id = row[0]
            parsed_id = raw_id[len(prefix) :] if raw_id.startswith(prefix) else raw_id
            result.append({"id": parsed_id, "name": row[1], "hops": row[2]})

        return result

    # ------------------------------------------------------------------
    # Phase 4.2 — Cognitive Salience via Spreading Activation
    # ------------------------------------------------------------------

    async def get_cognitive_salience(
        self,
        seed_id: str,
        agent_id: str,
        max_hops: int = 3,
        limit: int = 15,
    ) -> list[dict[str, Any]]:
        """Compute cognitive salience scores via in-engine spreading activation.

        Executes a **single Cypher query** that simulates energy spreading
        from a seed Entity node across the graph.  The salience score for
        each reachable node is calculated as::

            salience = (1.0 / hops) / (fan_out + 1)

        Where:
          - ``hops`` is the minimum path length from the seed node.
          - ``fan_out`` is the outgoing edge count of the candidate node.
          - The ``+ 1`` prevents division-by-zero for leaf nodes and
            penalises high-fan-out hubs (information dilution).

        **ADR 001 compliance**: The entire computation is pushed into the
        KùzuDB engine via a single Cypher traversal.  No N+1 Python
        round-trips, no intermediate materialisation.

        **Phase 4.1 integration**: Quarantined nodes
        (``is_quarantined = true``) are excluded from results, ensuring
        self-healing graph hygiene propagates into retrieval.

        **Zero-Trust**: Both the seed node MATCH and the traversal
        destination enforce ``agent_id`` via parameterised binding.

        **Error handling**: KùzuDB ``RuntimeError`` exceptions (e.g.
        malformed graph state, engine failures) are caught, logged,
        and degraded to an empty result set — never crash the caller.

        Args:
            seed_id: UUID of the seed Entity node (activation source).
            agent_id: Tenant isolation key (mandatory).
            max_hops: Maximum traversal depth (1, 2, or 3).  Defaults
                      to 3 for deep cognitive spreading.
            limit: Maximum results returned, ordered by descending
                   salience.  Defaults to 15.

        Returns:
            List of dicts ordered by descending salience::

                [
                    {
                        "node_id": str,   # Entity UUID
                        "score":   float,  # Cognitive salience
                    },
                    ...
                ]

            Empty list if the seed node doesn't exist, no reachable
            non-quarantined nodes are found, or the query fails.

        Raises:
            ValueError: If *max_hops* is not in the allowed set {1, 2, 3}.
            RuntimeError: If the provider has not been initialised.
        """
        if max_hops not in self._ALLOWED_MAX_HOPS:
            raise ValueError(
                f"max_hops must be one of {sorted(self._ALLOWED_MAX_HOPS)}, "
                f"got {max_hops}"
            )
        self._ensure_initialized()

        comp_seed_id = self._composite_id(agent_id, seed_id)

        # Build Cypher with literal hop bound (safe — validated int).
        # The entire spreading activation runs inside the KùzuDB engine
        # in a single query — zero N+1 context switches.
        cypher = (
            f"MATCH (seed:Entity {{id: $seed_id, agent_id: $agent_id}}) "
            f"MATCH (seed)-[r*1..{max_hops}]-(n:Entity {{agent_id: $agent_id}}) "
            f"WHERE n.is_quarantined = false "
            f"WITH n, min(length(r)) AS hops "
            f"OPTIONAL MATCH (n)-[:Observed]->(m:Entity) "
            f"WITH n, hops, count(m) AS fan "
            f"RETURN n.id AS node_id, "
            f"       (1.0 / hops) / CAST(fan + 1 AS FLOAT) AS salience_score "
            f"ORDER BY salience_score DESC "
            f"LIMIT $limit"
        )

        try:
            rows = await self.execute_query(
                cypher,
                {
                    "seed_id": comp_seed_id,
                    "agent_id": agent_id,
                    "limit": limit,
                },
            )
        except RuntimeError as exc:
            logger.error(
                "COGNITIVE_SALIENCE_FAILED | agent_id=%s seed_id=%s error=%s",
                agent_id,
                seed_id,
                exc,
            )
            return []

        prefix = f"{agent_id}::"
        return [
            {
                "node_id": (
                    row[0][len(prefix) :]
                    if isinstance(row[0], str) and row[0].startswith(prefix)
                    else row[0]
                ),
                "score": float(row[1]) if row[1] is not None else 0.0,
            }
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Synchronous internals (run inside executor threads)
    # ------------------------------------------------------------------

    def _sync_execute(
        self,
        query: str,
        parameters: dict[str, Any],
    ) -> list[list[Any]]:
        """Run a Cypher query and drain all rows (executor thread)."""
        import typing

        with self._conn_lock:
            assert self._conn is not None, "Connection not initialised"
            result = self._conn.execute(query, parameters=parameters)

        rows: list[list[Any]] = []
        if hasattr(result, "has_next"):
            agent_id = parameters.get("agent_id") if parameters else None
            prefix = f"{agent_id}::" if agent_id else None

            while result.has_next():  # type: ignore[union-attr]
                row = result.get_next()  # type: ignore[union-attr]
                if prefix:
                    if isinstance(row, dict):
                        for k, v in row.items():
                            if isinstance(v, str) and v.startswith(prefix):
                                row[k] = v[len(prefix) :]
                    elif isinstance(row, list):
                        for i in range(len(row)):
                            if isinstance(row[i], str) and row[i].startswith(prefix):
                                row[i] = row[i][len(prefix) :]
                rows.append(typing.cast(list[Any], row))
        return rows

    def _sync_execute_write(
        self,
        query: str,
        parameters: dict[str, Any],
    ) -> None:
        """Run a Cypher mutation and discard the result (executor thread)."""
        with self._conn_lock:
            assert self._conn is not None, "Connection not initialised"
            self._conn.execute(query, parameters=parameters)

    # ------------------------------------------------------------------
    # Guards
    # ------------------------------------------------------------------

    def _ensure_initialized(self) -> None:
        """Raise if the provider has not been initialised."""
        if not self._initialized:
            raise RuntimeError(
                "KuzuGraphProvider has not been initialised. "
                "Call `await provider.initialize()` first."
            )
