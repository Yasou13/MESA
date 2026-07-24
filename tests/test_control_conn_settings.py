import os
import shutil
import uuid

import pytest
import pytest_asyncio

from mesa_storage.control.client_repo import ClientRepository
from mesa_storage.control.connection_repo import ConnectionRepository
from mesa_storage.control.settings_repo import SettingsRepository
from mesa_storage.sqlite_engine import AsyncEngine

TEST_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    ".test_storage_tmp",
    "control_conn_repo",
)


@pytest.fixture(autouse=True)
def _clean_test_dir():
    os.makedirs(TEST_DIR, exist_ok=True)
    yield
    shutil.rmtree(TEST_DIR, ignore_errors=True)


@pytest_asyncio.fixture
async def sqlite_engine():
    db_path = os.path.join(TEST_DIR, f"conn_{uuid.uuid4().hex[:8]}.db")
    eng = AsyncEngine(db_path, max_connections=4)
    await eng.initialize()

    import alembic.command
    import alembic.config

    alembic_cfg = alembic.config.Config("mesa_storage/alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{db_path}")
    alembic.command.upgrade(alembic_cfg, "head")

    yield eng
    await eng.close()


@pytest.mark.asyncio
async def test_connection_crud(sqlite_engine):
    c_repo = ClientRepository(sqlite_engine)
    repo = ConnectionRepository(sqlite_engine)

    # Connection depends on client existing because of foreign key
    await c_repo.create_client("c1", "Client 1", "test", "u1")

    await repo.register_connection(
        connection_id="conn-1",
        client_id="c1",
        transport="stdio",
        status="CONNECTED",
        protocol_version="2024-11-05",
    )

    conn = await repo.get_connection("conn-1")
    assert conn is not None
    assert conn["status"] == "CONNECTED"
    assert conn["protocol_version"] == "2024-11-05"

    active = await repo.list_active_connections()
    assert len(active) == 1

    await repo.update_connection_status("conn-1", "DISCONNECTED")
    conn2 = await repo.get_connection("conn-1")
    assert conn2["status"] == "DISCONNECTED"
    assert conn2["disconnected_at"] is not None

    active2 = await repo.list_active_connections()
    assert len(active2) == 0


@pytest.mark.asyncio
async def test_settings_crud(sqlite_engine):
    repo = SettingsRepository(sqlite_engine)

    # initial get
    s = await repo.get_setting("non.existent")
    assert s is None

    # insert
    await repo.set_setting("mcp.enabled", True)
    await repo.set_setting("mcp.timeout", 30)

    val1 = await repo.get_setting("mcp.enabled")
    assert val1 is True
    val2 = await repo.get_setting("mcp.timeout")
    assert val2 == 30

    # update via set_setting
    await repo.set_setting("mcp.timeout", 60)
    val3 = await repo.get_setting("mcp.timeout")
    assert val3 == 60

    # get all
    all_s = await repo.get_all_settings()
    assert all_s["mcp.enabled"] is True
    assert all_s["mcp.timeout"] == 60
