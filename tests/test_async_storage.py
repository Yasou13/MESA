# MESA v0.3.0 — Phase 3: Async Storage & Graph Layer Test Suite
"""
Comprehensive tests for AsyncEngine, graph schema DDL, CRUD operations,
FTS5 lexical pre-filtering, and k-hop graph traversal.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import uuid

import pytest
import pytest_asyncio

from mesa_storage.schemas import (
    initialize_schema,
    validate_schema,
)
from mesa_storage.sqlite_engine import AsyncEngine

TEST_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    ".test_storage_tmp",
    "async_graph",
)

# Default agent_id used by insert_node/insert_edge when no explicit agent_id
# is provided.  All RLS-enforcing query functions now require this parameter.
_DEFAULT_AGENT = "__unset__"


@pytest.fixture(autouse=True)
def _clean_test_dir():
    os.makedirs(TEST_DIR, exist_ok=True)
    yield
    shutil.rmtree(TEST_DIR, ignore_errors=True)


@pytest_asyncio.fixture
async def engine():
    db_path = os.path.join(TEST_DIR, f"test_{uuid.uuid4().hex[:8]}.db")
    eng = AsyncEngine(db_path, max_connections=4)
    await eng.initialize()
    await initialize_schema(eng)
    yield eng
    await eng.close()


# ===================================================================
# AsyncEngine — lifecycle & PRAGMA enforcement
# ===================================================================


class TestAsyncEngine:
    @pytest.mark.asyncio
    async def test_initialize_creates_db_file(self):
        db_path = os.path.join(TEST_DIR, "init_test.db")
        eng = AsyncEngine(db_path)
        await eng.initialize()
        assert os.path.exists(db_path)
        await eng.close()

    @pytest.mark.asyncio
    async def test_initialize_is_idempotent(self):
        db_path = os.path.join(TEST_DIR, "idempotent.db")
        eng = AsyncEngine(db_path)
        await eng.initialize()
        await eng.initialize()  # No-op second call
        assert eng.is_initialized
        await eng.close()

    @pytest.mark.asyncio
    async def test_connection_before_init_raises(self):
        eng = AsyncEngine(os.path.join(TEST_DIR, "noinit.db"))
        with pytest.raises(RuntimeError, match="has not been initialized"):
            async with eng.connection():
                pass

    @pytest.mark.asyncio
    async def test_pragma_wal_enforced(self):
        db_path = os.path.join(TEST_DIR, "pragma.db")
        async with AsyncEngine(db_path) as eng:
            async with eng.connection() as db:
                async with db.execute("PRAGMA journal_mode;") as cur:
                    row = await cur.fetchone()
                    assert row[0] == "wal"

    @pytest.mark.asyncio
    async def test_pragma_synchronous_full_by_default(self):
        db_path = os.path.join(TEST_DIR, "sync.db")
        async with AsyncEngine(db_path) as eng:
            async with eng.connection() as db:
                async with db.execute("PRAGMA synchronous;") as cur:
                    row = await cur.fetchone()
                    # 2 = FULL. Weaker modes require an explicit
                    # test-isolated profile configuration.
                    assert row[0] == 2

    @pytest.mark.asyncio
    async def test_pragma_cache_size(self):
        db_path = os.path.join(TEST_DIR, "cache.db")
        async with AsyncEngine(db_path) as eng:
            async with eng.connection() as db:
                async with db.execute("PRAGMA cache_size;") as cur:
                    row = await cur.fetchone()
                    assert row[0] == -64000

    @pytest.mark.asyncio
    async def test_pragma_foreign_keys(self):
        db_path = os.path.join(TEST_DIR, "fk.db")
        async with AsyncEngine(db_path) as eng:
            async with eng.connection() as db:
                async with db.execute("PRAGMA foreign_keys;") as cur:
                    row = await cur.fetchone()
                    assert row[0] == 1

    @pytest.mark.asyncio
    async def test_async_context_manager(self):
        db_path = os.path.join(TEST_DIR, "ctx.db")
        async with AsyncEngine(db_path) as eng:
            assert eng.is_initialized
        assert not eng.is_initialized

    @pytest.mark.asyncio
    async def test_connection_metrics(self):
        db_path = os.path.join(TEST_DIR, "metrics.db")
        async with AsyncEngine(db_path) as eng:
            async with eng.connection() as db:
                await db.execute("SELECT 1;")
            snap = eng.metrics.snapshot()
            assert snap["connections_opened"] >= 1
            assert snap["connections_closed"] >= 1

    @pytest.mark.asyncio
    async def test_health_check_healthy(self):
        db_path = os.path.join(TEST_DIR, "health.db")
        async with AsyncEngine(db_path) as eng:
            result = await eng.health_check()
            assert result["status"] == "healthy"
            assert result["journal_mode"] == "wal"
            assert result["integrity"] == "ok"

    @pytest.mark.asyncio
    async def test_health_check_not_initialized(self):
        eng = AsyncEngine(os.path.join(TEST_DIR, "noinit_health.db"))
        result = await eng.health_check()
        assert result["status"] == "not_initialized"

    @pytest.mark.asyncio
    async def test_checkpoint(self):
        db_path = os.path.join(TEST_DIR, "ckpt.db")
        async with AsyncEngine(db_path) as eng:
            result = await eng.checkpoint("PASSIVE")
            assert "busy" in result
            assert "log_pages" in result

    @pytest.mark.asyncio
    async def test_checkpoint_invalid_mode(self):
        db_path = os.path.join(TEST_DIR, "ckpt_bad.db")
        async with AsyncEngine(db_path) as eng:
            with pytest.raises(ValueError, match="Invalid checkpoint mode"):
                await eng.checkpoint("INVALID")

    @pytest.mark.asyncio
    async def test_transaction_rollback_on_error(self):
        db_path = os.path.join(TEST_DIR, "txn.db")
        async with AsyncEngine(db_path) as eng:
            await initialize_schema(eng)
            async with eng.transaction() as db:
                await db.execute(
                    "INSERT INTO raw_logs (agent_id, payload, status) VALUES (?, ?, ?)",
                    ("agent_txn", '{"msg": "initial"}', "UNPROCESSED"),
                )
                await db.commit()

            with pytest.raises(ValueError):
                async with eng.transaction() as db:
                    await db.execute(
                        "UPDATE raw_logs SET status = ? WHERE agent_id = ?",
                        ("CHANGED", "agent_txn"),
                    )
                    raise ValueError("force rollback")

            # Verify rollback
            async with eng.connection() as db:
                async with db.execute(
                    "SELECT status FROM raw_logs WHERE agent_id = ?", ("agent_txn",)
                ) as cur:
                    rows = await cur.fetchall()
            assert len(rows) == 1
            assert rows[0][0] == "UNPROCESSED"

    @pytest.mark.asyncio
    async def test_concurrent_connections(self):
        db_path = os.path.join(TEST_DIR, "concurrent.db")
        async with AsyncEngine(db_path, max_connections=4) as eng:
            await initialize_schema(eng)

            async def _worker(idx: int) -> int:
                async with eng.transaction() as db:
                    await db.execute(
                        "INSERT INTO raw_logs (agent_id, payload, status) VALUES (?, ?, ?)",
                        ("agent_conc", f'{{"idx": {idx}}}', "UNPROCESSED"),
                    )
                    await db.commit()
                return idx

            ids = await asyncio.gather(*[_worker(i) for i in range(8)])
            assert len(ids) == 8
            async with eng.connection() as db:
                async with db.execute(
                    "SELECT id FROM raw_logs WHERE agent_id = ?", ("agent_conc",)
                ) as cur:
                    rows = await cur.fetchall()
            assert len(rows) == 8


# ===================================================================
# Schema validation
# ===================================================================


class TestSchemaValidation:
    @pytest.mark.asyncio
    async def test_schema_initializes_idempotently(self, engine):
        await initialize_schema(engine)  # Second call, no errors
        result = await validate_schema(engine)
        assert result["valid"] is True

    @pytest.mark.asyncio
    async def test_validate_schema_all_objects(self, engine):
        result = await validate_schema(engine)
        assert result["valid"] is True
        assert all(result["tables"].values())
        assert all(result["indexes"].values())
        assert all(result["triggers"].values())


