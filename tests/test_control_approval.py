import os
import shutil
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mesa_api.routers.control.router import create_control_router
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

    alembic_cfg = alembic.config.Config(
        "packages/mesa-memory/src/mesa_storage/alembic.ini"
    )
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

    assert await repo.decide_approval("appr-1", "APPROVED", "admin")
    assert not await repo.decide_approval("appr-1", "REJECTED", "other-admin")
    assert not await repo.decide_approval("missing", "APPROVED", "admin")
    decided = await repo.get_approval_request("appr-1")
    assert decided["status"] == "APPROVED"
    assert decided["decided_by"] == "admin"

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


def test_approval_decision_uses_authenticated_actor_and_cas_outcome() -> None:
    approval_repo = MagicMock()
    approval_repo.decide_approval = AsyncMock(return_value=True)
    approval_repo.get_approval_request = AsyncMock()
    access_control = MagicMock()
    access_control.check_control_role = AsyncMock(return_value=True)
    app = FastAPI()

    @app.middleware("http")
    async def attach_admin(request, call_next):
        request.state.principal = SimpleNamespace(
            principal_id="admin-principal", status="active"
        )
        return await call_next(request)

    unused_repo = MagicMock()
    app.include_router(
        create_control_router(
            lambda: unused_repo,
            lambda: unused_repo,
            lambda: unused_repo,
            lambda: unused_repo,
            lambda: unused_repo,
            lambda: approval_repo,
            get_access_control=lambda: access_control,
        )
    )
    client = TestClient(app, raise_server_exceptions=False)

    decided = client.post(
        "/control/mcp/approvals/approval-a/decide",
        json={"status": "APPROVED", "decided_by": "forged-client-actor"},
    )
    assert decided.status_code == 200
    approval_repo.decide_approval.assert_awaited_once_with(
        "approval-a", "APPROVED", "admin-principal", None
    )

    approval_repo.decide_approval.reset_mock()
    approval_repo.decide_approval.return_value = False
    approval_repo.get_approval_request.return_value = None
    missing = client.post(
        "/control/mcp/approvals/missing/decide", json={"status": "REJECTED"}
    )
    assert missing.status_code == 404

    approval_repo.get_approval_request.return_value = {"status": "APPROVED"}
    settled = client.post(
        "/control/mcp/approvals/approval-a/decide", json={"status": "REJECTED"}
    )
    assert settled.status_code == 409
