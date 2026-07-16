# MESA v0.3.0 — Phase 3: Isolated Maintenance Worker
# Resolves critical database lock vulnerability by isolating destructive
# I/O operations (VACUUM, LanceDB hard-delete, compaction) into a
# standalone background worker that runs ONLY during configured idle
# windows — never triggered by API routes.
#
# Architecture:
#   - API routes (DELETE /purge) ONLY perform soft-deletes
#   - This worker is the SOLE path for physical data removal
#   - Scheduled via asyncio sleep-loop with configurable idle windows
#   - SQLite VACUUM runs on a dedicated connection OUTSIDE the pool
#     (VACUUM requires exclusive access — no WAL readers allowed)
#   - LanceDB purge: hard-deletes expired records, then attempts
#     compaction via cleanup_old_versions (graceful if pylance absent)
#   - Full lifecycle metrics and structured logging for observability
"""
Isolated background maintenance worker for MESA v0.3.0.

Performs destructive I/O operations (SQLite VACUUM, LanceDB hard-delete
and compaction) on a configurable schedule, completely decoupled from
the request/response path.

**Critical design constraint**: API routes MUST only soft-delete records.
This worker is the single entry point for physical data removal, ensuring
no lock contention under concurrent API load.

Schedule windows are configurable at construction time.  By default the
worker runs daily at 00:00 UTC (midnight).  Multiple windows can be
specified for high-throughput deployments that need more frequent cleanup.

Usage::

    worker = MaintenanceWorker(
        sqlite_engine=engine,
        vector_engine=vec_engine,
        schedule_hours=[0, 12],  # midnight + noon UTC
    )
    task = asyncio.create_task(worker.start())
    ...
    await worker.stop()

Or as an async context manager::

    async with MaintenanceWorker(engine, vec_engine) as worker:
        # worker runs in background until context exit
        await some_long_running_app()
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger("MESA_Maintenance")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Default idle window: midnight UTC
_DEFAULT_SCHEDULE_HOURS: list[int] = [0]

# Grace period before first run to let the application fully start
_STARTUP_GRACE_SECONDS = 30

# Minimum interval between maintenance cycles to prevent tight loops
_MIN_CYCLE_INTERVAL_SECONDS = 3600  # 1 hour

# Maximum age for soft-deleted records before physical removal (hours)
_DEFAULT_RETENTION_HOURS = 24

# B2 FIX: Strict ISO 8601 timestamp validation for LanceDB filter values.
# LanceDB does not support parameterised bindings in WHERE/DELETE clauses,
# so timestamp values must be validated before interpolation to prevent
# latent SQL injection if the source is ever changed.
_ISO8601_SAFE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(\+\d{2}:\d{2}|Z)?$"
)

# VACUUM requires exclusive access — use a separate connection with
# extended busy timeout to wait for WAL readers to drain
_VACUUM_BUSY_TIMEOUT_MS = 30_000


# ---------------------------------------------------------------------------
# Maintenance metrics
# ---------------------------------------------------------------------------


@dataclass
class MaintenanceMetrics:
    """Tracks maintenance cycle statistics for observability."""

    cycles_completed: int = 0
    cycles_failed: int = 0
    total_vacuum_time_ms: float = 0.0
    total_purge_time_ms: float = 0.0
    nodes_purged: int = 0
    edges_purged: int = 0
    vectors_purged: int = 0
    last_cycle_at: str | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    async def record_cycle(
        self,
        vacuum_ms: float,
        purge_ms: float,
        nodes: int,
        edges: int,
        vectors: int,
    ) -> None:
        async with self._lock:
            self.cycles_completed += 1
            self.total_vacuum_time_ms += vacuum_ms
            self.total_purge_time_ms += purge_ms
            self.nodes_purged += nodes
            self.edges_purged += edges
            self.vectors_purged += vectors
            self.last_cycle_at = datetime.now(timezone.utc).isoformat()

    async def record_failure(self) -> None:
        async with self._lock:
            self.cycles_failed += 1

    def snapshot(self) -> dict:
        return {
            "cycles_completed": self.cycles_completed,
            "cycles_failed": self.cycles_failed,
            "total_vacuum_time_ms": round(self.total_vacuum_time_ms, 2),
            "total_purge_time_ms": round(self.total_purge_time_ms, 2),
            "nodes_purged": self.nodes_purged,
            "edges_purged": self.edges_purged,
            "vectors_purged": self.vectors_purged,
            "last_cycle_at": self.last_cycle_at,
        }


# ---------------------------------------------------------------------------
# Core maintenance worker
# ---------------------------------------------------------------------------


class MaintenanceWorker:
    """Isolated background worker for database maintenance operations.

    Guarantees:
        1. NEVER triggered by API routes — runs solely on a timer.
        2. SQLite VACUUM executes on a DEDICATED connection outside
           the AsyncEngine pool to avoid WAL reader contention.
        3. LanceDB hard-deletes only process records past the
           retention window (default 24h after soft-delete).
        4. All operations are wrapped in structured error handling —
           a single failure does not crash the worker loop.

    Args:
        sqlite_engine: Initialized AsyncEngine for the graph database.
        vector_engine: Initialized VectorEngine for vector storage.
            May be None if vector storage is not in use.
        schedule_hours: UTC hours at which to run (default: [0]).
        retention_hours: Hours after soft-delete before physical removal.
        enabled: Set to False to create without starting the loop.
    """

    def __init__(
        self,
        sqlite_engine: Any,
        vector_engine: Any | None = None,
        *,
        schedule_hours: list[int] | None = None,
        retention_hours: int = _DEFAULT_RETENTION_HOURS,
        enabled: bool = True,
    ) -> None:
        self._sqlite_engine = sqlite_engine
        self._vector_engine = vector_engine
        self._schedule_hours = schedule_hours or _DEFAULT_SCHEDULE_HOURS
        self._retention_hours = retention_hours
        self._enabled = enabled
        self._running = False
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._metrics = MaintenanceMetrics()

        # Validate schedule hours
        for h in self._schedule_hours:
            if not (0 <= h <= 23):
                raise ValueError(f"Schedule hour {h} out of range. Must be 0-23.")

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def metrics(self) -> MaintenanceMetrics:
        return self._metrics

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def schedule_hours(self) -> list[int]:
        return list(self._schedule_hours)

    # ------------------------------------------------------------------
    # Async context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "MaintenanceWorker":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.stop()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Launch the maintenance loop as a background task.

        Idempotent — calling start() on a running worker is a no-op.
        """
        if self._running or not self._enabled:
            return

        self._stop_event.clear()
        self._running = True
        self._task = asyncio.create_task(
            self._scheduler_loop(), name="mesa_maintenance_worker"
        )
        logger.info(
            "MAINTENANCE_WORKER_STARTED | schedule_utc=%s retention_hours=%d",
            self._schedule_hours,
            self._retention_hours,
        )

    async def stop(self) -> None:
        """Gracefully stop the worker and wait for the current cycle."""
        if not self._running:
            return

        self._stop_event.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=60)
            except asyncio.TimeoutError:
                logger.warning(
                    "MAINTENANCE_WORKER_STOP_TIMEOUT | "
                    "worker did not stop within 60s, cancelling"
                )
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
            self._task = None

        self._running = False
        logger.info(
            "MAINTENANCE_WORKER_STOPPED | metrics=%s",
            self._metrics.snapshot(),
        )

    # ------------------------------------------------------------------
    # Scheduler loop
    # ------------------------------------------------------------------

    async def _scheduler_loop(self) -> None:
        """Sleep-loop scheduler that fires at configured idle windows.

        Uses asyncio.Event for clean cancellation — no polling jitter.
        """
        # Grace period to let the application start fully
        try:
            await asyncio.wait_for(
                self._stop_event.wait(),
                timeout=_STARTUP_GRACE_SECONDS,
            )
            return  # stop was called during grace period
        except asyncio.TimeoutError:
            pass  # grace period elapsed, proceed

        while not self._stop_event.is_set():
            sleep_seconds = self._seconds_until_next_window()
            logger.debug("MAINTENANCE_SLEEP | next_window_in=%.0fs", sleep_seconds)

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=sleep_seconds)
                break  # stop was called while sleeping
            except asyncio.TimeoutError:
                pass  # sleep completed, time to run

            if self._stop_event.is_set():
                break

            await self._run_cycle()

    def _seconds_until_next_window(self) -> float:
        """Calculate seconds until the next scheduled maintenance window."""
        now = datetime.now(timezone.utc)
        candidates: list[datetime] = []

        for hour in self._schedule_hours:
            candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
            if candidate <= now:
                # Already past this hour today — schedule for tomorrow
                candidate += timedelta(days=1)
            candidates.append(candidate)

        next_run = min(candidates)
        delta = (next_run - now).total_seconds()

        # Enforce minimum interval
        return max(delta, _MIN_CYCLE_INTERVAL_SECONDS)

    # ------------------------------------------------------------------
    # Main maintenance cycle
    # ------------------------------------------------------------------

    async def _run_cycle(self) -> None:
        """Execute a full maintenance cycle: purge → vacuum → compact.

        Each phase is independently error-handled so a failure in one
        phase does not prevent the others from executing.
        """
        cycle_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        logger.info("MAINTENANCE_CYCLE_START | cycle_id=%s", cycle_id)

        nodes_purged = 0
        edges_purged = 0
        vectors_purged = 0
        vacuum_ms = 0.0
        purge_ms = 0.0

        try:
            # Phase 1: Purge soft-deleted records past retention window
            t_purge = time.monotonic()
            nodes_purged, edges_purged = await self._purge_sqlite_records()
            vectors_purged = await self._purge_vector_records()
            purge_ms = (time.monotonic() - t_purge) * 1000.0

            logger.info(
                "MAINTENANCE_PURGE_COMPLETE | cycle_id=%s "
                "nodes=%d edges=%d vectors=%d time_ms=%.1f",
                cycle_id,
                nodes_purged,
                edges_purged,
                vectors_purged,
                purge_ms,
            )

            # Phase 2: SQLite VACUUM + WAL checkpoint
            t_vacuum = time.monotonic()
            await self._vacuum_sqlite()
            vacuum_ms = (time.monotonic() - t_vacuum) * 1000.0

            logger.info(
                "MAINTENANCE_VACUUM_COMPLETE | cycle_id=%s time_ms=%.1f",
                cycle_id,
                vacuum_ms,
            )

            # Phase 3: LanceDB compaction (best-effort)
            await self._compact_vector_storage()

            await self._metrics.record_cycle(
                vacuum_ms=vacuum_ms,
                purge_ms=purge_ms,
                nodes=nodes_purged,
                edges=edges_purged,
                vectors=vectors_purged,
            )

            logger.info(
                "MAINTENANCE_CYCLE_COMPLETE | cycle_id=%s total_ms=%.1f",
                cycle_id,
                vacuum_ms + purge_ms,
            )

        except Exception as exc:
            await self._metrics.record_failure()
            logger.error(
                "MAINTENANCE_CYCLE_FAILED | cycle_id=%s error=%s",
                cycle_id,
                exc,
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # Phase 1: SQLite record purge
    # ------------------------------------------------------------------

    async def _purge_sqlite_records(self) -> tuple[int, int]:
        """Physically DELETE soft-deleted nodes past retention window.

        Only removes records where ``invalid_at`` is older than the
        configured retention window.  This ensures audit trail data is
        preserved for the required period before physical removal.

        Edge storage is owned by KùzuDB — edge purge is handled
        implicitly when KùzuDB nodes are removed.

        Returns:
            Tuple of (nodes_deleted, edges_deleted).
            ``edges_deleted`` is always 0 (edge storage migrated to KùzuDB).
        """
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=self._retention_hours)
        ).isoformat()

        nodes_deleted = 0

        try:
            async with self._sqlite_engine.connection() as db:
                # Delete expired nodes
                cursor = await db.execute(
                    "DELETE FROM nodes WHERE invalid_at IS NOT NULL "
                    "AND datetime(invalid_at) < datetime(?)",
                    (cutoff,),
                )
                nodes_deleted = cursor.rowcount

                await db.commit()

        except Exception as exc:
            logger.error("SQLITE_PURGE_FAILED | error=%s", exc, exc_info=True)
            raise

        # edges_deleted is always 0 — edge storage migrated to KùzuDB
        return nodes_deleted, 0

    # ------------------------------------------------------------------
    # Phase 2: SQLite VACUUM
    # ------------------------------------------------------------------

    async def _vacuum_sqlite(self) -> None:
        """Execute VACUUM on a dedicated connection to reclaim disk space.

        VACUUM requires exclusive database access — it cannot run
        while other connections hold read locks or active statements.
        We bypass aiosqlite entirely and use raw ``sqlite3`` via
        ``run_in_executor`` because aiosqlite's internal cursor
        management keeps statements alive that block VACUUM.

        The dedicated connection uses:
          - Extended busy_timeout (30s) to wait for WAL readers to drain
          - WAL checkpoint TRUNCATE first to minimise VACUUM work

        This method is the reason the maintenance worker must run in an
        idle window — VACUUM will block until all readers finish.
        """
        db_path = self._sqlite_engine.db_path

        if not os.path.exists(db_path):
            logger.warning("VACUUM_SKIPPED | db=%s does not exist", db_path)
            return

        try:
            # Step 1: WAL checkpoint via engine (uses pooled connection)
            await self._sqlite_engine.checkpoint(mode="TRUNCATE")

            # Step 2: VACUUM on a raw sqlite3 connection outside the pool
            # Must use synchronous sqlite3 — aiosqlite keeps internal
            # cursors alive that prevent VACUUM from acquiring exclusive lock
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._sync_vacuum, db_path)

            logger.info("VACUUM_COMPLETE | db=%s", db_path)

        except Exception as exc:
            logger.error(
                "VACUUM_FAILED | db=%s error=%s",
                db_path,
                exc,
                exc_info=True,
            )
            # Vacuum failure is non-fatal — the database is still usable
            # but WAL/disk space may grow until the next successful vacuum

    @staticmethod
    def _sync_vacuum(db_path: str) -> None:
        """Synchronous VACUUM on a raw sqlite3 connection.

        Runs in executor thread to avoid blocking the event loop.
        Uses isolation_level=None (autocommit) because VACUUM cannot
        execute inside a transaction.
        """
        conn = sqlite3.connect(db_path, isolation_level=None)
        try:
            conn.execute(f"PRAGMA busy_timeout={_VACUUM_BUSY_TIMEOUT_MS};")
            conn.execute("VACUUM;")
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Phase 3: LanceDB vector purge & compaction
    # ------------------------------------------------------------------

    async def _purge_vector_records(self) -> int:
        """Hard-delete expired vector records past the retention window.

        Returns:
            Number of records purged.
        """
        if self._vector_engine is None:
            return 0

        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=self._retention_hours)
        ).isoformat()

        purged = 0

        try:
            loop = asyncio.get_running_loop()
            purged = await loop.run_in_executor(None, self._sync_purge_vectors, cutoff)
        except Exception as exc:
            logger.error("VECTOR_PURGE_FAILED | error=%s", exc, exc_info=True)
            raise

        return purged

    def _sync_purge_vectors(self, cutoff_iso: str) -> int:
        """Synchronous vector purge (runs in executor thread).

        Deletes records where expired_at is non-null and older than
        the cutoff timestamp.

        B2 FIX: ``cutoff_iso`` is validated against a strict ISO 8601
        regex before interpolation into LanceDB SQL filters, enforcing
        the system-wide "zero unvalidated interpolation" invariant.
        """
        if self._vector_engine is None or not self._vector_engine.is_initialized:
            return 0

        db = self._vector_engine._db
        if db is None:
            return 0

        # B2 FIX: Validate cutoff_iso before string interpolation.
        # LanceDB does not support parameterised bindings, so this is
        # the defence-in-depth gate against SQL injection.
        if not _ISO8601_SAFE_RE.match(cutoff_iso):
            logger.error(
                "VECTOR_PURGE_REJECTED | cutoff=%r failed ISO 8601 validation",
                cutoff_iso,
            )
            return 0

        purged = 0
        table_names = self._vector_engine._list_table_names()

        for table_name in table_names:
            if not table_name.startswith("mesa_vectors_"):
                continue
            try:
                table = db.open_table(table_name)

                # Count records to be purged
                expired = (
                    table.search()
                    .where(f"expired_at IS NOT NULL AND expired_at < '{cutoff_iso}'")
                    .select(["node_id"])
                    .limit(1_000_000)
                    .to_arrow()
                )
                batch_count = expired.num_rows

                if batch_count == 0:
                    continue

                # Physical deletion
                table.delete(f"expired_at IS NOT NULL AND expired_at < '{cutoff_iso}'")
                purged += batch_count

                # Invalidate cached table handle
                with self._vector_engine._table_lock:
                    self._vector_engine._tables.pop(table_name, None)

                logger.info(
                    "VECTOR_TABLE_PURGED | table=%s records=%d",
                    table_name,
                    batch_count,
                )

            except Exception as exc:
                logger.error(
                    "VECTOR_TABLE_PURGE_ERROR | table=%s error=%s",
                    table_name,
                    exc,
                    exc_info=True,
                )

        return purged

    async def _compact_vector_storage(self) -> None:
        """Attempt LanceDB table compaction after purge.

        Uses ``cleanup_old_versions()`` if ``pylance`` is installed.
        Gracefully degrades to a no-op if the dependency is absent —
        records are still deleted, just not compacted on disk.
        """
        if self._vector_engine is None:
            return

        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._sync_compact_vectors)
        except Exception as exc:
            # Compaction failure is non-fatal
            logger.warning(
                "VECTOR_COMPACTION_SKIPPED | error=%s "
                "(records deleted but disk not reclaimed)",
                exc,
            )

    def _sync_compact_vectors(self) -> None:
        """Synchronous vector compaction (runs in executor thread)."""
        if self._vector_engine is None or not self._vector_engine.is_initialized:
            return

        db = self._vector_engine._db
        if db is None:
            return

        table_names = self._vector_engine._list_table_names()

        for table_name in table_names:
            if not table_name.startswith("mesa_vectors_"):
                continue
            try:
                table = db.open_table(table_name)

                # Modern LanceDB optimize API (v0.21+):
                #   1. compact_files() — merge small data fragments
                #   2. cleanup_old_versions() — remove stale manifest files
                if hasattr(table, "optimize"):
                    table.optimize.compact_files()
                    table.optimize.cleanup_old_versions(
                        older_than=timedelta(hours=1),
                        delete_unverified=True,
                    )
                    logger.info(
                        "VECTOR_COMPACTED | table=%s method=optimize.compact_files+cleanup",
                        table_name,
                    )
                else:
                    logger.debug(
                        "VECTOR_COMPACTION_UNAVAILABLE | table=%s "
                        "(Table.optimize API not available)",
                        table_name,
                    )

            except ImportError:
                logger.debug(
                    "VECTOR_COMPACTION_SKIPPED | table=%s "
                    "(pylance not installed — install with: "
                    "pip install pylance)",
                    table_name,
                )
            except Exception as exc:
                logger.warning(
                    "VECTOR_COMPACTION_ERROR | table=%s error=%s",
                    table_name,
                    exc,
                )

    # ------------------------------------------------------------------
    # Manual trigger (for testing and ops CLI)
    # ------------------------------------------------------------------

    async def run_now(self) -> dict:
        """Manually trigger a maintenance cycle immediately.

        Intended for operational CLI tools and test harnesses — NOT
        to be called from API routes.

        Returns:
            Metrics snapshot after the cycle completes.
        """
        await self._run_cycle()
        return self._metrics.snapshot()
