import os
import shutil
import uuid

import pytest
import pytest_asyncio

from mesa_mcp.gateway.middleware import ControlPlaneMiddleware
from mesa_storage.sqlite_engine import AsyncEngine

TEST_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    ".test_storage_tmp",
    "control_middleware",
)


@pytest.fixture(autouse=True)
def _clean_test_dir():
    os.makedirs(TEST_DIR, exist_ok=True)
    yield
    shutil.rmtree(TEST_DIR, ignore_errors=True)


@pytest_asyncio.fixture
async def middleware():
    db_path = os.path.join(TEST_DIR, f"mw_{uuid.uuid4().hex[:8]}.db")
    eng = AsyncEngine(db_path, max_connections=4)
    await eng.initialize()

    import alembic.command
    import alembic.config

    alembic_cfg = alembic.config.Config(
        "packages/mesa-memory/src/mesa_storage/alembic.ini"
    )
    alembic_cfg.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{db_path}")
    alembic.command.upgrade(alembic_cfg, "head")
    await eng.close()

    mw = ControlPlaneMiddleware(db_path=db_path)
    yield mw
    await mw.close()


@pytest.mark.asyncio
async def test_middleware_allow(middleware):
    async def mock_handler(args):
        return {"result": "ok"}

    # mesa_search_memory defaults to ALLOW in our PolicyEngine
    result = await middleware.execute_tool(
        "mesa_search_memory", {"query": "test"}, mock_handler
    )
    assert result == {"result": "ok"}


@pytest.mark.asyncio
async def test_middleware_require_approval(middleware):
    async def mock_handler(args):
        return {"result": "ok"}

    # mesa_store_memory defaults to REQUIRE_APPROVAL
    result = await middleware.execute_tool(
        "mesa_store_memory", {"content": "test"}, mock_handler
    )
    assert result.get("status") == "PENDING_APPROVAL"
    assert "approval_id" in result
