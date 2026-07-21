"""WAVE-004D durable dispatch completion receipt contracts."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from mesa_memory.config import QueueAdmissionPolicy
from mesa_storage.dao import MemoryDAO
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine


@dataclass
class Vector:
    async def get_active_node_ids(self, agent_id=None):
        return set()


async def env(tmp_path):
    sql = AsyncEngine(str(tmp_path / "completion.db"), max_connections=4)
    await sql.initialize()
    await initialize_schema(sql)
    return MemoryDAO(sql, Vector()), sql


def policy():
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


@pytest.mark.asyncio
async def test_verified_completion_is_receipted_and_fenced(tmp_path):
    dao, sql = await env(tmp_path)
    try:
        admitted = await dao.admit_raw_log(
            "tenant-a", {"agent_id": "tenant-a", "content": "one"}, policy=policy()
        )
        claim = (await dao.claim_dispatch_queue(worker_id="worker-a", limit=1))[0]
        assert (
            await dao.complete_dispatch_queue(
                claim["queue_record_id"],
                worker_id="worker-a",
                claim_token=claim["claim_token"],
                outcome="SUCCEEDED",
                side_effect_verified=True,
            )
            is True
        )
        assert (
            await dao.complete_dispatch_queue(
                claim["queue_record_id"],
                worker_id="worker-a",
                claim_token=claim["claim_token"],
                outcome="SUCCEEDED",
                side_effect_verified=True,
            )
            is False
        )
        receipt = await dao.get_dispatch_completion_receipt(admitted["queue_record_id"])
        assert (
            receipt["outcome"] == "SUCCEEDED" and receipt["side_effect_verified"] == 1
        )
    finally:
        await sql.close()


@pytest.mark.asyncio
async def test_failed_or_stale_completion_never_acknowledges(tmp_path):
    dao, sql = await env(tmp_path)
    try:
        admitted = await dao.admit_raw_log(
            "tenant-a", {"agent_id": "tenant-a", "content": "one"}, policy=policy()
        )
        claim = (await dao.claim_dispatch_queue(worker_id="worker-a", limit=1))[0]
        assert (
            await dao.complete_dispatch_queue(
                claim["queue_record_id"],
                worker_id="worker-b",
                claim_token="stale",
                outcome="SUCCEEDED",
                side_effect_verified=True,
            )
            is False
        )
        assert (
            await dao.complete_dispatch_queue(
                claim["queue_record_id"],
                worker_id="worker-a",
                claim_token=claim["claim_token"],
                outcome="FAILED",
                side_effect_verified=False,
            )
            is False
        )
        assert (
            await dao.get_dispatch_completion_receipt(admitted["queue_record_id"])
            is None
        )
    finally:
        await sql.close()
