import os
import shutil
import uuid

import pytest
import pytest_asyncio

from mesa_storage.control.approval_repo import ApprovalRepository
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

    alembic_cfg = alembic.config.Config("mesa_storage/alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{db_path}")
    alembic.command.upgrade(alembic_cfg, "head")

    yield eng
    await eng.close()


@pytest.mark.asyncio
async def test_approval_repo_list_and_expire(sqlite_engine):
    repo = ApprovalRepository(sqlite_engine)

    # Create requests
    await repo.create_approval_request(
        "appr-1", "call-1", "client-1", "WRITE", "test 1", "hash-1"
    )
    await repo.create_approval_request(
        "appr-2", "call-2", "client-2", "DELETE", "test 2", "hash-2"
    )
    await repo.create_approval_request(
        "appr-3", "call-3", "client-1", "WRITE", "test 3", "hash-3"
    )

    await repo.decide_approval("appr-1", "APPROVED", "admin")

    # Test list_approvals
    all_approvals = await repo.list_approvals()
    assert len(all_approvals) == 3

    # Test list_pending_approvals
    pending = await repo.list_pending_approvals()
    assert len(pending) == 2

    client_pending = await repo.list_pending_approvals(client_id="client-1")
    assert len(client_pending) == 1
    assert client_pending[0]["approval_id"] == "appr-3"

    # Test count_pending
    assert await repo.count_pending() == 2

    # Test expire_stale_approvals
    # Should not expire anything with default TTL since they were just created
    expired_count = await repo.expire_stale_approvals(ttl_seconds=86400)
    assert expired_count == 0

    # Expire with 0 TTL to force expiration
    expired_count = await repo.expire_stale_approvals(ttl_seconds=-1)
    assert expired_count == 2

    assert await repo.count_pending() == 0
