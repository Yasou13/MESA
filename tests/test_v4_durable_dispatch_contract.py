"""V4 HTTP admission retains durable dispatch and restart guarantees."""

import asyncio
import json

import pytest

from mesa_storage.dao import MemoryDAO
from mesa_storage.sqlite_engine import AsyncEngine
from tests.v4_release_helpers import insert_memory
from tests.v4_release_helpers import v4_release_env as v4_release_env


@pytest.mark.asyncio
async def test_v4_raw_log_dispatch_is_idempotent_and_receipted(v4_release_env):
    env = v4_release_env
    admitted = await insert_memory(env)
    duplicate = await insert_memory(env)
    assert duplicate["duplicate"] is True
    assert duplicate["raw_log_id"] == admitted["raw_log_id"]
    intents = await asyncio.gather(
        env.dao.dispatch_raw_log(
            "agent-a", admitted["raw_log_id"], worker_id="dispatcher-a"
        ),
        env.dao.dispatch_raw_log(
            "agent-a", admitted["raw_log_id"], worker_id="dispatcher-b"
        ),
    )
    assert len({item["dispatch_id"] for item in intents}) == 1
    assert all(item["state"] == "RECEIPT_RECORDED" for item in intents)
    receipt = await env.dao.get_dispatch_receipt(intents[0]["dispatch_id"])
    assert receipt["agent_id"] == "agent-a"
    assert receipt["queue_record_id"] == intents[0]["queue_record_id"]
    async with env.sql.connection() as db:
        for table in (
            "raw_logs",
            "dispatch_journal",
            "dispatch_queue",
            "dispatch_receipts",
        ):
            async with db.execute(f"SELECT COUNT(*) FROM {table}") as cursor:
                assert (await cursor.fetchone())[0] == 1, table


@pytest.mark.asyncio
async def test_v4_restart_claims_pending_dispatch_without_scope_reconstruction(
    v4_release_env,
):
    env = v4_release_env
    admitted = await insert_memory(env)
    database_path = env.sql.db_path
    await env.sql.close()
    restarted = AsyncEngine(database_path, max_connections=4)
    await restarted.initialize()
    dao = MemoryDAO(restarted, env.dao._vec)
    try:
        (claim,) = await dao.claim_dispatch_queue(
            worker_id="restart-dispatcher", limit=1
        )
        assert claim["agent_id"] == "agent-a"
        assert claim["payload_reference"] == admitted["raw_log_id"]
        raw = await dao.get_raw_log("agent-a", admitted["raw_log_id"])
        payload = raw["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        assert payload["tenant_id"] == "tenant-a"
        assert payload["workspace_id"] == "workspace-a"
        assert payload["dataset_id"] == "dataset-a"
        assert payload["session_id"] == env.session_id
        receipt = await dao.get_dispatch_receipt_by_source(
            "agent-a", admitted["raw_log_id"]
        )
        assert receipt["queue_record_id"] == claim["queue_record_id"]
        assert await dao.claim_dispatch_queue(worker_id="other-worker", limit=1) == []
    finally:
        await restarted.close()


@pytest.mark.asyncio
async def test_v4_recovery_enqueues_pending_raw_log_without_scope_reconstruction(
    v4_release_env,
):
    env = v4_release_env
    admitted = await insert_memory(env)
    # Fault injection: normal V4 admission writes these transport rows atomically.
    # Retain the canonical V4 payload and exercise recovery of missing dispatch.
    async with env.sql.transaction() as db:
        await db.execute("DELETE FROM dispatch_receipts")
        await db.execute("DELETE FROM dispatch_queue")
        await db.execute("DELETE FROM dispatch_journal")
        await db.commit()
    recovered = await env.dao.recover_raw_log_dispatches(worker_id="restart-dispatcher")
    assert len(recovered) == 1
    assert recovered[0]["source_record_id"] == admitted["raw_log_id"]
    assert recovered[0]["agent_id"] == "agent-a"
    receipt = await env.dao.get_dispatch_receipt_by_source(
        "agent-a", admitted["raw_log_id"]
    )
    assert receipt["agent_id"] == "agent-a"
    (claim,) = await env.dao.claim_dispatch_queue(worker_id="worker-a", limit=1)
    assert claim["queue_record_id"] == receipt["queue_record_id"]
