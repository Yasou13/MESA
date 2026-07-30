"""Durable dispatch consumption must be owned by the worker, not the API."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from mesa_memory.worker_runtime import _WORKER_ID, _consume_dispatches_once


@pytest.mark.asyncio
async def test_worker_consumes_and_finalizes_a_durable_cold_path_dispatch() -> None:
    dispatch = {
        "queue_record_id": "queue-1",
        "payload_reference": 7,
        "agent_id": "tenant-a",
        "claim_token": "fence-1",
    }
    dao = SimpleNamespace(
        claim_dispatch_queue=AsyncMock(return_value=[dispatch]),
        get_raw_log=AsyncMock(return_value={"status": "processed"}),
        complete_dispatch_queue=AsyncMock(return_value=True),
    )

    with patch("mesa_memory.worker_runtime.process_cold_path", new=AsyncMock()) as run:
        result = await _consume_dispatches_once(dao, model_processing_enabled=False)

    run.assert_awaited_once_with(
        7,
        "tenant-a",
        dao,
        model_processing_enabled=False,
    )
    dao.complete_dispatch_queue.assert_awaited_once_with(
        "queue-1",
        worker_id=_WORKER_ID,
        claim_token="fence-1",
        outcome="processed",
        side_effect_verified=True,
    )
    assert result == {"claimed": 1, "finalized": 1, "retried": 0}


@pytest.mark.asyncio
async def test_long_running_dispatch_renews_its_lease_before_finalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatch = {
        "queue_record_id": "queue-1",
        "payload_reference": 7,
        "agent_id": "tenant-a",
        "claim_token": "fence-1",
    }
    release_processing = asyncio.Event()
    started_processing = asyncio.Event()

    async def slow_cold_path(*_args, **_kwargs) -> None:
        started_processing.set()
        await release_processing.wait()

    dao = SimpleNamespace(
        claim_dispatch_queue=AsyncMock(return_value=[dispatch]),
        renew_dispatch_queue_lease=AsyncMock(return_value=True),
        get_raw_log=AsyncMock(return_value={"status": "processed"}),
        complete_dispatch_queue=AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "mesa_memory.worker_runtime._DISPATCH_LEASE_RENEWAL_SECONDS", 0.02
    )

    with patch(
        "mesa_memory.worker_runtime.process_cold_path", new=slow_cold_path
    ):
        task = asyncio.create_task(
            _consume_dispatches_once(dao, model_processing_enabled=False)
        )
        await asyncio.wait_for(started_processing.wait(), timeout=1)
        for _ in range(100):
            if dao.renew_dispatch_queue_lease.await_count:
                break
            await asyncio.sleep(0.001)
        release_processing.set()
        result = await asyncio.wait_for(task, timeout=1)

    dao.renew_dispatch_queue_lease.assert_awaited_with(
        "queue-1", worker_id=_WORKER_ID, claim_token="fence-1"
    )
    assert result == {"claimed": 1, "finalized": 1, "retried": 0}


@pytest.mark.asyncio
async def test_worker_does_not_finalize_a_dispatch_after_lease_ownership_is_lost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatch = {
        "queue_record_id": "queue-1",
        "payload_reference": 7,
        "agent_id": "tenant-a",
        "claim_token": "fence-1",
    }
    dao = SimpleNamespace(
        claim_dispatch_queue=AsyncMock(return_value=[dispatch]),
        renew_dispatch_queue_lease=AsyncMock(return_value=False),
        get_raw_log=AsyncMock(),
        complete_dispatch_queue=AsyncMock(),
    )
    monkeypatch.setattr(
        "mesa_memory.worker_runtime._DISPATCH_LEASE_RENEWAL_SECONDS", 0.02
    )

    async def never_finish(*_args, **_kwargs) -> None:
        await asyncio.Future()

    with patch(
        "mesa_memory.worker_runtime.process_cold_path", new=never_finish):
        with pytest.raises(RuntimeError, match="lease ownership was lost"):
            await _consume_dispatches_once(dao, model_processing_enabled=False)

    dao.complete_dispatch_queue.assert_not_awaited()
