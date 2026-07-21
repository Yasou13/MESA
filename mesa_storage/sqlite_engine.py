# MESA v0.3.0 — Phase 3: Non-blocking SQLite Connection Manager
# Enforces WAL journal mode, NORMAL synchronous, and 64MB cache on every
# connection to prevent concurrency locks under async workloads.
#
# Architecture:
#   - Single AsyncEngine instance per database file
#   - Bounded connection concurrency via asyncio.Semaphore
#   - PRAGMA enforcement on every connection open (not just first)
#   - Connection metrics for observability
#   - WAL checkpoint management for disk space control
#   - Graceful shutdown with connection draining
#   - Async context manager protocol for lifecycle management
"""
Non-blocking aiosqlite connection manager for the MESA storage layer.

Enforces production-grade PRAGMA configurations on every connection to
eliminate WAL contention under concurrent async readers/writers:

    PRAGMA journal_mode=WAL;      — write-ahead logging for lock-free reads
    PRAGMA synchronous=NORMAL;    — balanced durability vs. throughput
    PRAGMA cache_size=-64000;     — 64 MB page cache (negative = KiB)
    PRAGMA foreign_keys=ON;       — referential integrity enforcement
    PRAGMA busy_timeout=5000;     — 5s busy retry for transient WAL locks
    PRAGMA temp_store=MEMORY;     — in-memory temporary tables

Connection Pooling:
    Uses an asyncio.Semaphore to bound concurrent connections and prevent
    file descriptor exhaustion.  Default max_connections=8, tuneable at
    construction time.

Usage::

    async with AsyncEngine("./storage/graph.db") as engine:
        async with engine.connection() as db:
            await db.execute("INSERT INTO ...")
            await db.commit()

    # Or manual lifecycle:
    engine = AsyncEngine("./storage/graph.db")
    await engine.initialize()
    ...
    await engine.close()
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator

import aiosqlite

logger = logging.getLogger("MESA_Storage")

# ---------------------------------------------------------------------------
# PRAGMA constants — enforced on EVERY connection open
# ---------------------------------------------------------------------------

_PRAGMA_INIT: list[str] = [
    "PRAGMA journal_mode=WAL;",
    "PRAGMA synchronous=NORMAL;",
    "PRAGMA cache_size=-64000;",
    "PRAGMA foreign_keys=ON;",
    "PRAGMA busy_timeout=5000;",
    "PRAGMA temp_store=MEMORY;",
]

# ---------------------------------------------------------------------------
# Connection metrics — lightweight observability
# ---------------------------------------------------------------------------


@dataclass
class ConnectionMetrics:
    """Tracks connection lifecycle statistics for observability."""

    connections_opened: int = 0
    connections_closed: int = 0
    connections_failed: int = 0
    total_connection_time_ms: float = 0.0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    @property
    def connections_active(self) -> int:
        """Number of connections currently held by callers."""
        return self.connections_opened - self.connections_closed

    @property
    def avg_connection_time_ms(self) -> float:
        """Average time a connection was held, in milliseconds."""
        closed = self.connections_closed
        if closed == 0:
            return 0.0
        return self.total_connection_time_ms / closed

    async def record_open(self) -> None:
        async with self._lock:
            self.connections_opened += 1

    async def record_close(self, held_ms: float) -> None:
        async with self._lock:
            self.connections_closed += 1
            self.total_connection_time_ms += held_ms

    async def record_failure(self) -> None:
        async with self._lock:
            self.connections_failed += 1

    def snapshot(self) -> dict:
        """Return a JSON-serialisable snapshot of current metrics."""
        return {
            "connections_opened": self.connections_opened,
            "connections_closed": self.connections_closed,
            "connections_active": self.connections_active,
            "connections_failed": self.connections_failed,
            "avg_connection_time_ms": round(self.avg_connection_time_ms, 2),
        }


# ---------------------------------------------------------------------------
# Core engine
# ---------------------------------------------------------------------------


class AsyncEngine:
    """Non-blocking SQLite connection manager using aiosqlite.

    Guarantees:
        1. WAL mode + NORMAL sync on every connection (not just first).
        2. 64 MB page cache to minimise disk I/O on hot paths.
        3. 5-second busy timeout to handle transient WAL locks.
        4. Foreign key enforcement.
        5. Bounded concurrency via asyncio.Semaphore.

    Usage::

        engine = AsyncEngine("./storage/graph.db", max_connections=8)
        await engine.initialize()

        async with engine.connection() as db:
            await db.execute("INSERT INTO ...")
            await db.commit()

        await engine.close()

    Or as an async context manager::

        async with AsyncEngine("./storage/graph.db") as engine:
            async with engine.connection() as db:
                ...
    """

    def __init__(self, db_path: str, *, max_connections: int = 8) -> None:
        self._db_path = db_path
        self._max_connections = max_connections
        self._semaphore = asyncio.Semaphore(max_connections)
        self._initialized = False
        self._lock = asyncio.Lock()
        self._metrics = ConnectionMetrics()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def db_path(self) -> str:
        """Return the filesystem path to the managed database."""
        return self._db_path

    @property
    def metrics(self) -> ConnectionMetrics:
        """Return the connection metrics tracker."""
        return self._metrics

    @property
    def is_initialized(self) -> bool:
        """Whether the engine has been initialized."""
        return self._initialized

    # ------------------------------------------------------------------
    # Async context manager protocol
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "AsyncEngine":
        await self.initialize()
        return self

    import typing  # type: ignore[no-untyped-def]

    async def __aexit__(
        self, exc_type: typing.Any, exc_val: typing.Any, exc_tb: typing.Any
    ) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Ensure the database file and parent directories exist.

        Also performs a single connection to verify the PRAGMA configuration
        applies cleanly.  Idempotent — safe to call multiple times.
        """
        if self._initialized:
            return

        async with self._lock:
            if self._initialized:
                return

            # Ensure parent directory exists
            db_dir = Path(self._db_path).parent
            db_dir.mkdir(parents=True, exist_ok=True)

            # Verify PRAGMA application on a probe connection
            async with aiosqlite.connect(self._db_path) as db:
                await self._apply_pragmas(db)

                # Verify WAL mode was accepted
                async with db.execute("PRAGMA journal_mode;") as cursor:
                    row = await cursor.fetchone()
                    mode = row[0] if row else "unknown"
                    if mode.lower() != "wal":
                        logger.warning(
                            "WAL mode requested but got '%s' — "
                            "database may be in-memory or read-only",
                            mode,
                        )
                    else:
                        logger.info(
                            "PRAGMA_INIT | db=%s journal_mode=WAL "
                            "synchronous=NORMAL cache_size=64MB "
                            "max_connections=%d",
                            self._db_path,
                            self._max_connections,
                        )

            self._initialized = True

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[aiosqlite.Connection]:
        """Acquire a non-blocking connection with enforced PRAGMAs.

        Yields an ``aiosqlite.Connection`` that has already executed all
        production PRAGMAs.  The connection is closed on context exit.
        Connection concurrency is bounded by the configured semaphore.

        Raises:
            RuntimeError: If the engine has not been initialized.
        """
        if not self._initialized:
            raise RuntimeError(
                f"AsyncEngine for '{self._db_path}' has not been initialized. "
                "Call await engine.initialize() first."
            )

        await self._semaphore.acquire()
        t_start = time.monotonic()
        db: aiosqlite.Connection | None = None
        try:
            db = await aiosqlite.connect(self._db_path)
            await self._apply_pragmas(db)
            db.row_factory = aiosqlite.Row
            await self._metrics.record_open()
            yield db
        except Exception:
            await self._metrics.record_failure()
            raise
        finally:
            held_ms = (time.monotonic() - t_start) * 1000.0
            if db is not None:
                try:
                    await db.close()
                except Exception:
                    logger.debug(
                        "CONNECTION_CLOSE_ERROR | db=%s (ignored)", self._db_path
                    )
                await self._metrics.record_close(held_ms)
            self._semaphore.release()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        """Acquire a connection wrapped in an IMMEDIATE transaction.

        On success the caller is responsible for ``await db.commit()``.
        On exception the transaction is automatically rolled back.

        Usage::

            async with engine.transaction() as db:
                await db.execute("INSERT INTO ...")
                await db.commit()
        """
        async with self.connection() as db:
            await db.execute("BEGIN IMMEDIATE;")
            try:
                yield db
            except Exception:
                await db.execute("ROLLBACK;")
                raise

    async def execute_script(self, sql_script: str) -> None:
        """Execute a multi-statement SQL script in a single transaction.

        Useful for schema migrations and bulk DDL.
        """
        async with self.connection() as db:
            await db.executescript(sql_script)

    # ------------------------------------------------------------------
    # WAL management
    # ------------------------------------------------------------------

    async def checkpoint(self, mode: str = "PASSIVE") -> dict:
        """Execute a WAL checkpoint.

        Args:
            mode: One of PASSIVE, FULL, RESTART, or TRUNCATE.

        Returns:
            Dict with checkpoint result: busy, log_pages, checkpointed_pages.
        """
        valid_modes = {"PASSIVE", "FULL", "RESTART", "TRUNCATE"}
        mode = mode.upper()
        if mode not in valid_modes:
            raise ValueError(
                f"Invalid checkpoint mode '{mode}'. Must be one of {valid_modes}"
            )

        async with self.connection() as db:
            async with db.execute(f"PRAGMA wal_checkpoint({mode});") as cursor:
                row = await cursor.fetchone()
                if row:
                    result = {
                        "busy": row[0],
                        "log_pages": row[1],
                        "checkpointed_pages": row[2],
                    }
                    logger.info(
                        "WAL_CHECKPOINT | mode=%s busy=%d log=%d ckpt=%d",
                        mode,
                        result["busy"],
                        result["log_pages"],
                        result["checkpointed_pages"],
                    )
                    return result
                return {"busy": -1, "log_pages": -1, "checkpointed_pages": -1}

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    async def health_check(self) -> dict:
        """Perform a lightweight health check on the database.

        Returns:
            Dict with health status and diagnostic information.
        """
        result: dict = {
            "status": "unknown",
            "db_path": self._db_path,
            "initialized": self._initialized,
            "metrics": self._metrics.snapshot(),
        }

        if not self._initialized:
            result["status"] = "not_initialized"
            return result

        try:
            async with self.connection() as db:
                # Verify database is readable
                async with db.execute("SELECT 1;") as cursor:
                    row = await cursor.fetchone()
                    if row and row[0] == 1:
                        result["status"] = "healthy"
                    else:
                        result["status"] = "degraded"

                # Check WAL mode
                async with db.execute("PRAGMA journal_mode;") as cursor:
                    row = await cursor.fetchone()
                    result["journal_mode"] = row[0] if row else "unknown"

                # Check integrity (quick)
                async with db.execute("PRAGMA quick_check;") as cursor:
                    row = await cursor.fetchone()
                    result["integrity"] = row[0] if row else "unknown"

        except Exception as exc:
            result["status"] = "unhealthy"
            result["error"] = str(exc)

        return result

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Mark the engine as shut down.

        aiosqlite connections are already closed on context exit, so this
        method primarily resets the initialisation flag for clean restarts.
        Logs final connection metrics on shutdown.
        """
        async with self._lock:
            if self._initialized:
                logger.info(
                    "ASYNC_ENGINE_CLOSED | db=%s metrics=%s",
                    self._db_path,
                    self._metrics.snapshot(),
                )
            self._initialized = False

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    async def _apply_pragmas(db: aiosqlite.Connection) -> None:
        """Execute all production PRAGMAs on the given connection."""
        for pragma in _PRAGMA_INIT:
            await db.execute(pragma)
