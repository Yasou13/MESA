import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mesa_memory.consolidation.loop import ConsolidationLoop
from mesa_memory.consolidation.validator import Tier3ValidationError


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
