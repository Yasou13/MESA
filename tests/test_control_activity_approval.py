import os
import shutil
import uuid

import pytest
import pytest_asyncio

from mesa_mcp.gateway.middleware import audit_payload_metadata
from mesa_storage.control.activity_repo import ActivityRecorder
from mesa_storage.control.approval_repo import ApprovalRepository
from mesa_storage.sqlite_engine import AsyncEngine

TEST_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    ".test_storage_tmp",
    "control_act_appr",
)


@pytest.fixture(autouse=True)
def _clean_test_dir():
    os.makedirs(TEST_DIR, exist_ok=True)
    yield
    shutil.rmtree(TEST_DIR, ignore_errors=True)


@pytest_asyncio.fixture
async def sqlite_engine():
    db_path = os.path.join(TEST_DIR, f"actappr_{uuid.uuid4().hex[:8]}.db")
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
async def test_activity_recorder(sqlite_engine):
    repo = ActivityRecorder(sqlite_engine)

    await repo.record_call_start(
        call_id="call-1",
        trace_id="trace-1",
        client_id="c1",
        tool_name="mesa_store",
        operation_type="WRITE",
        decision="ALLOW",
    )

    call = await repo.get_call("call-1")
    assert call["status"] == "STARTED"

    await repo.record_call_completion(
        call_id="call-1", status="SUCCESS", duration_ms=150, memory_id="mem-123"
    )

    call2 = await repo.get_call("call-1")
    assert call2["status"] == "SUCCESS"
    assert call2["duration_ms"] == 150
    assert call2["memory_id"] == "mem-123"
    assert call2["completed_at"] is not None


@pytest.mark.asyncio
async def test_activity_audit_metadata_does_not_store_argument_values(sqlite_engine):
    repo = ActivityRecorder(sqlite_engine)
    canary = "api_key=do-not-store-this-canary"
    await repo.record_call_start(
        call_id="call-secret",
        trace_id="trace-secret",
        client_id="c1",
        tool_name="mesa_remember",
        operation_type="WRITE",
        decision="REQUIRE_APPROVAL",
        metadata=audit_payload_metadata({"content": canary, "idempotency_key": "idem"}),
    )

    call = await repo.get_call("call-secret")
    assert canary not in call["metadata_json"]
    assert "content" in call["metadata_json"]
    assert "payload_sha256" in call["metadata_json"]


@pytest.mark.asyncio
async def test_approval_repository(sqlite_engine):
    repo = ApprovalRepository(sqlite_engine)

    await repo.create_approval_request(
        approval_id="appr-1",
        call_id="call-x",
        client_id="c1",
        operation="WRITE",
        request_summary="Store system password",
        payload_hash="abcd",
    )

    req = await repo.get_approval_request("appr-1")
    assert req["status"] == "PENDING"

    await repo.decide_approval("appr-1", "APPROVED", "admin", "Looks ok")

    req2 = await repo.get_approval_request("appr-1")
    assert req2["status"] == "APPROVED"
    assert req2["decided_by"] == "admin"
    assert req2["decision_reason"] == "Looks ok"
