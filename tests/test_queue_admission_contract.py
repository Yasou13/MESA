"""WAVE-004B admission/backpressure contracts."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from mesa_memory.config import MesaConfig, QueueAdmissionPolicy
from mesa_storage.dao import (
    MemoryDAO,
    QueueOverCapacityError,
    QueueRecordTooLargeError,
    QueueUnavailableError,
)
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine


@dataclass
class Vector:
    async def get_active_node_ids(self, agent_id=None):
        return set()


@pytest.fixture
def policy() -> QueueAdmissionPolicy:
    return QueueAdmissionPolicy(
        queue_max_pending_records=3,
        queue_max_pending_bytes=4096,
        queue_max_pending_records_per_tenant=2,
        queue_max_pending_bytes_per_tenant=2048,
        queue_max_in_flight_records=2,
        queue_max_in_flight_records_per_tenant=1,
        queue_max_retry_pending_records=2,
        queue_max_retry_pending_records_per_tenant=1,
        queue_max_single_record_bytes=1024,
        queue_retry_after_seconds=2,
    )


async def _env(tmp_path):
    sql = AsyncEngine(str(tmp_path / "admission.db"), max_connections=4)
    await sql.initialize()
    await initialize_schema(sql)
    return MemoryDAO(sql, Vector()), sql


@pytest.mark.asyncio
async def test_config_rejects_unbounded_or_inconsistent_queue_limits():
    with pytest.raises(ValueError):
        QueueAdmissionPolicy(queue_max_pending_records=0)
    with pytest.raises(ValueError):
        QueueAdmissionPolicy(
            queue_max_pending_records=2,
            queue_max_pending_records_per_tenant=3,
        )
    with pytest.raises(ValueError):
        QueueAdmissionPolicy(
            queue_max_pending_bytes=100,
            queue_max_single_record_bytes=101,
        )
    with pytest.raises(ValueError):
        MesaConfig(queue_max_pending_records=0)


@pytest.mark.asyncio
async def test_concurrent_admission_enforces_per_tenant_count_and_keeps_only_durable_accepts(
    tmp_path, policy
):
    dao, sql = await _env(tmp_path)
    try:
        calls = [
            dao.admit_raw_log(
                "tenant-a",
                {"agent_id": "tenant-a", "content": f"record-{index}"},
                policy=policy,
            )
            for index in range(3)
        ]
        results = await asyncio.gather(*calls, return_exceptions=True)
        accepted = [result for result in results if isinstance(result, dict)]
        rejected = [
            result for result in results if isinstance(result, QueueOverCapacityError)
        ]
        assert len(accepted) == 2
        assert len(rejected) == 1
        assert rejected[0].scope == "tenant"
        assert all(result["admission"] == "DEFERRED" for result in accepted)
        metrics = await dao.get_queue_admission_metrics("tenant-a")
        assert metrics["global"]["records"] == 2
        assert metrics["tenant"]["records"] == 2
        assert metrics["global"]["bytes"] == metrics["tenant"]["bytes"]
    finally:
        await sql.close()


@pytest.mark.asyncio
async def test_admission_rejects_oversized_payload_without_durable_fallback(
    tmp_path, policy
):
    dao, sql = await _env(tmp_path)
    try:
        with pytest.raises(QueueRecordTooLargeError):
            await dao.admit_raw_log(
                "tenant-a",
                {"agent_id": "tenant-a", "content": "x" * 2048},
                policy=policy,
            )
        metrics = await dao.get_queue_admission_metrics("tenant-a")
        assert metrics["global"]["records"] == 0
        async with sql.connection() as db:
            async with db.execute("SELECT COUNT(*) FROM raw_logs") as cursor:
                assert (await cursor.fetchone())[0] == 0
    finally:
        await sql.close()


@pytest.mark.asyncio
async def test_admission_enforces_serialized_byte_budget(tmp_path, policy):
    dao, sql = await _env(tmp_path)
    byte_policy = policy.model_copy(
        update={
            "queue_max_pending_records": 3,
            "queue_max_pending_bytes": 300,
            "queue_max_pending_records_per_tenant": 3,
            "queue_max_pending_bytes_per_tenant": 300,
            "queue_max_single_record_bytes": 250,
        }
    )
    try:
        await dao.admit_raw_log(
            "tenant-a",
            {"agent_id": "tenant-a", "content": "x" * 150},
            policy=byte_policy,
        )
        with pytest.raises(QueueOverCapacityError) as rejected:
            await dao.admit_raw_log(
                "tenant-b",
                {"agent_id": "tenant-b", "content": "y" * 100},
                policy=byte_policy,
            )
        assert rejected.value.scope == "global"
    finally:
        await sql.close()


@pytest.mark.asyncio
async def test_admission_deduplicates_the_same_scoped_memory_type(tmp_path, policy):
    dao, sql = await _env(tmp_path)
    payload = {
        "agent_id": "tenant-a",
        "session_id": "session-a",
        "content": "MESA uses durable queues.",
        "metadata": {
            "memory_type": "architecture",
            "content_sha256": "a" * 64,
        },
    }
    try:
        first = await dao.admit_raw_log("tenant-a", payload, policy=policy)
        duplicate = await dao.admit_raw_log("tenant-a", payload, policy=policy)

        assert first["admission"] == "DEFERRED"
        assert duplicate == {
            "admission": "DEDUPLICATED",
            "log_id": first["log_id"],
            "deduplicated": True,
        }
        metrics = await dao.get_queue_admission_metrics("tenant-a")
        assert metrics["tenant"]["records"] == 1
    finally:
        await sql.close()


@pytest.mark.asyncio
async def test_router_maps_admission_rejections_to_stable_http_contracts():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from fastapi import BackgroundTasks

    from mesa_api.router import create_memory_router
    from mesa_api.schemas import MemoryInsertRequest

    class AccessControlStub:
        async def check_access(self, *_args):
            return True

    payload = MemoryInsertRequest(
        agent_id="tenant-a", session_id="session-a", content="payload"
    )
    request = SimpleNamespace()

    async def call(error):
        dao = SimpleNamespace(admit_raw_log=AsyncMock(side_effect=error))
        router = create_memory_router(
            get_dao=lambda: dao, get_access_control=lambda: AccessControlStub()  # type: ignore[return-value]
        )
        endpoint = next(
            route.endpoint
            for route in router.routes
            if route.path == "/v3/memory/insert"
        )
        return await endpoint(
            request=request,
            payload=payload,
            background_tasks=BackgroundTasks(),
            dao=dao,
        )

    capacity = await call(QueueOverCapacityError("tenant"))
    assert capacity.status_code == 503
    assert capacity.headers["retry-after"] == "5"
    assert b"queue_over_capacity" in capacity.body
    assert b'"scope":"tenant"' in capacity.body

    oversized = await call(QueueRecordTooLargeError("too large"))
    assert oversized.status_code == 413
    assert b"queue_record_too_large" in oversized.body

    unavailable = await call(QueueUnavailableError("unavailable"))
    assert unavailable.status_code == 503
    assert b"queue_unavailable" in unavailable.body


@pytest.mark.asyncio
async def test_global_count_limit_then_finalization_reopens_admission(tmp_path, policy):
    dao, sql = await _env(tmp_path)
    global_policy = QueueAdmissionPolicy(
        queue_max_pending_records=1,
        queue_max_pending_bytes=4096,
        queue_max_pending_records_per_tenant=1,
        queue_max_pending_bytes_per_tenant=2048,
        queue_max_in_flight_records=1,
        queue_max_in_flight_records_per_tenant=1,
        queue_max_retry_pending_records=1,
        queue_max_retry_pending_records_per_tenant=1,
        queue_max_single_record_bytes=1024,
        queue_retry_after_seconds=2,
    )
    try:
        accepted = await dao.admit_raw_log(
            "tenant-a", {"agent_id": "tenant-a", "content": "one"}, policy=global_policy
        )
        with pytest.raises(QueueOverCapacityError) as rejected:
            await dao.admit_raw_log(
                "tenant-b",
                {"agent_id": "tenant-b", "content": "two"},
                policy=global_policy,
            )
        assert rejected.value.scope == "global"
        async with sql.transaction() as db:
            await db.execute(
                "UPDATE dispatch_queue SET state = 'FINALIZED' WHERE queue_record_id = ?",
                (accepted["queue_record_id"],),
            )
            await db.commit()
        reopened = await dao.admit_raw_log(
            "tenant-b", {"agent_id": "tenant-b", "content": "two"}, policy=global_policy
        )
        assert reopened["admission"] == "DEFERRED"
    finally:
        await sql.close()


@pytest.mark.asyncio
async def test_tenant_count_limit_does_not_reject_another_tenant(tmp_path, policy):
    dao, sql = await _env(tmp_path)
    try:
        await dao.admit_raw_log(
            "tenant-a", {"agent_id": "tenant-a", "content": "one"}, policy=policy
        )
        await dao.admit_raw_log(
            "tenant-a", {"agent_id": "tenant-a", "content": "two"}, policy=policy
        )
        with pytest.raises(QueueOverCapacityError) as rejected:
            await dao.admit_raw_log(
                "tenant-a", {"agent_id": "tenant-a", "content": "three"}, policy=policy
            )
        assert rejected.value.scope == "tenant"
        other = await dao.admit_raw_log(
            "tenant-b", {"agent_id": "tenant-b", "content": "other"}, policy=policy
        )
        assert other["admission"] == "DEFERRED"
    finally:
        await sql.close()


@pytest.mark.asyncio
async def test_retry_and_in_flight_rows_remain_in_capacity_accounting_after_restart(
    tmp_path, policy
):
    db_path = tmp_path / "admission.db"
    dao, sql = await _env(tmp_path)
    try:
        first = await dao.admit_raw_log(
            "tenant-a", {"agent_id": "tenant-a", "content": "one"}, policy=policy
        )
        async with sql.transaction() as db:
            await db.execute(
                "UPDATE dispatch_queue SET state = 'IN_FLIGHT' WHERE queue_record_id = ?",
                (first["queue_record_id"],),
            )
            await db.commit()
        with pytest.raises(QueueOverCapacityError):
            await dao.admit_raw_log(
                "tenant-a", {"agent_id": "tenant-a", "content": "two"}, policy=policy
            )
    finally:
        await sql.close()

    restarted = AsyncEngine(str(db_path), max_connections=4)
    await restarted.initialize()
    try:
        restarted_dao = MemoryDAO(restarted, Vector())
        metrics = await restarted_dao.get_queue_admission_metrics("tenant-a")
        assert metrics["global"]["in_flight"] == 1
        async with restarted.transaction() as db:
            await db.execute("UPDATE dispatch_queue SET state = 'RETRY_PENDING'")
            await db.commit()
        metrics = await restarted_dao.get_queue_admission_metrics("tenant-a")
        assert metrics["global"]["retry_pending"] == 1
        with pytest.raises(QueueOverCapacityError):
            await restarted_dao.admit_raw_log(
                "tenant-a", {"agent_id": "tenant-a", "content": "two"}, policy=policy
            )
    finally:
        await restarted.close()


@pytest.mark.asyncio
async def test_unauthorized_router_call_does_not_reach_admission():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from fastapi import BackgroundTasks

    from mesa_api.router import create_memory_router
    from mesa_api.schemas import MemoryInsertRequest

    class AccessControlStub:
        async def check_access(self, *_args):
            return False

    dao = SimpleNamespace(admit_raw_log=AsyncMock())
    router = create_memory_router(
        get_dao=lambda: dao, get_access_control=lambda: AccessControlStub()  # type: ignore[return-value]
    )
    endpoint = next(
        route.endpoint for route in router.routes if route.path == "/v3/memory/insert"
    )
    with pytest.raises(Exception) as denied:
        await endpoint(
            request=SimpleNamespace(),
            payload=MemoryInsertRequest(
                agent_id="tenant-a", session_id="session-a", content="payload"
            ),
            background_tasks=BackgroundTasks(),
            dao=dao,
        )
    assert getattr(denied.value, "status_code", None) == 403
    dao.admit_raw_log.assert_not_awaited()


@pytest.mark.asyncio
async def test_scoped_memory_lookup_returns_only_its_requested_session():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from mesa_api.router import create_memory_router

    class AccessControlStub:
        async def check_access(self, *_args):
            return True

    dao = SimpleNamespace(
        get_raw_log=AsyncMock(
            return_value={
                "status": "DEFERRED",
                "created_at": "2026-07-24T00:00:00Z",
                "payload": {
                    "session_id": "session-a",
                    "content": "Scoped raw memory",
                    "metadata": {
                        "mesa_mcp_project_id": "mesa",
                        "memory_type": "architecture",
                        "source_file": "docs/architecture.md",
                    },
                },
            }
        ),
        get_memory_by_id=AsyncMock(
            return_value={
                "session_id": "session-a",
                "content": "Projected scoped memory",
                "node_type": "ENTITY",
                "created_at": "2026-07-24T00:00:00Z",
            }
        ),
    )
    router = create_memory_router(
        get_dao=lambda: dao, get_access_control=lambda: AccessControlStub()
    )
    endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/v3/memory/records/{memory_id}"
    )

    raw = await endpoint("raw_17", "tenant-a", "session-a", dao)
    assert raw["memory"]["content"] == "Scoped raw memory"
    assert raw["memory"]["source"] == {"file": "docs/architecture.md"}

    projected = await endpoint("node-1", "tenant-a", "session-a", dao)
    assert projected["memory"]["content"] == "Projected scoped memory"
    dao.get_memory_by_id.assert_awaited_once_with(
        "tenant-a", "node-1", session_id="session-a"
    )
