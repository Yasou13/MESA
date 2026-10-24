import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mesa_memory.config import config
from mesa_memory.consolidation.loop import (
    ConsolidationLoop,
    start_tier3_deferred_worker,
)
from mesa_memory.consolidation.validator import Tier3ValidationError


@pytest.mark.asyncio
async def test_loop_exceptions():
    dao = MagicMock()
    dao.get_memories = AsyncMock(side_effect=[[{"id": "1"}], asyncio.CancelledError()])

    loop = ConsolidationLoop(dao, MagicMock(), MagicMock(), MagicMock(), MagicMock())
    loop.run_batch = AsyncMock()
    with patch.object(config, "consolidation_idle_timeout", 0):
        await loop.start()

        dao.get_memories = AsyncMock(
            side_effect=[Exception("test"), asyncio.CancelledError()]
        )
        await loop.start()


@pytest.mark.asyncio
async def test_loop_circuit_breaker_open():
    from tenacity import RetryError, wait_none

    loop = ConsolidationLoop(
        MagicMock(), MagicMock(), MagicMock(), MagicMock(), MagicMock()
    )
    with patch("mesa_memory.consolidation.loop.llm_circuit_breaker") as cb:
        cb.is_open = True
        with patch.object(loop._validate_with_timeout.retry, "wait", wait_none()):
            with pytest.raises(RetryError):
                await loop._validate_with_timeout({})


@pytest.mark.asyncio
async def test_loop_run_batch_exceptions():
    loop = ConsolidationLoop(
        MagicMock(), MagicMock(), MagicMock(), MagicMock(), MagicMock()
    )
    loop._validate_with_timeout = AsyncMock(
        side_effect=Tier3ValidationError("invalid", "test")
    )
    loop.dead_letter_queue.aappend = AsyncMock()
    await loop.run_batch([{"tier3_deferred": True}])

    loop._validate_with_timeout = AsyncMock(side_effect=asyncio.TimeoutError())
    await loop.run_batch([{"tier3_deferred": True}])

    loop._validate_with_timeout = AsyncMock(return_value={"decision": None})
    loop.validator = MagicMock()
    loop.validator.validate = AsyncMock(return_value=True)
    await loop.run_batch([{"tier3_deferred": True}])

    loop._validate_with_timeout = AsyncMock(return_value={"decision": None})
    loop.validator.validate = AsyncMock(return_value=False)
    await loop.run_batch([{"tier3_deferred": True}])

    import sqlite3

    loop.dao.invalidate_node = AsyncMock(
        side_effect=sqlite3.OperationalError("database is locked")
    )
    loop._validate_with_timeout = AsyncMock(return_value={"decision": False})
    await loop.run_batch([{"tier3_deferred": True}])

    loop.dao.invalidate_node = AsyncMock(side_effect=Exception("generic error"))
    await loop.run_batch([{"tier3_deferred": True}])


@pytest.mark.asyncio
async def test_start_tier3_deferred_worker():
    dao = MagicMock()
    dao.get_memories = AsyncMock(
        side_effect=[
            [{"id": "1", "tier3_deferred": True}],
            Exception("test_err"),
            asyncio.CancelledError(),
        ]
    )
    dao.mark_consolidated = AsyncMock()
    loop_instance = ConsolidationLoop(
        dao, MagicMock(), MagicMock(), MagicMock(), MagicMock()
    )
    loop_instance.run_batch = AsyncMock()

    await start_tier3_deferred_worker(dao, loop_instance, sleep_interval=0)

    # Test mark_consolidated exception
    dao.get_memories = AsyncMock(
        side_effect=[[{"id": "1", "tier3_deferred": True}], asyncio.CancelledError()]
    )
    dao.mark_consolidated = AsyncMock(side_effect=Exception("db err"))
    await start_tier3_deferred_worker(dao, loop_instance, sleep_interval=0)
