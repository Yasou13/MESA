# MESA v0.3.0 — Phase 3: Disk-backed Vector Engine (LanceDB)
# Local-first, serverless vector storage linked to the SQLite graph layer.
#
# Architecture:
#   - LanceDB disk-backed tables partitioned by embedding dimension
#   - Mandatory node_id + agent_id row-level isolation on every record
#   - All synchronous LanceDB I/O offloaded via asyncio.run_in_executor
#     to prevent event loop blocking
#   - Cosine distance metric for similarity search
#   - Soft-delete via expired_at column for MVCC-style record management
#   - Metrics tracking for observability
"""
Disk-backed vector storage engine for the MESA knowledge graph.

Wraps LanceDB's synchronous API in an async-safe executor pattern so that
disk I/O never blocks the ``asyncio`` event loop.  Every record is linked
to the SQLite graph via ``node_id`` (UUID foreign key) and scoped to an
``agent_id`` for mandatory row-level tenant isolation.

Schema per table::

    node_id        TEXT   — UUID linking to nodes.id in SQLite
    agent_id       TEXT   — tenant isolation key
    embedding      VECTOR — float32 list of dimension N
    content_hash   TEXT   — SHA-256 of source content for dedup
    created_at     TEXT   — ISO 8601 timestamp
    expired_at     TEXT   — soft-delete marker (NULL = active)

Usage::

    engine = VectorEngine("./storage/vectors.lance")
    await engine.initialize()

    await engine.upsert(
        node_id="abc-123",
        agent_id="agent_alpha",
        embedding=[0.1, 0.2, ...],
    )

    results = await engine.search(
        query_vector=[0.1, 0.2, ...],
        limit=10,
        agent_id="agent_alpha",
    )

    await engine.close()
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import lancedb
import litellm
import pyarrow as pa
from sentence_transformers import SentenceTransformer

logger = logging.getLogger("MESA_Storage")

# ---------------------------------------------------------------------------
# Default configuration constants
# ---------------------------------------------------------------------------

_DEFAULT_METRIC = "cosine"
_DEFAULT_TABLE_PREFIX = "mesa_vectors_"
_MAX_WORKERS = 4

# Strict allowlist for values interpolated into LanceDB WHERE clauses.
# LanceDB does not support parameterised binding, so all filter values
# must be sanitised against injection at the engine boundary.
_SAFE_FILTER_VALUE_RE = re.compile(r"^[a-zA-Z0-9_\-\.@:]+$")


def _validate_filter_value(value: str, field_name: str) -> None:
    """Reject values that could manipulate LanceDB filter expressions.

    Raises:
        ValueError: If the value contains characters outside the strict
                    allowlist (alphanumeric, underscore, hyphen, dot, @, colon).
    """
    if not _SAFE_FILTER_VALUE_RE.match(value):
        raise ValueError(
            f"{field_name} contains unsafe characters for LanceDB filter: {value!r}"
        )


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


@dataclass
class VectorMetrics:
    """Lightweight observability for vector operations."""

    upserts: int = 0
    searches: int = 0
    soft_deletes: int = 0
    errors: int = 0
    total_search_time_ms: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def avg_search_time_ms(self) -> float:
        if self.searches == 0:
            return 0.0
        return self.total_search_time_ms / self.searches

    def snapshot(self) -> dict:
        return {
            "upserts": self.upserts,
            "searches": self.searches,
            "soft_deletes": self.soft_deletes,
            "errors": self.errors,
            "avg_search_time_ms": round(self.avg_search_time_ms, 2),
        }


# ---------------------------------------------------------------------------
# PyArrow schema factory
# ---------------------------------------------------------------------------


def _build_schema(dimension: int) -> pa.Schema:
    """Build a strict PyArrow schema for a given embedding dimension.

    Every record is linked to the SQLite graph via node_id and scoped
    to an agent_id for row-level isolation.
    """
    return pa.schema(
        [
            pa.field("node_id", pa.string(), nullable=False),
            pa.field("agent_id", pa.string(), nullable=False),
            pa.field("embedding", pa.list_(pa.float32(), dimension), nullable=False),
            pa.field("content_hash", pa.string(), nullable=True),
            pa.field("created_at", pa.string(), nullable=False),
            pa.field("expired_at", pa.string(), nullable=True),
        ]
    )


# ---------------------------------------------------------------------------
# Core engine
# ---------------------------------------------------------------------------


class VectorEngine:
    """Async-safe, disk-backed vector storage engine using LanceDB.

    All LanceDB synchronous calls are offloaded to a bounded
    ``ThreadPoolExecutor`` via ``asyncio.run_in_executor`` to guarantee
    the main event loop is never blocked during disk I/O.

    Guarantees:
        1. Every record carries ``node_id`` (FK to SQLite nodes.id) and
           ``agent_id`` for mandatory row-level tenant isolation.
        2. Cosine distance metric for all similarity searches.
        3. Soft-delete via ``expired_at`` column — active records have
           ``expired_at IS NULL``.
        4. Dimension-partitioned tables (``mesa_vectors_{dim}``) to
           support heterogeneous embedding models.
    """

    def __init__(
        self,
        uri: str,
        *,
        max_workers: int = _MAX_WORKERS,
        metric: str = _DEFAULT_METRIC,
    ) -> None:
        self._uri = uri
        self._metric = metric
        self._max_workers = max_workers
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="mesa_vec",
        )
        self._db: lancedb.db.LanceDBConnection | None = None
        self._tables: dict[str, Any] = {}
        self._table_lock = threading.Lock()
        self._mutation_lock = asyncio.Lock()
        self._initialized = False
        self._init_lock = asyncio.Lock()
        self._metrics = VectorMetrics()

        self._embedder = None
        self._fallback_embedder = False

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def uri(self) -> str:
        return self._uri

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    @property
    def metrics(self) -> VectorMetrics:
        return self._metrics

    # ------------------------------------------------------------------
    # Async context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "VectorEngine":
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Connect to LanceDB and prepare the storage directory.

        Idempotent — safe to call multiple times.
        """
        if self._initialized:
            return

        async with self._init_lock:
            if self._initialized:
                return

            loop = asyncio.get_running_loop()
            self._db = await loop.run_in_executor(self._executor, self._sync_connect)
            self._initialized = True
            logger.info(
                "VECTOR_ENGINE_INIT | uri=%s metric=%s workers=%d",
                self._uri,
                self._metric,
                self._max_workers,
            )

    def _sync_connect(self) -> lancedb.db.LanceDBConnection:
        """Synchronous LanceDB connection (runs in executor)."""
        Path(self._uri).mkdir(parents=True, exist_ok=True)

        # Initialize embedding model here to avoid blocking main thread
        try:
            self._embedder = SentenceTransformer("all-MiniLM-L6-v2")
            logger.info(
                "VECTOR_ENGINE_EMBEDDER | Loaded local sentence-transformers/all-MiniLM-L6-v2"
            )
        except Exception as exc:
            logger.warning(
                "VECTOR_ENGINE_EMBEDDER | Failed to load local model: %s. Falling back to litellm.",
                exc,
            )
            self._fallback_embedder = True

        return lancedb.connect(self._uri)

    def _sync_get_or_create_table(self, dimension: int) -> Any:
        """Get or create a dimension-partitioned table (thread-safe)."""
        table_name = f"{_DEFAULT_TABLE_PREFIX}{dimension}"

        with self._table_lock:
            if table_name in self._tables:
                return self._tables[table_name]

        schema = _build_schema(dimension)
        assert self._db is not None

        with self._table_lock:
            # Double-check after acquiring lock
            if table_name in self._tables:
                return self._tables[table_name]

            try:
                table = self._db.open_table(table_name)
            except (FileNotFoundError, ValueError):
                table = self._db.create_table(table_name, schema=schema)

            self._tables[table_name] = table
            return table

    # ------------------------------------------------------------------
    # Embedding generation
    # ------------------------------------------------------------------

    async def compute_embedding(self, text: str) -> list[float]:
        """Compute a 384-dimensional dense vector for the given text.

        Uses the local SentenceTransformer model if available, otherwise falls
        back to litellm text-embedding-3-small (and truncates/pads to 384 if needed).
        """
        if not self._initialized:
            raise RuntimeError("VectorEngine has not been initialized.")

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor, self._sync_compute_embedding, text
        )

    def _sync_compute_embedding(self, text: str) -> list[float]:
        if not self._fallback_embedder and self._embedder is not None:
            # SentenceTransformer returns numpy array
            vector = self._embedder.encode(text)
            return vector.tolist()
        else:
            # Fallback to litellm
            try:
                response = litellm.embedding(
                    model="text-embedding-3-small", input=[text]
                )
                vector = response.data[0]["embedding"]
                # Ensure 384 dimensions to match schema
                if len(vector) > 384:
                    vector = vector[:384]
                elif len(vector) < 384:
                    vector = vector + [0.0] * (384 - len(vector))
                return vector
            except Exception as exc:
                logger.error(
                    "VECTOR_ENGINE_EMBED_ERROR | litellm fallback failed: %s", exc
                )
                # Absolute last resort: return zero vector of correct dimension
                return [0.0] * 384

    async def compute_embedding_batch(self, texts: list[str]) -> list[list[float]]:
        """Compute embeddings for a batch of texts."""
        if not self._initialized:
            raise RuntimeError("VectorEngine has not been initialized.")

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor, self._sync_compute_embedding_batch, texts
        )

    def _sync_compute_embedding_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        if not self._fallback_embedder and self._embedder is not None:
            vectors = self._embedder.encode(texts)
            return [v.tolist() for v in vectors]
        else:
            # Fallback to litellm
            try:
                response = litellm.embedding(
                    model="text-embedding-3-small", input=texts
                )
                vectors = []
                for item in response.data:
                    v = item["embedding"]
                    if len(v) > 384:
                        v = v[:384]
                    elif len(v) < 384:
                        v = v + [0.0] * (384 - len(v))
                    vectors.append(v)
                return vectors
            except Exception as exc:
                logger.error(
                    "VECTOR_ENGINE_EMBED_ERROR | litellm fallback failed: %s", exc
                )
                return [[0.0] * 384 for _ in texts]

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    async def upsert(
        self,
        node_id: str,
        agent_id: str,
        embedding: list[float],
        content_hash: str | None = None,
    ) -> None:
        """Insert or update a vector record linked to a graph node.

        Uses LanceDB ``merge_insert`` on ``node_id`` for upsert
        semantics.  Falls back to ``add`` if merge_insert fails.

        Args:
            node_id: UUID linking to nodes.id in the SQLite graph.
            agent_id: Tenant isolation key (mandatory).
            embedding: Float32 embedding vector.
            content_hash: Optional SHA-256 of source content for dedup.
        """
        if not self._initialized:
            raise RuntimeError(
                "VectorEngine has not been initialized. "
                "Call await engine.initialize() first."
            )

        loop = asyncio.get_running_loop()
        async with self._mutation_lock:
            await loop.run_in_executor(
                self._executor,
                self._sync_upsert,
                node_id,
                agent_id,
                embedding,
                content_hash,
            )

    def _sync_upsert(
        self,
        node_id: str,
        agent_id: str,
        embedding: list[float],
        content_hash: str | None,
    ) -> None:
        """Synchronous upsert (runs in executor thread)."""
        dimension = len(embedding)
        table = self._sync_get_or_create_table(dimension)
        now = datetime.now(timezone.utc).isoformat()

        record = {
            "node_id": node_id,
            "agent_id": agent_id,
            "embedding": embedding,
            "content_hash": content_hash,
            "created_at": now,
            "expired_at": None,
        }

        try:
            table.merge_insert(
                "node_id"
            ).when_matched_update_all().when_not_matched_insert_all().execute([record])
        except (RuntimeError, OSError) as exc:
            logger.warning(
                "merge_insert failed for node_id=%s, falling back to add(): %s",
                node_id,
                exc,
            )
            table.add([record])

        with self._metrics._lock:
            self._metrics.upserts += 1

    async def bulk_upsert(
        self,
        records: list[dict],
    ) -> int:
        """Insert or update multiple vector records in a single batch.

        Each dict must contain: node_id, agent_id, embedding.
        Optional: content_hash.

        Returns:
            Number of records processed.
        """
        if not records:
            return 0
        if not self._initialized:
            raise RuntimeError("VectorEngine has not been initialized.")

        loop = asyncio.get_running_loop()
        async with self._mutation_lock:
            return await loop.run_in_executor(
                self._executor, self._sync_bulk_upsert, records
            )

    def _sync_bulk_upsert(self, records: list[dict]) -> int:
        """Synchronous bulk upsert (runs in executor thread)."""
        now = datetime.now(timezone.utc).isoformat()

        # Group records by dimension for table partitioning
        by_dim: dict[int, list[dict]] = {}
        for r in records:
            dim = len(r["embedding"])
            by_dim.setdefault(dim, []).append(r)

        total = 0
        for dim, batch in by_dim.items():
            table = self._sync_get_or_create_table(dim)
            rows = [
                {
                    "node_id": r["node_id"],
                    "agent_id": r["agent_id"],
                    "embedding": r["embedding"],
                    "content_hash": r.get("content_hash"),
                    "created_at": now,
                    "expired_at": None,
                }
                for r in batch
            ]
            try:
                table.merge_insert(
                    "node_id"
                ).when_matched_update_all().when_not_matched_insert_all().execute(rows)
            except (RuntimeError, OSError):
                logger.warning(
                    "bulk merge_insert failed for dim=%d, falling back to add()",
                    dim,
                )
                table.add(rows)
            total += len(rows)

        with self._metrics._lock:
            self._metrics.upserts += total

        return total

    # ------------------------------------------------------------------
    # E1 FIX: Orphan reconciliation support
    # ------------------------------------------------------------------

    async def get_existing_node_ids(
        self,
        agent_id: str,
        node_ids: list[str],
    ) -> set[str]:
        """Return the subset of ``node_ids`` that have vector entries.

        Used by the DAO startup reconciliation scan to detect orphaned
        SQLite nodes that were never written to LanceDB (SIGKILL mid-saga).

        LanceDB tables are dimension-partitioned (``mesa_vectors_{dim}``),
        not agent-partitioned, so this method scans all ``mesa_vectors_*``
        tables and filters by ``agent_id`` in the WHERE clause.

        Args:
            agent_id: Tenant isolation key (used in WHERE filter).
            node_ids: List of node IDs to check.

        Returns:
            Set of node_ids that have at least one vector entry.
        """
        if not node_ids or not self.is_initialized:
            return set()

        _validate_filter_value(agent_id, "agent_id")
        loop = asyncio.get_running_loop()

        def _check() -> set[str]:
            if self._db is None:
                return set()
            try:
                table_names = self._list_table_names()
            except Exception:
                logger.error("get_existing_node_ids failed to list tables", exc_info=True)
                return set()

            found: set[str] = set()
            for table_name in table_names:
                if not table_name.startswith(_DEFAULT_TABLE_PREFIX):
                    continue
                try:
                    table = self._db.open_table(table_name)
                except (FileNotFoundError, ValueError, OSError):
                    continue

                # Query for existing node_ids in batches to avoid
                # oversized filter expressions
                batch_size = 100
                for i in range(0, len(node_ids), batch_size):
                    batch = node_ids[i : i + batch_size]
                    # Build a safe OR filter scoped to agent_id
                    id_conditions = " OR ".join(
                        f"node_id = '{nid}'"
                        for nid in batch
                        if _SAFE_FILTER_VALUE_RE.match(nid)
                    )
                    if not id_conditions:
                        continue
                    where = f"agent_id = '{agent_id}' AND ({id_conditions})"
                    try:
                        result = (
                            table.search()
                            .where(where)
                            .select(["node_id"])
                            .limit(batch_size)
                            .to_arrow()
                        )
                        for row_idx in range(result.num_rows):
                            found.add(str(result.column("node_id")[row_idx]))
                    except Exception:
                        logger.warning(
                            "get_existing_node_ids query failed for table=%s agent=%s",
                            table_name, agent_id, exc_info=True,
                        )

            return found

        return await loop.run_in_executor(self._executor, _check)

    # ------------------------------------------------------------------
    # Search operations
    # ------------------------------------------------------------------

    async def search(
        self,
        query_vector: list[float],
        *,
        limit: int = 10,
        agent_id: str | None = None,
        include_expired: bool = False,
    ) -> list[dict]:
        """Execute a cosine similarity search against the vector index.

        Offloaded to the thread pool to prevent event loop blocking.

        Args:
            query_vector: The query embedding (float32).
            limit: Maximum number of nearest neighbors to return.
            agent_id: If provided, filters results to this tenant only.
            include_expired: If True, includes soft-deleted records.

        Returns:
            List of result dicts sorted by ascending cosine distance.
            Each dict includes node_id, agent_id, content_hash,
            created_at, and _distance.
        """
        if not self._initialized:
            raise RuntimeError("VectorEngine has not been initialized.")

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor,
            self._sync_search,
            query_vector,
            limit,
            agent_id,
            include_expired,
        )

    def _sync_search(
        self,
        query_vector: list[float],
        limit: int,
        agent_id: str | None,
        include_expired: bool,
    ) -> list[dict]:
        """Synchronous cosine search (runs in executor thread)."""
        t_start = time.monotonic()

        dimension = len(query_vector)
        table_name = f"{_DEFAULT_TABLE_PREFIX}{dimension}"

        with self._table_lock:
            table = self._tables.get(table_name)

        if table is None:
            try:
                table = self._sync_get_or_create_table(dimension)
            except (FileNotFoundError, ValueError):
                return []

        # Build query
        query = table.search(query_vector).metric(self._metric).limit(limit)

        # Apply filters
        filters: list[str] = []
        if not include_expired:
            filters.append("expired_at IS NULL")
        if agent_id is not None:
            _validate_filter_value(agent_id, "agent_id")
            filters.append(f"agent_id = '{agent_id}'")

        if filters:
            query = query.where(" AND ".join(filters))

        try:
            results = query.to_list()
        except Exception as exc:
            logger.warning("VECTOR_SEARCH_ERROR | error=%s", exc)
            with self._metrics._lock:
                self._metrics.errors += 1
            return []

        elapsed_ms = (time.monotonic() - t_start) * 1000.0
        with self._metrics._lock:
            self._metrics.searches += 1
            self._metrics.total_search_time_ms += elapsed_ms

        # Strip embedding from results to reduce memory on hot paths
        for r in results:
            r.pop("embedding", None)

        return results

    # ------------------------------------------------------------------
    # Delete operations
    # ------------------------------------------------------------------

    async def soft_delete(self, node_id: str) -> None:
        """Soft-delete a vector record by setting expired_at.

        Marks the record as expired rather than physically removing it,
        consistent with MVCC-style management across the storage layer.
        """
        if not self._initialized:
            raise RuntimeError("VectorEngine has not been initialized.")

        loop = asyncio.get_running_loop()
        async with self._mutation_lock:
            await loop.run_in_executor(self._executor, self._sync_soft_delete, node_id)

    def _sync_soft_delete(self, node_id: str) -> None:
        """Synchronous soft-delete (runs in executor thread)."""
        now = datetime.now(timezone.utc).isoformat()
        assert self._db is not None

        table_names = self._list_table_names()
        for table_name in table_names:
            if not table_name.startswith(_DEFAULT_TABLE_PREFIX):
                continue
            try:
                table = self._db.open_table(table_name)
                _validate_filter_value(node_id, "node_id")
                table.update(
                    where=f"node_id = '{node_id}'",
                    values={"expired_at": now},
                )
                # Invalidate cached handle — stale after mutation
                with self._table_lock:
                    self._tables.pop(table_name, None)
            except (RuntimeError, OSError) as exc:
                logger.error(
                    "VECTOR_SOFT_DELETE_ERROR | node_id=%s table=%s error=%s",
                    node_id,
                    table_name,
                    exc,
                )
                with self._metrics._lock:
                    self._metrics.errors += 1
                raise

        with self._metrics._lock:
            self._metrics.soft_deletes += 1

    async def hard_delete(self, node_id: str) -> None:
        """Physically remove a vector record from disk.

        Use sparingly — prefer soft_delete for audit trail.
        """
        if not self._initialized:
            raise RuntimeError("VectorEngine has not been initialized.")

        loop = asyncio.get_running_loop()
        async with self._mutation_lock:
            await loop.run_in_executor(self._executor, self._sync_hard_delete, node_id)

    def _sync_hard_delete(self, node_id: str) -> None:
        """Synchronous hard delete (runs in executor thread)."""
        assert self._db is not None

        for table_name in self._list_table_names():
            if not table_name.startswith(_DEFAULT_TABLE_PREFIX):
                continue
            try:
                table = self._db.open_table(table_name)
                _validate_filter_value(node_id, "node_id")
                table.delete(f"node_id = '{node_id}'")
                # Invalidate cached handle — stale after mutation
                with self._table_lock:
                    self._tables.pop(table_name, None)
            except (RuntimeError, OSError) as exc:
                logger.error(
                    "VECTOR_HARD_DELETE_ERROR | node_id=%s error=%s",
                    node_id,
                    exc,
                )
                raise

    # ------------------------------------------------------------------
    # Query operations
    # ------------------------------------------------------------------

    async def get_active_node_ids(self, agent_id: str | None = None) -> set[str]:
        """Return all active (non-expired) node_ids in the vector index.

        Args:
            agent_id: If provided, scopes results to this tenant.

        Returns:
            Set of active node_id strings.
        """
        if not self._initialized:
            raise RuntimeError("VectorEngine has not been initialized.")

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor, self._sync_get_active_node_ids, agent_id
        )

    def _sync_get_active_node_ids(self, agent_id: str | None) -> set[str]:
        """Synchronous node ID retrieval (runs in executor thread)."""
        assert self._db is not None
        ids: set[str] = set()

        for table_name in self._list_table_names():
            if not table_name.startswith(_DEFAULT_TABLE_PREFIX):
                continue
            try:
                table = self._db.open_table(table_name)
                where = "expired_at IS NULL"
                if agent_id:
                    _validate_filter_value(agent_id, "agent_id")
                    where += f" AND agent_id = '{agent_id}'"
                arrow_table = (
                    table.search()
                    .where(where)
                    .select(["node_id"])
                    .limit(100_000)
                    .to_arrow()
                )
                ids.update(arrow_table.column("node_id").to_pylist())
            except Exception as exc:
                logger.warning("get_active_node_ids error for %s: %s", table_name, exc)

        return ids

    async def count_records(self, active_only: bool = True) -> dict[str, int]:
        """Return record counts per dimension table.

        Args:
            active_only: If True, count only non-expired records.

        Returns:
            Dict mapping table name to record count.
        """
        if not self._initialized:
            raise RuntimeError("VectorEngine has not been initialized.")

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor, self._sync_count_records, active_only
        )

    def _sync_count_records(self, active_only: bool) -> dict[str, int]:
        assert self._db is not None
        counts: dict[str, int] = {}

        for table_name in self._list_table_names():
            if not table_name.startswith(_DEFAULT_TABLE_PREFIX):
                continue
            try:
                table = self._db.open_table(table_name)
                if active_only:
                    arrow_table = (
                        table.search()
                        .where("expired_at IS NULL")
                        .select(["node_id"])
                        .limit(1_000_000)
                        .to_arrow()
                    )
                    counts[table_name] = arrow_table.num_rows
                else:
                    counts[table_name] = table.count_rows()
            except Exception as exc:
                logger.warning("count_records error for %s: %s", table_name, exc)
                counts[table_name] = -1

        return counts

    # ------------------------------------------------------------------
    # Health & diagnostics
    # ------------------------------------------------------------------

    async def health_check(self) -> dict:
        """Return health status and diagnostic info."""
        result: dict = {
            "status": "unknown",
            "uri": self._uri,
            "initialized": self._initialized,
            "metrics": self._metrics.snapshot(),
        }

        if not self._initialized:
            result["status"] = "not_initialized"
            return result

        try:
            loop = asyncio.get_running_loop()
            tables = await loop.run_in_executor(self._executor, self._list_table_names)
            result["tables"] = [
                t for t in tables if t.startswith(_DEFAULT_TABLE_PREFIX)
            ]
            result["table_count"] = len(result["tables"])
            result["status"] = "healthy"
        except Exception as exc:
            result["status"] = "unhealthy"
            result["error"] = str(exc)

        return result

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Shut down the executor and reset state."""
        if self._initialized:
            logger.info(
                "VECTOR_ENGINE_CLOSED | uri=%s metrics=%s",
                self._uri,
                self._metrics.snapshot(),
            )
        self._executor.shutdown(wait=False)
        self._tables.clear()
        self._db = None
        self._initialized = False

    # ------------------------------------------------------------------
    # Phase 4.3 — Blue/Green Vector Space Alignment
    # ------------------------------------------------------------------

    async def apply_procrustes_and_switch(
        self,
        transformation_matrix: Any,  # numpy.ndarray — imported at runtime
        golden_dataset: list[dict[str, Any]],
        threshold: float = 0.85,
    ) -> bool:
        """Align the vector space using Orthogonal Procrustes rotation.

        Applies ``transformation_matrix`` (R* = U V^T) to all active
        embeddings via a **Blue/Green rollback protocol** that protects
        against LanceDB's lack of ACID transactions:

        1. **ISOLATE**: Deep-copy the active table to ``<name>_backup``.
        2. **TRANSFORM**: Apply ``np.dot(embedding, R*)`` to every
           active vector and write to ``<name>_new``.
        3. **VERIFY**: Evaluate ``Recall@5`` against ``golden_dataset``
           on the new table.
        4. **SWITCH or ROLLBACK**:
           - ``Recall@5 >= threshold`` → promote ``_new``, drop backup.
           - ``Recall@5 < threshold`` or exception → drop ``_new``,
             keep original intact.

        **Non-blocking**: All ``np.dot`` matrix multiplications and
        synchronous LanceDB I/O are offloaded to ``run_in_executor``.

        Args:
            transformation_matrix: Orthogonal rotation matrix (R*) from
                Procrustes analysis.  Shape must be ``(dim, dim)`` where
                dim matches the embedding dimension.
            golden_dataset: List of evaluation dicts::

                    [
                        {
                            "query_vector": [float, ...],
                            "expected_node_id": "uuid-string",
                        },
                        ...
                    ]

            threshold: Minimum ``Recall@5`` score (0.0–1.0) required
                to promote the transformed table.  Default 0.85.

        Returns:
            ``True`` if the alignment was accepted and promoted.
            ``False`` if the alignment was rolled back.

        Raises:
            RuntimeError: If the engine has not been initialised.
        """
        if not self._initialized:
            raise RuntimeError("VectorEngine has not been initialized.")

        assert self._db is not None

        loop = asyncio.get_running_loop()

        # Discover the active table (use first dimension-partitioned table)
        table_names: list[str] = await loop.run_in_executor(
            self._executor, self._list_table_names
        )
        active_tables: list[str] = [
            t
            for t in table_names
            if t.startswith(_DEFAULT_TABLE_PREFIX)
            and not t.endswith("_backup")
            and not t.endswith("_new")
        ]

        if not active_tables:
            logger.warning("ALIGN_SKIP | reason=no_active_tables uri=%s", self._uri)
            return False

        # Process each dimension-partitioned table
        all_succeeded: bool = True
        for active_name in active_tables:
            backup_name: str = f"{active_name}_backup"
            new_name: str = f"{active_name}_new"

            try:
                # ==================================================
                # PHASE 1: ISOLATE — deep-copy active → backup
                # ==================================================
                logger.info(
                    "ALIGN_ISOLATE | table=%s backup=%s",
                    active_name,
                    backup_name,
                )
                await loop.run_in_executor(
                    self._executor,
                    self._sync_copy_table,
                    active_name,
                    backup_name,
                )

                # ==================================================
                # PHASE 2: TRANSFORM — apply R* to all embeddings
                # ==================================================
                logger.info(
                    "ALIGN_TRANSFORM | table=%s new=%s matrix_shape=%s",
                    active_name,
                    new_name,
                    transformation_matrix.shape,
                )
                await loop.run_in_executor(
                    self._executor,
                    self._sync_transform_table,
                    active_name,
                    new_name,
                    transformation_matrix,
                )

                # ==================================================
                # PHASE 3: VERIFY — Recall@5 against golden dataset
                # ==================================================
                recall: float = await loop.run_in_executor(
                    self._executor,
                    self._sync_verify_recall,
                    new_name,
                    golden_dataset,
                )
                logger.info(
                    "ALIGN_VERIFY | table=%s recall@5=%.4f threshold=%.4f",
                    new_name,
                    recall,
                    threshold,
                )

                # ==================================================
                # PHASE 4: SWITCH or ROLLBACK
                # ==================================================
                if recall >= threshold:
                    # --- SWITCH: promote _new, drop backup + old ---
                    async with self._mutation_lock:
                        await loop.run_in_executor(
                            self._executor,
                            self._sync_promote_table,
                            active_name,
                            new_name,
                            backup_name,
                        )
                    logger.info(
                        "ALIGN_SWITCH_SUCCESS | table=%s recall@5=%.4f "
                        "new_table=%s promoted=true",
                        active_name,
                        recall,
                        new_name,
                    )
                else:
                    # --- ROLLBACK: drop _new, keep original ---
                    await loop.run_in_executor(
                        self._executor,
                        self._sync_rollback_table,
                        new_name,
                        backup_name,
                    )
                    logger.critical(
                        "ALIGN_ROLLBACK | table=%s recall@5=%.4f < threshold=%.4f "
                        "— new table dropped, original preserved",
                        active_name,
                        recall,
                        threshold,
                    )
                    all_succeeded = False

            except Exception as exc:
                # --- EXCEPTION ROLLBACK: drop _new, keep original ---
                logger.critical(
                    "ALIGN_EXCEPTION_ROLLBACK | table=%s error=%s "
                    "— dropping _new, preserving original",
                    active_name,
                    exc,
                )
                try:
                    await loop.run_in_executor(
                        self._executor,
                        self._sync_rollback_table,
                        new_name,
                        backup_name,
                    )
                except Exception as cleanup_exc:
                    logger.error(
                        "ALIGN_CLEANUP_FAILED | table=%s error=%s",
                        active_name,
                        cleanup_exc,
                    )
                all_succeeded = False

        return all_succeeded

    # ------------------------------------------------------------------
    # Blue/Green sync helpers (run inside executor threads)
    # ------------------------------------------------------------------

    def _sync_copy_table(
        self,
        source_name: str,
        dest_name: str,
    ) -> None:
        """Deep-copy a LanceDB table for backup (runs in executor).

        Reads all rows from the source table and inserts them into a
        new table with the same schema.
        """
        assert self._db is not None

        # Drop destination if it already exists (stale from previous run)
        existing: list[str] = self._list_table_names()
        if dest_name in existing:
            self._db.drop_table(dest_name)

        source_table = self._db.open_table(source_name)
        arrow_data: pa.Table = source_table.to_arrow()

        if arrow_data.num_rows == 0:
            self._db.create_table(dest_name, schema=arrow_data.schema)
        else:
            self._db.create_table(dest_name, data=arrow_data)

        logger.debug(
            "TABLE_COPIED | src=%s dest=%s rows=%d",
            source_name,
            dest_name,
            arrow_data.num_rows,
        )

    def _sync_transform_table(
        self,
        source_name: str,
        new_name: str,
        transformation_matrix: Any,  # numpy.ndarray — imported at runtime
    ) -> None:
        """Apply Procrustes rotation to all embeddings (runs in executor).

        Reads all active (non-expired) embeddings from the source table,
        applies ``np.dot(embedding, R*)`` to each vector, and writes the
        transformed records to a new table.

        The ``np.dot`` call operates on the full matrix at once (batched)
        to leverage BLAS acceleration.
        """
        import numpy as np

        assert self._db is not None

        # Drop _new if stale from a previous failed run
        existing: list[str] = self._list_table_names()
        if new_name in existing:
            self._db.drop_table(new_name)

        source_table = self._db.open_table(source_name)
        arrow_data: pa.Table = source_table.to_arrow()

        if arrow_data.num_rows == 0:
            self._db.create_table(new_name, schema=arrow_data.schema)
            return

        # Extract embeddings as numpy matrix for batched dot product
        embedding_col = arrow_data.column("embedding")
        embeddings_list: list[list[float]] = embedding_col.to_pylist()
        embeddings_np: np.ndarray = np.array(embeddings_list, dtype=np.float32)

        # Batched matrix multiplication: (N, D) @ (D, D) → (N, D)
        transformed_np: np.ndarray = np.dot(
            embeddings_np, transformation_matrix.astype(np.float32)
        )

        # Rebuild the Arrow table with transformed embeddings
        dim: int = transformed_np.shape[1]
        transformed_lists: list[list[float]] = transformed_np.tolist()

        new_embedding_array = pa.array(
            transformed_lists,
            type=pa.list_(pa.float32(), dim),
        )

        # Replace the embedding column
        col_idx: int = arrow_data.schema.get_field_index("embedding")
        new_arrow: pa.Table = arrow_data.set_column(
            col_idx, arrow_data.schema.field(col_idx), new_embedding_array
        )

        self._db.create_table(new_name, data=new_arrow)

        logger.debug(
            "TABLE_TRANSFORMED | src=%s new=%s rows=%d dim=%d",
            source_name,
            new_name,
            new_arrow.num_rows,
            dim,
        )

    def _sync_verify_recall(
        self,
        table_name: str,
        golden_dataset: list[dict[str, Any]],
    ) -> float:
        """Compute Recall@5 on the golden dataset (runs in executor).

        For each entry in golden_dataset, searches the specified table
        for the top 5 nearest neighbors and checks if
        ``expected_node_id`` is present.

        Returns:
            Recall@5 as a float between 0.0 and 1.0.
        """
        assert self._db is not None

        if not golden_dataset:
            logger.warning("RECALL_VERIFY_SKIP | reason=empty_golden_dataset")
            return 0.0

        table = self._db.open_table(table_name)

        hits: int = 0
        total: int = len(golden_dataset)

        for entry in golden_dataset:
            query_vector: list[float] = entry["query_vector"]
            expected_id: str = entry["expected_node_id"]

            try:
                results = (
                    table.search(query_vector)
                    .metric(self._metric)
                    .where("expired_at IS NULL")
                    .limit(5)
                    .select(["node_id"])
                    .to_list()
                )
                result_ids: list[str] = [r["node_id"] for r in results]
                if expected_id in result_ids:
                    hits += 1
            except Exception as exc:
                logger.warning(
                    "RECALL_QUERY_FAILED | table=%s expected_id=%s error=%s",
                    table_name,
                    expected_id,
                    exc,
                )

        recall: float = hits / total if total > 0 else 0.0
        return recall

    def _sync_promote_table(
        self,
        active_name: str,
        new_name: str,
        backup_name: str,
    ) -> None:
        """Promote _new to active and clean up (runs in executor).

        Sequence:
        1. Drop the backup table (no longer needed).
        2. Drop the old active table.
        3. Rename _new → active.
        4. Invalidate cached table handles.
        """
        assert self._db is not None

        # 1. Drop backup
        try:
            self._db.drop_table(backup_name)
        except Exception as exc:
            logger.warning("DROP_BACKUP_FAILED | table=%s error=%s", backup_name, exc)

        # 2. Drop old active
        try:
            self._db.drop_table(active_name)
        except Exception as exc:
            logger.warning("DROP_ACTIVE_FAILED | table=%s error=%s", active_name, exc)

        # 3. Rename _new → active
        try:
            self._db.rename_table(new_name, active_name)
        except (AttributeError, Exception):
            # LanceDB may not support rename_table in all versions.
            # Fallback: copy _new → active, then drop _new.
            self._sync_copy_table(new_name, active_name)
            try:
                self._db.drop_table(new_name)
            except Exception:
                logger.warning(
                    "PROMOTE_DROP_NEW_FAILED | table=%s — stale table may remain",
                    new_name, exc_info=True,
                )

        # 4. Invalidate cached table handles
        with self._table_lock:
            self._tables.pop(active_name, None)
            self._tables.pop(new_name, None)
            self._tables.pop(backup_name, None)

    def _sync_rollback_table(
        self,
        new_name: str,
        backup_name: str,
    ) -> None:
        """Drop the _new table and clean up backup (runs in executor).

        The original active table is untouched — this is the safety
        guarantee of the Blue/Green protocol.
        """
        assert self._db is not None

        # Drop the failed _new table
        try:
            existing: list[str] = self._list_table_names()
            if new_name in existing:
                self._db.drop_table(new_name)
        except Exception as exc:
            logger.error("ROLLBACK_DROP_NEW_FAILED | table=%s error=%s", new_name, exc)

        # Drop the backup (original is still intact)
        try:
            existing = self._list_table_names()
            if backup_name in existing:
                self._db.drop_table(backup_name)
        except Exception as exc:
            logger.warning(
                "ROLLBACK_DROP_BACKUP_FAILED | table=%s error=%s",
                backup_name,
                exc,
            )

        # Invalidate cached handles
        with self._table_lock:
            self._tables.pop(new_name, None)
            self._tables.pop(backup_name, None)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _list_table_names(self) -> list[str]:
        """Return table names, handling LanceDB API version differences."""
        assert self._db is not None
        # LanceDB >=0.30 deprecated table_names() for list_tables()
        if hasattr(self._db, "list_tables"):
            result = self._db.list_tables()
        else:
            result = self._db.table_names()  # pragma: no cover
        if isinstance(result, list):
            return result
        if hasattr(result, "tables"):
            return result.tables
        return list(result)
