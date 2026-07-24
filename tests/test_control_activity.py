import os
import shutil
import uuid

import pytest
import pytest_asyncio

from mesa_storage.control.activity_repo import ActivityRecorder
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
async def test_activity_recorder_list_and_count(sqlite_engine):
    repo = ActivityRecorder(sqlite_engine)

    # Record some calls
    await repo.record_call_start(
        "call-1", "trace-A", "client-1", "tool-A", "READ", "ALLOW"
    )
    await repo.record_call_start(
        "call-2", "trace-A", "client-1", "tool-B", "WRITE", "REQUIRE_APPROVAL"
    )
    await repo.record_call_start(
        "call-3", "trace-B", "client-2", "tool-C", "READ", "ALLOW"
    )

    await repo.record_call_completion("call-1", "SUCCESS")
    await repo.record_call_completion("call-2", "PENDING_APPROVAL")
    await repo.record_call_completion("call-3", "ERROR")

    # Test list_recent_calls
    calls = await repo.list_recent_calls()
    assert len(calls) == 3

    # Test list_recent_calls filtering by client
    client_calls = await repo.list_recent_calls(client_id="client-1")
    assert len(client_calls) == 2

    # Test list_recent_calls filtering by status
    status_calls = await repo.list_recent_calls(status="SUCCESS")
    assert len(status_calls) == 1
    assert status_calls[0]["call_id"] == "call-1"

    # Test list_calls_by_trace
    trace_calls = await repo.list_calls_by_trace("trace-A")
    assert len(trace_calls) == 2

    # Test count_calls_by_status
    counts = await repo.count_calls_by_status()
    assert counts.get("SUCCESS") == 1
    assert counts.get("PENDING_APPROVAL") == 1
    assert counts.get("ERROR") == 1
