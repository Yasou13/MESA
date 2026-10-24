"""V4 admitted work uses verified and fenced completion receipts."""

import pytest

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
