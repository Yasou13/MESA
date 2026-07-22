"""Durable dispatch consumption must be owned by the worker, not the API."""

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
