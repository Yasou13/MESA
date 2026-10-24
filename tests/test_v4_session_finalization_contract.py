"""V4 session-end requests retain durable finalization contracts."""

import pytest

from mesa_workers.ingestion_worker import process_session_finalization
from tests.v4_release_helpers import insert_memory
from tests.v4_release_helpers import v4_release_env as v4_release_env


@pytest.mark.asyncio
async def test_v4_finalization_is_idempotent_and_empty_session_completes(
    v4_release_env,
):
    env = v4_release_env
    response = await env.client.post(f"/v4/sessions/{env.session_id}/end")
    assert response.status_code == 200
    assert response.json()["status"] == "ended"
    second = await env.dao.request_session_finalization("agent-a", env.session_id)
    assert response.json()["finalization_id"] == second["finalization_id"]
    assert second["state"] == "COMPLETED"
    assert (await env.dao.get_v4_session(env.session_id))["status"] == "ENDED"


@pytest.mark.asyncio
async def test_v4_finalization_failure_is_bounded_and_stale_fence_is_rejected(
    v4_release_env,
):
    env = v4_release_env
    await insert_memory(env)
    response = await env.client.post(f"/v4/sessions/{env.session_id}/end")
    assert response.status_code == 200
    assert response.json()["status"] == "pending"
    intent = await env.dao.get_session_finalization("agent-a", env.session_id)
    assert intent["state"] == "PENDING"
    assert intent["finalization_id"] == response.json()["finalization_id"]
    claim = await env.dao.claim_session_finalization(
        "agent-a",
        env.session_id,
        worker_id="worker-a",
        lease_seconds=1,
    )
    assert claim is not None
    async with env.sql.transaction() as db:
        await db.execute(
            "UPDATE session_finalization_journal SET lease_expires_at='1970-01-01' WHERE finalization_id=?",
            (intent["finalization_id"],),
        )
        await db.commit()
    newer = await env.dao.claim_session_finalization(
        "agent-a",
        env.session_id,
        worker_id="worker-b",
    )
    assert newer is not None and newer["claim_token"] != claim["claim_token"]
    assert not await env.dao.fail_session_finalization(
        "agent-a",
        env.session_id,
        worker_id="worker-a",
        claim_token=claim["claim_token"],
        error_class="Stale",
    )
    assert await env.dao.fail_session_finalization(
        "agent-a",
        env.session_id,
        worker_id="worker-b",
        claim_token=newer["claim_token"],
        error_class="Unavailable",
    )
    assert (
        await process_session_finalization("agent-a", env.session_id, env.dao, None)
        == "BLOCKED"
    )
    final = await env.dao.get_session_finalization("agent-a", env.session_id)
    assert final["state"] == "BLOCKED" and final["attempt_count"] == 3
    assert final["last_error_class"] == "ConsolidationUnavailable"


@pytest.mark.asyncio
async def test_finalization_processes_pending_v4_log_without_legacy_extraction(
    v4_release_env,
    monkeypatch,
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from mesa_workers import ingestion_worker as worker

    env = v4_release_env
    await insert_memory(env)
    response = await env.client.post(f"/v4/sessions/{env.session_id}/end")
    assert response.status_code == 200
    assert response.json()["status"] == "pending"
    assert await env.dao.get_pending_session_raw_logs("agent-a", env.session_id)

    # Retain the investigation's sentinel after deleting the legacy function.
    extraction = AsyncMock(side_effect=AssertionError("legacy extraction called"))
    monkeypatch.setattr(worker, "_run_rebel_extraction", extraction, raising=False)

    async def accept(records):
        record = records[0]
        await env.dao.record_mutation_extraction("agent-a", record["mutation_id"], [])
        return {"accepted": [record["candidate_id"]], "rejected": [], "deferred": []}

    loop = SimpleNamespace(run_batch=AsyncMock(side_effect=accept))
    assert (
        await process_session_finalization("agent-a", env.session_id, env.dao, loop)
        == "COMPLETED"
    )
    extraction.assert_not_awaited()
    loop.run_batch.assert_awaited_once()
    assert await env.dao.get_pending_session_raw_logs("agent-a", env.session_id) == []
