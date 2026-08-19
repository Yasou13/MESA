import sqlite3
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from mesa_storage.sqlite_engine import AsyncEngine


def _upgrade_database(db_path: Path, revision: str) -> None:
    project_root = Path(__file__).resolve().parents[1]
    migration_script = (
        "from alembic import command; from alembic.config import Config; "
        "import sys; "
        "config = Config(sys.argv[1]); "
        "config.set_main_option('sqlalchemy.url', f'sqlite+pysqlite:///{sys.argv[2]}'); "
        "command.upgrade(config, sys.argv[3])"
    )
    subprocess.run(
        [
            sys.executable,
            "-c",
            migration_script,
            str(project_root / "mesa_storage" / "alembic.ini"),
            db_path.as_posix(),
            revision,
        ],
        cwd=project_root,
        check=True,
    )


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
    monkeypatch.setenv("MESA_RUNTIME_PROFILE", "test-isolated")
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

    # A weak setting cannot leak into a production profile.
    monkeypatch.setenv("MESA_RUNTIME_PROFILE", "combined")
    prod_engine = AsyncEngine(str(tmp_path / "prod2.db"))
    await prod_engine.initialize()
    assert prod_engine.synchronous_mode == "FULL"
    async with prod_engine.connection() as db:
        async with db.execute("PRAGMA synchronous;") as cursor:
            row = await cursor.fetchone()
            assert row[0] == 2
    await prod_engine.close()


def test_weak_explicit_mode_requires_test_isolated_profile(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("MESA_RUNTIME_PROFILE", "combined")
    with pytest.raises(ValueError, match="test-isolated"):
        AsyncEngine(":memory:", synchronous_mode="NORMAL")
    with pytest.raises(ValueError, match="test-isolated"):
        AsyncEngine(":memory:", synchronous_mode="OFF")


@pytest.mark.asyncio
async def test_production_environment_ignores_weak_normal_and_off_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("MESA_RUNTIME_PROFILE", "combined")
    for requested in ("NORMAL", "OFF"):
        monkeypatch.setenv("MESA_SQLITE_SYNCHRONOUS", requested)
        engine = AsyncEngine(str(tmp_path / f"production-{requested.lower()}.db"))
        await engine.initialize()
        assert engine.synchronous_mode == "FULL"
        async with engine.connection() as db:
            async with db.execute("PRAGMA synchronous") as cursor:
                assert (await cursor.fetchone())[0] == 2
        await engine.close()


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


def test_maintenance_vacuum_connection_applies_durability_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The maintenance worker's independent canonical writer cannot bypass FULL."""
    import mesa_workers.maintenance as maintenance

    db_path = tmp_path / "maintenance.db"
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE records (id INTEGER PRIMARY KEY)")
        db.execute("INSERT INTO records VALUES (1)")

    configured: list[dict[str, object]] = []
    original = maintenance.configure_sqlite_connection

    def track_configuration(connection: sqlite3.Connection, **kwargs: object) -> None:
        configured.append(kwargs)
        original(connection, **kwargs)

    monkeypatch.setattr(maintenance, "configure_sqlite_connection", track_configuration)
    maintenance.MaintenanceWorker._sync_vacuum(str(db_path))

    assert configured == [
        {"journal_mode": "", "busy_timeout_ms": maintenance._VACUUM_BUSY_TIMEOUT_MS}
    ]
    with sqlite3.connect(db_path) as db:
        assert db.execute("PRAGMA synchronous").fetchone()[0] == 2


def test_kuzu_migration_journal_is_full_without_journal_mode_redesign(
    tmp_path: Path,
) -> None:
    from mesa_storage.kuzu_migration import KuzuMigrationCoordinator

    coordinator = KuzuMigrationCoordinator(
        tmp_path / "graph.kuzu", tmp_path / "kuzu-migration-journal.db"
    )
    connection = coordinator._open_journal()
    try:
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    finally:
        connection.close()


def test_embedding_identity_connection_is_full_without_journal_mode_redesign(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import mesa_storage.embedding_identity as embedding_identity
    from mesa_storage.writer_lock import StorageWriterLock

    storage = tmp_path / "storage"
    storage.mkdir()
    database = storage / "mesa.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE system_operations (operation_kind TEXT, state TEXT, "
            "source_manifest_hash TEXT, attempt_count INTEGER)"
        )

    observed: list[tuple[int, str]] = []
    original = embedding_identity.configure_sqlite_connection

    def track_configuration(connection: sqlite3.Connection, **kwargs: object) -> None:
        original(connection, **kwargs)
        observed.append(
            (
                int(connection.execute("PRAGMA synchronous").fetchone()[0]),
                str(connection.execute("PRAGMA journal_mode").fetchone()[0]),
            )
        )

    monkeypatch.setattr(
        embedding_identity, "configure_sqlite_connection", track_configuration
    )
    with StorageWriterLock.acquire(storage, owner="embedding-test") as writer_lock:
        with pytest.raises(
            embedding_identity.EmbeddingIdentityAdoptionError,
            match="one maintenance-pending rebuild",
        ):
            embedding_identity.adopt_legacy_embedding_identity(
                trusted_root=tmp_path,
                storage_root=storage,
                writer_lock=writer_lock,
                provider="provider",
                model="model",
                version="1",
                dimension=4,
            )

    assert observed == [(2, "delete")]


def test_alembic_sqlite_connection_observes_full_at_runtime(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    database = tmp_path / "alembic-runtime.db"
    probe_script = """
from alembic import command
from alembic.config import Config
from sqlalchemy import event
from sqlalchemy.engine import Engine
import sys

observed = []

def after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    if statement.strip().upper().startswith("PRAGMA SYNCHRONOUS="):
        cursor.execute("PRAGMA synchronous")
        observed.append(int(cursor.fetchone()[0]))

event.listen(Engine, "after_cursor_execute", after_cursor_execute)
config = Config(sys.argv[1])
config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{sys.argv[2]}")
command.upgrade(config, "bb2355d0cdd4")
assert observed and set(observed) == {2}, observed
"""
    subprocess.run(
        [
            sys.executable,
            "-c",
            probe_script,
            str(project_root / "mesa_storage" / "alembic.ini"),
            database.as_posix(),
        ],
        cwd=project_root,
        check=True,
    )


def test_rebuild_manifest_validation_connection_observes_full(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import mesa_storage.rebuild_preparation as rebuild_preparation

    database = tmp_path / "rebuild-validation.db"
    _upgrade_database(database, "head")
    with sqlite3.connect(database) as connection:
        before_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]

    observed: list[tuple[int, str]] = []
    original = rebuild_preparation.configure_sqlite_connection

    def track_configuration(connection: sqlite3.Connection, **kwargs: object) -> None:
        original(connection, **kwargs)
        observed.append(
            (
                int(connection.execute("PRAGMA synchronous").fetchone()[0]),
                str(connection.execute("PRAGMA journal_mode").fetchone()[0]),
            )
        )

    monkeypatch.setattr(
        rebuild_preparation, "configure_sqlite_connection", track_configuration
    )
    manifest, source_hash = rebuild_preparation.canonical_sqlite_manifest(database)

    assert observed == [(2, before_mode)]
    assert manifest["sqlite_integrity"] == "ok"
    assert source_hash
