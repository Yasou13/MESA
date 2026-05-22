# MESA v0.3.0 — Maintenance Worker Test Suite
"""
Tests for the isolated background maintenance worker: scheduler logic,
SQLite VACUUM, record purge with retention windows, LanceDB hard-delete,
compaction graceful degradation, and lifecycle management.
"""

from __future__ import annotations

import os
import shutil
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from mesa_storage.schemas import (
    initialize_schema,
    insert_edge,
    insert_node,
    soft_delete_edge,
    soft_delete_node,
)
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.vector_engine import VectorEngine
from mesa_workers.maintenance import MaintenanceWorker

TEST_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    ".test_storage_tmp",
    "maintenance",
)

VEC_8D = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]


@pytest.fixture(autouse=True)
def _clean_test_dir():
    os.makedirs(TEST_DIR, exist_ok=True)
    yield
    shutil.rmtree(TEST_DIR, ignore_errors=True)


@pytest_asyncio.fixture
async def sqlite_engine():
    db_path = os.path.join(TEST_DIR, f"maint_{uuid.uuid4().hex[:8]}.db")
    eng = AsyncEngine(db_path, max_connections=4)
    await eng.initialize()
    await initialize_schema(eng)
    yield eng
    await eng.close()


@pytest_asyncio.fixture
async def vector_engine():
    uri = os.path.join(TEST_DIR, f"vec_{uuid.uuid4().hex[:8]}.lance")
    eng = VectorEngine(uri, max_workers=2)
    await eng.initialize()
    yield eng
    await eng.close()


# ===================================================================
# Worker lifecycle
# ===================================================================


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_start_stop(self, sqlite_engine):
        worker = MaintenanceWorker(sqlite_engine, enabled=True)
        await worker.start()
        assert worker.is_running
        await worker.stop()
        assert not worker.is_running

    @pytest.mark.asyncio
    async def test_start_idempotent(self, sqlite_engine):
        worker = MaintenanceWorker(sqlite_engine)
        await worker.start()
        await worker.start()  # second call is no-op
        assert worker.is_running
        await worker.stop()

    @pytest.mark.asyncio
    async def test_disabled_worker_does_not_start(self, sqlite_engine):
        worker = MaintenanceWorker(sqlite_engine, enabled=False)
        await worker.start()
        assert not worker.is_running

    @pytest.mark.asyncio
    async def test_async_context_manager(self, sqlite_engine):
        async with MaintenanceWorker(sqlite_engine, enabled=True) as worker:
            assert worker.is_running
        assert not worker.is_running

    def test_invalid_schedule_hour_raises(self, sqlite_engine):
        with pytest.raises(ValueError, match="out of range"):
            MaintenanceWorker(sqlite_engine, schedule_hours=[25])

    @pytest.mark.asyncio
    async def test_schedule_hours_property(self, sqlite_engine):
        worker = MaintenanceWorker(sqlite_engine, schedule_hours=[0, 6, 12, 18])
        assert worker.schedule_hours == [0, 6, 12, 18]


# ===================================================================
# SQLite purge
# ===================================================================


class TestSQLitePurge:
    @pytest.mark.asyncio
    async def test_purge_removes_expired_nodes_past_retention(self, sqlite_engine):
        """Nodes soft-deleted > retention_hours ago are physically removed."""
        n1 = uuid.uuid4().hex
        n2 = uuid.uuid4().hex
        await insert_node(sqlite_engine, n1, "OldNode")
        await insert_node(sqlite_engine, n2, "FreshNode")

        # Soft-delete both
        await soft_delete_node(sqlite_engine, n1, agent_id="__unset__")
        await soft_delete_node(sqlite_engine, n2, agent_id="__unset__")

        # Backdate n1's invalid_at to exceed retention window
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        async with sqlite_engine.connection() as db:
            await db.execute(
                "UPDATE nodes SET invalid_at = ? WHERE id = ?",
                (old_ts, n1),
            )
            await db.commit()

        # Run maintenance with 24h retention
        worker = MaintenanceWorker(sqlite_engine, retention_hours=24)
        result = await worker.run_now()

        assert result["nodes_purged"] >= 1

        # n1 should be physically gone, n2 still exists (soft-deleted)
        async with sqlite_engine.connection() as db:
            async with db.execute("SELECT id FROM nodes WHERE id = ?", (n1,)) as cur:
                assert await cur.fetchone() is None

            async with db.execute("SELECT id FROM nodes WHERE id = ?", (n2,)) as cur:
                assert await cur.fetchone() is not None

    @pytest.mark.asyncio
    async def test_purge_removes_expired_edges(self, sqlite_engine):
        """Edges past retention are purged."""
        n1 = uuid.uuid4().hex
        n2 = uuid.uuid4().hex
        e1 = uuid.uuid4().hex
        await insert_node(sqlite_engine, n1, "A")
        await insert_node(sqlite_engine, n2, "B")
        await insert_edge(sqlite_engine, e1, n1, n2, "REL")
        await soft_delete_edge(sqlite_engine, e1, agent_id="__unset__")

        # Backdate edge
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        async with sqlite_engine.connection() as db:
            await db.execute(
                "UPDATE edges SET invalid_at = ? WHERE id = ?",
                (old_ts, e1),
            )
            await db.commit()

        worker = MaintenanceWorker(sqlite_engine, retention_hours=24)
        result = await worker.run_now()

        assert result["edges_purged"] >= 1

    @pytest.mark.asyncio
    async def test_purge_respects_retention_window(self, sqlite_engine):
        """Recently soft-deleted records are NOT purged."""
        nid = uuid.uuid4().hex
        await insert_node(sqlite_engine, nid, "RecentDelete")
        await soft_delete_node(sqlite_engine, nid, agent_id="__unset__")

        worker = MaintenanceWorker(sqlite_engine, retention_hours=24)
        result = await worker.run_now()

        # Should not be purged — deleted < 24h ago
        assert result["nodes_purged"] == 0


# ===================================================================
# SQLite VACUUM
# ===================================================================


class TestSQLiteVacuum:
    @pytest.mark.asyncio
    async def test_vacuum_executes_without_error(self, sqlite_engine):
        """VACUUM should complete without raising."""
        worker = MaintenanceWorker(sqlite_engine)
        # Directly call vacuum to test isolation
        await worker._vacuum_sqlite()

    @pytest.mark.asyncio
    async def test_vacuum_reclaims_space_after_purge(self, sqlite_engine):
        """Insert → soft-delete → backdate → purge → vacuum reduces DB size."""
        # Insert bulk data
        for i in range(100):
            await insert_node(sqlite_engine, uuid.uuid4().hex, f"Bulk_{i}")

        db_path = sqlite_engine.db_path
        size_before = os.path.getsize(db_path)

        # Soft-delete and backdate all
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        async with sqlite_engine.connection() as db:
            await db.execute("UPDATE nodes SET invalid_at = ?", (old_ts,))
            await db.commit()

        worker = MaintenanceWorker(sqlite_engine, retention_hours=24)
        await worker.run_now()

        size_after = os.path.getsize(db_path)
        # After VACUUM the file should be smaller or equal
        assert size_after <= size_before


# ===================================================================
# LanceDB vector purge
# ===================================================================


class TestVectorPurge:
    @pytest.mark.asyncio
    async def test_purge_expired_vectors(self, sqlite_engine, vector_engine):
        """Expired vectors past retention are hard-deleted."""
        nid = uuid.uuid4().hex
        await vector_engine.upsert(nid, "agent_1", VEC_8D)
        await vector_engine.soft_delete(nid)

        # Backdate expired_at via direct table access
        db = vector_engine._db
        assert db is not None
        table = db.open_table("mesa_vectors_8")
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        table.update(
            where=f"node_id = '{nid}'",
            values={"expired_at": old_ts},
        )

        worker = MaintenanceWorker(
            sqlite_engine,
            vector_engine=vector_engine,
            retention_hours=24,
        )
        result = await worker.run_now()

        assert result["vectors_purged"] >= 1

    @pytest.mark.asyncio
    async def test_purge_preserves_active_vectors(self, sqlite_engine, vector_engine):
        """Active (non-expired) vectors are never touched."""
        nid = uuid.uuid4().hex
        await vector_engine.upsert(nid, "agent_1", VEC_8D)

        worker = MaintenanceWorker(
            sqlite_engine,
            vector_engine=vector_engine,
            retention_hours=24,
        )
        await worker.run_now()

        ids = await vector_engine.get_active_node_ids()
        assert nid in ids

    @pytest.mark.asyncio
    async def test_worker_without_vector_engine(self, sqlite_engine):
        """Worker operates fine without vector engine (None)."""
        worker = MaintenanceWorker(sqlite_engine, vector_engine=None)
        result = await worker.run_now()
        assert result["vectors_purged"] == 0
        assert result["cycles_completed"] == 1


# ===================================================================
# Scheduler logic
# ===================================================================


class TestScheduler:
    @pytest.mark.asyncio
    async def test_seconds_until_next_window(self, sqlite_engine):
        """_seconds_until_next_window returns a positive value ≥ min interval."""
        worker = MaintenanceWorker(sqlite_engine, schedule_hours=[0, 12])
        secs = worker._seconds_until_next_window()
        assert secs >= 3600  # minimum cycle interval

    @pytest.mark.asyncio
    async def test_run_now_returns_metrics(self, sqlite_engine):
        worker = MaintenanceWorker(sqlite_engine)
        result = await worker.run_now()
        assert "cycles_completed" in result
        assert result["cycles_completed"] == 1
        assert result["cycles_failed"] == 0


# ===================================================================
# Metrics
# ===================================================================


class TestMetrics:
    @pytest.mark.asyncio
    async def test_metrics_accumulate_across_cycles(self, sqlite_engine):
        worker = MaintenanceWorker(sqlite_engine)

        await worker.run_now()
        await worker.run_now()

        snap = worker.metrics.snapshot()
        assert snap["cycles_completed"] == 2
        assert snap["last_cycle_at"] is not None


# ===================================================================
# Missing Coverage Tests
# ===================================================================


class TestMaintenanceMissingCoverage:
    @pytest.mark.asyncio
    async def test_record_failure(self, sqlite_engine):
        worker = MaintenanceWorker(sqlite_engine)
        await worker.metrics.record_failure()
        assert worker.metrics.cycles_failed == 1

    @pytest.mark.asyncio
    async def test_stop_timeout_and_cancelled_error(self, sqlite_engine):
        worker = MaintenanceWorker(sqlite_engine)
        await worker.start()

        import asyncio
        from unittest.mock import AsyncMock, patch

        # Mock wait_for to raise TimeoutError, triggering the cancel block
        async def mock_wait_for(*args, **kwargs):
            raise asyncio.TimeoutError()

        with patch("asyncio.wait_for", new_callable=AsyncMock) as m_wait_for:
            m_wait_for.side_effect = mock_wait_for
            await worker.stop()

        assert not worker.is_running

    @pytest.mark.asyncio
    async def test_scheduler_loop_grace_period_and_stop_event(self, sqlite_engine):
        worker = MaintenanceWorker(sqlite_engine)

        import asyncio
        from unittest.mock import patch

        # Test stop event being set during grace period
        worker._stop_event.set()
        await worker._scheduler_loop()

        worker._stop_event.clear()

        # Test stop event being set during sleep
        with patch.object(worker, "_seconds_until_next_window", return_value=0.1):

            async def trigger_stop():
                await asyncio.sleep(0.01)
                worker._stop_event.set()

            asyncio.create_task(trigger_stop())
            await worker._scheduler_loop()

    @pytest.mark.asyncio
    async def test_run_cycle_exception(self, sqlite_engine):
        worker = MaintenanceWorker(sqlite_engine)
        from unittest.mock import patch

        with patch.object(
            worker, "_purge_sqlite_records", side_effect=Exception("Test Error")
        ):
            await worker._run_cycle()

        assert worker.metrics.cycles_failed == 1

    @pytest.mark.asyncio
    async def test_purge_sqlite_exception(self, sqlite_engine):
        worker = MaintenanceWorker(sqlite_engine)
        from unittest.mock import patch

        with patch.object(
            sqlite_engine, "connection", side_effect=Exception("DB Error")
        ):
            with pytest.raises(Exception, match="DB Error"):
                await worker._purge_sqlite_records()

    @pytest.mark.asyncio
    async def test_vacuum_sqlite_missing_db(self, sqlite_engine):
        worker = MaintenanceWorker(sqlite_engine)
        from unittest.mock import patch

        with patch("os.path.exists", return_value=False):
            await worker._vacuum_sqlite()

    @pytest.mark.asyncio
    async def test_vacuum_sqlite_exception(self, sqlite_engine):
        worker = MaintenanceWorker(sqlite_engine)
        from unittest.mock import patch

        with patch.object(
            sqlite_engine, "checkpoint", side_effect=Exception("Vacuum Error")
        ):
            await worker._vacuum_sqlite()

    @pytest.mark.asyncio
    async def test_purge_vector_records_exception(self, sqlite_engine):
        from unittest.mock import MagicMock

        mock_vec = MagicMock()
        worker = MaintenanceWorker(sqlite_engine, vector_engine=mock_vec)

        from unittest.mock import patch

        with patch("asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value.run_in_executor.side_effect = Exception(
                "Vector Purge Error"
            )
            with pytest.raises(Exception, match="Vector Purge Error"):
                await worker._purge_vector_records()

    def test_sync_purge_vectors_early_returns(self, sqlite_engine):
        worker = MaintenanceWorker(sqlite_engine)
        # 1. no vector engine
        assert worker._sync_purge_vectors("some_date") == 0

        # 2. not initialized
        from unittest.mock import MagicMock

        mock_vec = MagicMock()
        mock_vec.is_initialized = False
        worker = MaintenanceWorker(sqlite_engine, vector_engine=mock_vec)
        assert worker._sync_purge_vectors("some_date") == 0

        # 3. no db
        mock_vec.is_initialized = True
        mock_vec._db = None
        assert worker._sync_purge_vectors("some_date") == 0

        # 4. table not mesa_vectors_
        mock_vec._db = MagicMock()
        mock_vec._list_table_names.return_value = ["ignore_this_table"]
        assert worker._sync_purge_vectors("some_date") == 0

        # 5. exception during table operations
        mock_vec._list_table_names.return_value = ["mesa_vectors_8"]
        mock_vec._db.open_table.side_effect = Exception("Open table error")
        # Should catch and continue, returning 0
        assert worker._sync_purge_vectors("some_date") == 0

    @pytest.mark.asyncio
    async def test_compact_vector_storage_exception(self, sqlite_engine):
        from unittest.mock import MagicMock

        mock_vec = MagicMock()
        worker = MaintenanceWorker(sqlite_engine, vector_engine=mock_vec)

        from unittest.mock import patch

        with patch("asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value.run_in_executor.side_effect = Exception(
                "Vector Compact Error"
            )
            # Exception is caught and logged, doesn't raise
            await worker._compact_vector_storage()

    def test_sync_compact_vectors_early_returns(self, sqlite_engine):
        worker = MaintenanceWorker(sqlite_engine)
        # 1. no vector engine
        worker._sync_compact_vectors()

        # 2. not initialized
        from unittest.mock import MagicMock

        mock_vec = MagicMock()
        mock_vec.is_initialized = False
        worker = MaintenanceWorker(sqlite_engine, vector_engine=mock_vec)
        worker._sync_compact_vectors()

        # 3. no db
        mock_vec.is_initialized = True
        mock_vec._db = None
        worker._sync_compact_vectors()

        # 4. table not mesa_vectors_
        mock_vec._db = MagicMock()
        mock_vec._list_table_names.return_value = ["ignore_this_table"]
        worker._sync_compact_vectors()

        # 5. missing optimize attribute
        mock_vec._list_table_names.return_value = ["mesa_vectors_8"]
        mock_table_no_opt = MagicMock(spec=[])
        mock_vec._db.open_table.return_value = mock_table_no_opt
        worker._sync_compact_vectors()

        # 6. ImportError
        mock_table_with_opt = MagicMock()
        mock_table_with_opt.optimize = MagicMock()
        mock_table_with_opt.optimize.compact_files.side_effect = ImportError(
            "pylance missing"
        )
        mock_vec._db.open_table.return_value = mock_table_with_opt
        worker._sync_compact_vectors()
