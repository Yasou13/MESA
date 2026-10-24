import os
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from mesa_memory.consolidation.loop import (
    CircuitBreaker,
    ConsolidationLoop,
    PersistentQueue,
)


@pytest.mark.asyncio
async def test_consolidation_loop_full():
    dao = AsyncMock()
    dao.get_all_embeddings = MagicMock(return_value=[])
    dao.load_embedding_cache = MagicMock(return_value=[])
    embedder = MagicMock()
    llm_a = MagicMock()
    llm_b = MagicMock()
    obs_layer = MagicMock()

    loop = ConsolidationLoop(dao, embedder, llm_a, llm_b, obs_layer)

    # Empty batch
    dao.get_memories.return_value = []
    await loop.run_batch()

    # Non-empty batch
    dao.get_memories.return_value = [
        {"id": 1, "content": "test", "tier3_deferred": True}
    ]
    loop.router = AsyncMock()
    loop.router.validate.return_value = {"decision": "DISCARD"}
    await loop.run_batch([{"id": 1, "content": "test", "tier3_deferred": True}])

    # ADMIT
    loop.router.validate.return_value = {"decision": "ADMIT"}
    await loop.run_batch([{"id": 1, "content": "test", "tier3_deferred": True}])

    # UNCERTAIN
    loop.router.validate.return_value = {"decision": "UNCERTAIN"}
    await loop.run_batch([{"id": 1, "content": "test", "tier3_deferred": True}])




@pytest.mark.asyncio
async def test_persistent_queue(tmp_path):
    queue_path = tmp_path / "test_queue.jsonl"
    q = PersistentQueue(str(queue_path), trusted_root=str(tmp_path))
    assert await q.alen() == 0
    await q.aappend({"a": 1})
    assert await q.alen() == 1
    item = await q.agetitem(0)
    assert item["a"] == 1
    assert item["state"] == "PENDING"
    assert item["attempt_count"] == 0
    with pytest.raises(IndexError):
        _ = await q.agetitem(1)
    os.remove(queue_path)
    assert await q.alen() == 0
    with pytest.raises(IndexError):
        _ = await q.agetitem(0)


def test_circuit_breaker():
    cb = CircuitBreaker(failure_threshold=2, cooldown_period=0.1)
    assert not cb.is_open
    cb.record_failure()
    assert not cb.is_open
    cb.record_failure()
    assert cb.is_open

    time.sleep(0.15)
    assert not cb.is_open
    cb.record_success()
    assert not cb.is_open
