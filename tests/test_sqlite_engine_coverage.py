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
