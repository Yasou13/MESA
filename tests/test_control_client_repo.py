import os
import shutil
import uuid

import pytest
import pytest_asyncio

from mesa_storage.control.client_repo import ClientRepository
from mesa_storage.sqlite_engine import AsyncEngine

TEST_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    ".test_storage_tmp",
    "control_repo",
)


@pytest.fixture(autouse=True)
def _clean_test_dir():
    os.makedirs(TEST_DIR, exist_ok=True)
    yield
    shutil.rmtree(TEST_DIR, ignore_errors=True)


@pytest_asyncio.fixture
async def sqlite_engine():
    db_path = os.path.join(TEST_DIR, f"ctrl_{uuid.uuid4().hex[:8]}.db")
    eng = AsyncEngine(db_path, max_connections=4)
    await eng.initialize()

    import alembic.command
    import alembic.config

    # Run migrations
    alembic_cfg = alembic.config.Config(
        "packages/mesa-memory/src/mesa_storage/alembic.ini"
    )
    alembic_cfg.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{db_path}")
    alembic.command.upgrade(alembic_cfg, "head")

    yield eng
    await eng.close()


@pytest.mark.asyncio
async def test_client_crud(sqlite_engine):
    repo = ClientRepository(sqlite_engine)

    # Create
    await repo.create_client(
        client_id="test-client-1",
        display_name="Test Client",
        client_type="test",
        principal_id="user-123",
        metadata={"version": "1.0"},
    )

    # Get
    client = await repo.get_client("test-client-1")
    assert client is not None
    assert client["display_name"] == "Test Client"
    assert client["metadata"]["version"] == "1.0"

    # Update
    await repo.update_client("test-client-1", display_name="Updated Client")
    client2 = await repo.get_client("test-client-1")
    assert client2["display_name"] == "Updated Client"

    # List
    clients = await repo.list_clients()
    assert len(clients) == 1
    assert clients[0]["client_id"] == "test-client-1"

    # Missing
    assert await repo.get_client("not-found") is None


@pytest.mark.asyncio
async def test_project_bindings(sqlite_engine):
    repo = ClientRepository(sqlite_engine)

    await repo.create_client(
        client_id="test-client-1",
        display_name="Test Client",
        client_type="test",
        principal_id="user-123",
    )

    b_id = await repo.add_project_binding(
        client_id="test-client-1",
        external_project_id="proj-a",
        tenant_id="t-1",
        workspace_id="w-1",
        dataset_id="d-1",
    )
    assert b_id.startswith("bnd_")

    binding = await repo.get_project_binding("test-client-1", "proj-a")
    assert binding is not None
    assert binding["dataset_id"] == "d-1"
    assert binding["enabled"] == 1

    # Upsert test
    await repo.add_project_binding(
        client_id="test-client-1",
        external_project_id="proj-a",
        tenant_id="t-1",
        workspace_id="w-1",
        dataset_id="d-2",
    )

    binding2 = await repo.get_project_binding("test-client-1", "proj-a")
    assert binding2["dataset_id"] == "d-2"
