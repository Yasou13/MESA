"""FLOW-002 durable session finalization contract."""

from dataclasses import dataclass

import pytest

from mesa_storage.dao import MemoryDAO
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_workers.ingestion_worker import process_session_finalization


@dataclass
class Vector:
    async def get_active_node_ids(self, agent_id=None):
        return set()


async def env(tmp_path):
    sql = AsyncEngine(str(tmp_path / "finalize.sqlite"))
    await sql.initialize()
    await initialize_schema(sql)
    return MemoryDAO(sql, Vector()), sql


@pytest.mark.asyncio
async def test_finalization_is_idempotent_and_empty_session_completes(tmp_path):
    dao, sql = await env(tmp_path)
    try:
        first = await dao.request_session_finalization("tenant-a", "session-a")
        second = await dao.request_session_finalization("tenant-a", "session-a")
        assert first["finalization_id"] == second["finalization_id"]
        assert first["state"] == "COMPLETED"
    finally:
        await sql.close()


@pytest.mark.asyncio
async def test_finalization_failure_is_bounded_and_stale_fence_is_rejected(tmp_path):
    dao, sql = await env(tmp_path)
    try:
        await dao.insert_raw_log(
            "tenant-a",
            {"agent_id": "tenant-a", "session_id": "session-a", "content": "pending"},
        )
        intent = await dao.request_session_finalization("tenant-a", "session-a")
        assert intent["state"] == "PENDING"
        claim = await dao.claim_session_finalization(
            "tenant-a", "session-a", worker_id="worker-a", lease_seconds=1
        )
        assert claim is not None
        async with sql.transaction() as db:
            await db.execute(
                "UPDATE session_finalization_journal SET lease_expires_at='1970-01-01' WHERE finalization_id=?",
                (intent["finalization_id"],),
            )
            await db.commit()
        newer = await dao.claim_session_finalization(
            "tenant-a", "session-a", worker_id="worker-b"
        )
        assert newer is not None and newer["claim_token"] != claim["claim_token"]
        assert not await dao.fail_session_finalization(
            "tenant-a",
            "session-a",
            worker_id="worker-a",
            claim_token=claim["claim_token"],
            error_class="Stale",
        )
        assert await dao.fail_session_finalization(
            "tenant-a",
            "session-a",
            worker_id="worker-b",
            claim_token=newer["claim_token"],
            error_class="Unavailable",
        )
        assert (
            await process_session_finalization("tenant-a", "session-a", dao, None)
            == "BLOCKED"
        )
        final = await dao.get_session_finalization("tenant-a", "session-a")
        assert final["state"] == "BLOCKED" and final["attempt_count"] == 3
        assert final["last_error_class"] == "ConsolidationUnavailable"
    finally:
        await sql.close()
