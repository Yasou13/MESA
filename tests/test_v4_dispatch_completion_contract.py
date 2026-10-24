"""V4 admitted work uses verified and fenced completion receipts."""

import pytest

from mesa_storage.dao import MemoryDAO
from tests.v4_release_helpers import insert_memory
from tests.v4_release_helpers import v4_release_env as v4_release_env


@pytest.mark.asyncio
async def test_v4_verified_completion_is_receipted_and_fenced(v4_release_env):
    env = v4_release_env
    admitted = await insert_memory(env)
    (claim,) = await env.dao.claim_dispatch_queue(worker_id="worker-a", limit=1)
    assert claim["payload_reference"] == admitted["raw_log_id"]
    arguments = dict(
        worker_id="worker-a",
        claim_token=claim["claim_token"],
        outcome="SUCCEEDED",
        side_effect_verified=True,
    )
    assert await env.dao.complete_dispatch_queue(claim["queue_record_id"], **arguments)
    assert not await env.dao.complete_dispatch_queue(
        claim["queue_record_id"], **arguments
    )
    receipt = await env.dao.get_dispatch_completion_receipt(claim["queue_record_id"])
    assert receipt["outcome"] == "SUCCEEDED"
    assert receipt["side_effect_verified"] == 1
    assert receipt["agent_id"] == "agent-a"


@pytest.mark.asyncio
async def test_v4_failed_or_stale_completion_never_acknowledges(v4_release_env):
    env = v4_release_env
    await insert_memory(env)
    (claim,) = await env.dao.claim_dispatch_queue(worker_id="worker-a", limit=1)
    assert not await env.dao.complete_dispatch_queue(
        claim["queue_record_id"],
        worker_id="worker-b",
        claim_token="stale",
        outcome="SUCCEEDED",
        side_effect_verified=True,
    )
    assert not await env.dao.complete_dispatch_queue(
        claim["queue_record_id"],
        worker_id="worker-a",
        claim_token=claim["claim_token"],
        outcome="FAILED",
        side_effect_verified=False,
    )
    assert (
        await env.dao.get_dispatch_completion_receipt(claim["queue_record_id"]) is None
    )


@pytest.mark.asyncio
async def test_v4_dead_letter_is_atomic_with_mutation_and_raw_log(
    v4_release_env, monkeypatch
):
    env = v4_release_env
    admitted = await insert_memory(env)
    async with env.sql.transaction() as db:
        await db.execute("UPDATE dispatch_queue SET attempt_count = 4")
        await db.commit()
    (claim,) = await env.dao.claim_dispatch_queue(worker_id="worker-a", limit=1)

    async def unavailable(*args, **kwargs):
        raise RuntimeError("mutation write unavailable")

    arguments = dict(
        worker_id="worker-a",
        claim_token=claim["claim_token"],
        outcome="DEFERRED",
        side_effect_verified=False,
    )
    with monkeypatch.context() as patch:
        patch.setattr(
            MemoryDAO, "_transition_memory_mutation_in_tx", staticmethod(unavailable)
        )
        with pytest.raises(RuntimeError, match="mutation write unavailable"):
            await env.dao.complete_dispatch_queue(claim["queue_record_id"], **arguments)
    async with env.sql.connection() as db:
        async with db.execute("SELECT state FROM dispatch_queue") as cursor:
            assert (await cursor.fetchone())[0] == "IN_FLIGHT"
    assert (await env.dao.get_mutation("agent-a", admitted["mutation_id"]))[
        "state"
    ] == "RECEIVED"
    assert (await env.dao.get_raw_log("agent-a", admitted["raw_log_id"]))[
        "status"
    ] == "DEFERRED"
    assert not await env.dao.complete_dispatch_queue(
        claim["queue_record_id"], **arguments
    )
    assert (await env.dao.get_mutation("agent-a", admitted["mutation_id"]))[
        "state"
    ] == "DEAD_LETTER"
    assert (await env.dao.get_raw_log("agent-a", admitted["raw_log_id"]))[
        "status"
    ] == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state,raw_status",
    [
        ("COMMITTED", "processed"),
        ("REJECTED", "rejected"),
        ("BLOCKED", "failed:original"),
    ],
)
async def test_v4_dead_letter_preserves_final_mutation_and_raw_log(
    v4_release_env, state, raw_status
):
    env = v4_release_env
    admitted = await insert_memory(env)
    mutation_id = admitted["mutation_id"]
    if state == "COMMITTED":
        assert await env.dao.record_mutation_extraction("agent-a", mutation_id, [])
        for stage in ["VALIDATED", "SQL_APPLIED", "VECTOR_APPLIED", "GRAPH_APPLIED"]:
            assert await env.dao.set_mutation_state("agent-a", mutation_id, stage)
    assert await env.dao.set_mutation_state("agent-a", mutation_id, state)
    await env.dao.update_raw_log_status("agent-a", admitted["raw_log_id"], raw_status)
    async with env.sql.transaction() as db:
        await db.execute("UPDATE dispatch_queue SET attempt_count = 4")
        await db.commit()
    (claim,) = await env.dao.claim_dispatch_queue(worker_id="worker-a", limit=1)
    assert not await env.dao.complete_dispatch_queue(
        claim["queue_record_id"],
        worker_id="worker-a",
        claim_token=claim["claim_token"],
        outcome="DEFERRED",
        side_effect_verified=False,
    )
    assert (await env.dao.get_mutation("agent-a", mutation_id))["state"] == state
    assert (await env.dao.get_raw_log("agent-a", admitted["raw_log_id"]))[
        "status"
    ] == raw_status
