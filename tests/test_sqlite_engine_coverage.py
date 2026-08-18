from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from mesa_storage.sqlite_engine import AsyncEngine


@pytest.mark.asyncio
async def test_initialize_concurrent():
    """Hits line 210: initialized is set to true while waiting for lock."""
    engine = AsyncEngine(":memory:")
    engine._initialized = False

    class FakeLock:
        async def __aenter__(self):
            engine._initialized = True
            return None

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    engine._lock = FakeLock()

    await engine.initialize()
    # It should hit line 210 and return early


@pytest.mark.asyncio
async def test_initialize_non_wal():
    """Hits line 225: mode.lower() != 'wal'"""
    engine = AsyncEngine(":memory:")
    await engine.initialize()
    assert engine.is_initialized


@pytest.mark.asyncio
async def test_connection_close_error():
    """Hits line 276: await db.close() raises Exception"""
    engine = AsyncEngine(":memory:")
    await engine.initialize()

    with patch("aiosqlite.Connection.close", new_callable=AsyncMock) as mock_close:
        mock_close.side_effect = Exception("Mock close error")
        async with engine.connection():
            pass


@pytest.mark.asyncio
async def test_execute_script():
    """Hits lines 308-309: execute_script"""
    engine = AsyncEngine(":memory:")
    await engine.initialize()
    await engine.execute_script("CREATE TABLE IF NOT EXISTS test_script (id INT);")


@pytest.mark.asyncio
async def test_checkpoint_no_row():
    """Hits line 348: row is None"""
    engine = AsyncEngine(":memory:")
    await engine.initialize()

    with patch("aiosqlite.Cursor.fetchone", new_callable=AsyncMock) as mock_fetchone:
        mock_fetchone.return_value = None
        res = await engine.checkpoint("PASSIVE")
        assert res["busy"] == -1


@pytest.mark.asyncio
async def test_health_check_exception():
    """Hits lines 392-393: exception in health_check"""
    engine = AsyncEngine(":memory:")
    await engine.initialize()

    class MockConnection:
        def execute(self, *args, **kwargs):
            raise ValueError("Health check failed")

    class MockConnectionManager:
        async def __aenter__(self):
            return MockConnection()

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    with patch.object(engine, "connection", return_value=MockConnectionManager()):
        res = await engine.health_check()
        assert res["status"] == "unhealthy"
        assert res["error"] == "Health check failed"


@pytest.mark.asyncio
async def test_production_default_synchronous_is_full(tmp_path: Path):
    db_file = tmp_path / "prod.db"
    engine = AsyncEngine(str(db_file))
    await engine.initialize()
    assert engine.synchronous_mode == "FULL"

    async with engine.connection() as db:
        async with db.execute("PRAGMA synchronous;") as cursor:
            row = await cursor.fetchone()
            # 2 = FULL in SQLite PRAGMA synchronous
            assert row[0] == 2
    await engine.close()


@pytest.mark.asyncio
async def test_explicit_env_override_synchronous_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("MESA_SQLITE_SYNCHRONOUS", "NORMAL")
    db_file = tmp_path / "dev.db"
    engine = AsyncEngine(str(db_file))
    await engine.initialize()
    assert engine.synchronous_mode == "NORMAL"

    async with engine.connection() as db:
        async with db.execute("PRAGMA synchronous;") as cursor:
            row = await cursor.fetchone()
            # 1 = NORMAL
            assert row[0] == 1
    await engine.close()

    # Once env var is removed, defaults to FULL
    monkeypatch.delenv("MESA_SQLITE_SYNCHRONOUS", raising=False)
    prod_engine = AsyncEngine(str(tmp_path / "prod2.db"))
    await prod_engine.initialize()
    assert prod_engine.synchronous_mode == "FULL"
    async with prod_engine.connection() as db:
        async with db.execute("PRAGMA synchronous;") as cursor:
            row = await cursor.fetchone()
            assert row[0] == 2
    await prod_engine.close()


@pytest.mark.asyncio
async def test_rbac_and_api_keys_connection_durability(tmp_path: Path):
    from mesa_memory.security.api_keys import APIKeyStore
    from mesa_memory.security.rbac import AccessControl

    rbac_path = str(tmp_path / "rbac.db")
    ac = AccessControl(policy_path=rbac_path)
    await ac.initialize()

    async with ac._connect() as db:
        async with db.execute("PRAGMA synchronous;") as cursor:
            row = await cursor.fetchone()
            assert row[0] == 2  # FULL

    store = APIKeyStore(policy_path=rbac_path)
    await store.initialize()
    async with store._connect() as db:
        async with db.execute("PRAGMA synchronous;") as cursor:
            row = await cursor.fetchone()
            assert row[0] == 2  # FULL


@pytest.mark.asyncio
async def test_commit_reopen_and_uncommitted_rollback_semantics(tmp_path: Path):
    db_file = tmp_path / "durability_test.db"
    engine = AsyncEngine(str(db_file))
    await engine.initialize()

    async with engine.connection() as db:
        await db.execute("CREATE TABLE records (id TEXT PRIMARY KEY, val TEXT);")
        await db.execute("INSERT INTO records VALUES ('committed-1', 'durable');")
        await db.commit()
    await engine.close()

    # Reopen and verify committed data persists
    engine2 = AsyncEngine(str(db_file))
    await engine2.initialize()
    async with engine2.connection() as db:
        async with db.execute(
            "SELECT val FROM records WHERE id = 'committed-1';"
        ) as cursor:
            row = await cursor.fetchone()
            assert row[0] == "durable"

    # Transaction with uncommitted write / rollback
    try:
        async with engine2.transaction() as db:
            await db.execute("INSERT INTO records VALUES ('uncommitted-1', 'phantom');")
            raise RuntimeError("simulated crash before commit")
    except RuntimeError:
        pass

    # Verify uncommitted write is absent
    async with engine2.connection() as db:
        async with db.execute(
            "SELECT val FROM records WHERE id = 'uncommitted-1';"
        ) as cursor:
            row = await cursor.fetchone()
            assert row is None
    await engine2.close()
