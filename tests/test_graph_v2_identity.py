"""Graph V2 canonical entity identity contracts."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from mesa_memory.consolidation.writer import GraphWriter


@pytest.mark.asyncio
async def test_v4_triplet_uses_stable_tenant_scoped_entity_ids() -> None:
    dao = MagicMock()
    dao.insert_memory = AsyncMock(side_effect=lambda _agent, **kwargs: kwargs["node_id"])
    dao.insert_edge = AsyncMock()
    dao.graph_provider = None
    embedder = MagicMock()
    embedder.aembed = AsyncMock(return_value=[0.1, 0.2])
    embedder.EMBEDDING_DIM = 2
    writer = GraphWriter(dao=dao, embedder=embedder, human_review_queue=MagicMock())

    await writer._write_triplet(
        "tenant-a",
        "source-a",
        {"head": "Alice", "relation": "KNOWS", "tail": "Bob"},
        weight=1.0,
        mutation_id="mutation-a",
    )
    first = [call.kwargs["node_id"] for call in dao.insert_memory.await_args_list]
    dao.insert_memory.reset_mock()
    await writer._write_triplet(
        "tenant-a",
        "source-b",
        {"head": " alice ", "relation": "KNOWS", "tail": "BOB"},
        weight=1.0,
        mutation_id="mutation-b",
    )
    second = [call.kwargs["node_id"] for call in dao.insert_memory.await_args_list]

    assert first == second
    assert first[0] != first[1]
