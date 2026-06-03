import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mesa_memory.consolidation.lock import (
    _embed_text,
    calculate_composite_similarity,
    validate_extraction_pair,
)
from mesa_memory.consolidation.loop import (
    ConsolidationLoop,
    start_tier3_deferred_worker,
)
from mesa_memory.consolidation.validator import Tier3ValidationError
from tests.fixtures.vectors import (
    VEC_BASE_384,
    VEC_MATCH_384,
    VEC_NEAR_384,
    VEC_ORTHOGONAL_384,
)


class SyncEmbedder:
    """Mock embedder returning deterministic, text-dependent 384-dim vectors.

    Different input texts produce vectors with distinct angular directions,
    preventing the degenerate case where all pairwise cosine similarities
    collapse to 1.0 (which was the flaw with ``[0.1] * 384``).
    """

    _VECTOR_MAP = {
        "h": VEC_BASE_384,
        "t": VEC_NEAR_384,
        "r": VEC_MATCH_384,
    }

    def embed(self, text: str) -> list[float]:
        return self._VECTOR_MAP.get(text, VEC_ORTHOGONAL_384)


@pytest.mark.asyncio
async def test_embed_text_sync():
    emb = SyncEmbedder()
    res = await _embed_text("hello", emb)
    assert res.shape == (1, 384)


@pytest.mark.asyncio
async def test_calculate_composite_similarity_sync_embedder():
    emb = SyncEmbedder()
    trip_a = {"head": "h", "tail": "t", "relation": "r"}
    trip_b = {"head": "h", "tail": "t", "relation": "r"}
    sim = await calculate_composite_similarity(trip_a, trip_b, emb)
    assert sim > 0.9


@pytest.mark.asyncio
async def test_calculate_composite_similarity_cache():
    emb = SyncEmbedder()
    cache = {
        "h": VEC_BASE_384,
        "t": VEC_NEAR_384,
        "r": VEC_MATCH_384,
    }
    trip_a = {"head": "h", "tail": "t", "relation": "r"}
    trip_b = {"head": "h", "tail": "t", "relation": "r"}
    sim = await calculate_composite_similarity(trip_a, trip_b, emb, cache=cache)
    assert sim > 0.9


def test_validate_extraction_pair():
    entities_a = [{"name": "E1"}]
    relations_a = [{"source": "E1", "target": "E2", "type": "rel"}]
    entities_b = [{"name": "E1"}, {"name": "E2"}]
    relations_b = [{"source": "E1", "target": "E2", "type": "rel"}]

    res = validate_extraction_pair(entities_a, relations_a, entities_b, relations_b)
    assert "entity_similarity" in res

    res2 = validate_extraction_pair([], [], [], [])
    assert res2["entity_similarity"] == 0.0


@pytest.mark.asyncio
async def test_loop_exceptions():
    dao = MagicMock()
    dao.get_memories = AsyncMock(side_effect=[[{"id": "1"}], asyncio.CancelledError()])

    loop = ConsolidationLoop(dao, MagicMock(), MagicMock(), MagicMock(), MagicMock())
    loop.run_batch = AsyncMock()
    await loop.start()

    dao.get_memories = AsyncMock(
        side_effect=[Exception("test"), asyncio.CancelledError()]
    )
    await loop.start()


@pytest.mark.asyncio
async def test_loop_delegations():
    loop = ConsolidationLoop(
        MagicMock(), MagicMock(), MagicMock(), MagicMock(), MagicMock()
    )
    loop.triplet_extractor = MagicMock()
    loop.triplet_extractor._single_record_extract = AsyncMock(return_value="a")
    loop.triplet_extractor._retry_with_bisection = AsyncMock(return_value="b")

    with patch(
        "mesa_memory.consolidation.loop.TripletExtractor.build_records_block"
    ) as m:
        loop._build_records_block([])
        m.assert_called_once()

    await loop._single_record_extract({}, None, None)
    await loop._retry_with_bisection([], None, None, None)


@pytest.mark.asyncio
async def test_loop_extract_batch_retry_exceptions():
    from tenacity import RetryError

    loop = ConsolidationLoop(
        MagicMock(), MagicMock(), MagicMock(), MagicMock(), MagicMock()
    )
    loop.triplet_extractor.extract_batch = AsyncMock(side_effect=Exception("fail"))

    with patch("mesa_memory.consolidation.loop.llm_circuit_breaker") as cb:
        cb.is_open = False
        with patch("mesa_memory.consolidation.loop.wait_exponential", return_value=0):
            with pytest.raises(RetryError):
                await loop._extract_batch_with_retry([{"id": "1"}])
        cb.record_failure.assert_called()


@pytest.mark.asyncio
async def test_loop_circuit_breaker_open():
    from tenacity import RetryError

    loop = ConsolidationLoop(
        MagicMock(), MagicMock(), MagicMock(), MagicMock(), MagicMock()
    )
    with patch("mesa_memory.consolidation.loop.llm_circuit_breaker") as cb:
        cb.is_open = True
        with pytest.raises(RetryError):
            await loop._extract_batch_with_retry([])

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
async def test_run_batch_extraction_exceptions():
    from tenacity import Future, RetryError

    loop = ConsolidationLoop(
        MagicMock(), MagicMock(), MagicMock(), MagicMock(), MagicMock()
    )
    f = Future(1)
    f.set_exception(Exception("mock error"))
    loop._extract_batch_with_retry = AsyncMock(side_effect=RetryError(f))
    loop.dead_letter_queue.aappend = AsyncMock()
    loop.triplet_extractor.sort_by_salience = MagicMock(return_value=[{"id": "1"}])
    await loop.run_batch([{"id": "1"}])

    loop._extract_batch_with_retry = AsyncMock(side_effect=Exception("unhandled"))
    await loop.run_batch([{"id": "1"}])


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
