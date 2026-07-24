import os
import shutil
import uuid

import pytest
import pytest_asyncio

from mesa_mcp.gateway.policy.engine import PolicyEngine
from mesa_storage.control.policy_repo import PolicyRepository
from mesa_storage.control.settings_repo import SettingsRepository
from mesa_storage.sqlite_engine import AsyncEngine

TEST_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    ".test_storage_tmp",
    "control_policy",
)


@pytest.fixture(autouse=True)
def _clean_test_dir():
    os.makedirs(TEST_DIR, exist_ok=True)
    yield
    shutil.rmtree(TEST_DIR, ignore_errors=True)


@pytest_asyncio.fixture
async def sqlite_engine():
    db_path = os.path.join(TEST_DIR, f"pol_{uuid.uuid4().hex[:8]}.db")
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
async def test_policy_crud_and_engine(sqlite_engine):
    p_repo = PolicyRepository(sqlite_engine)
    s_repo = SettingsRepository(sqlite_engine)
    engine = PolicyEngine(p_repo, s_repo)

    # 1. Test fallback to default
    effect1 = await engine.evaluate("client-1", "proj-A", "WRITE")
    assert effect1 == "REQUIRE_APPROVAL"

    # 2. Test settings default
    await s_repo.set_setting("writes.default_policy", "DENY")
    effect2 = await engine.evaluate("client-1", "proj-A", "WRITE")
    assert effect2 == "DENY"

    # 3. Test global policy rule
    await p_repo.create_rule(
        rule_id="r1",
        scope_type="GLOBAL",
        operation="WRITE",
        effect="ALLOW",
        created_by="admin",
        priority=50,
    )
    effect3 = await engine.evaluate("client-1", "proj-A", "WRITE")
    assert effect3 == "ALLOW"

    # 4. Test specific policy overriding global (higher priority)
    await p_repo.create_rule(
        rule_id="r2",
        scope_type="PROJECT",
        scope_id="proj-A",
        operation="WRITE",
        effect="DENY",
        created_by="admin",
        priority=100,
    )
    effect4 = await engine.evaluate("client-1", "proj-A", "WRITE")
    assert effect4 == "DENY"

    # Evaluate for another project should still be ALLOW
    effect5 = await engine.evaluate("client-1", "proj-B", "WRITE")
    assert effect5 == "ALLOW"
