from unittest.mock import AsyncMock, MagicMock

import pytest

from mesa_memory.retrieval.hybrid import HybridRetriever


@pytest.mark.asyncio
async def test_run_graph_spreading():
    dao = AsyncMock()
    # Simulate KùzuDB salience results
    dao.find_nodes_by_name.return_value = [{"id": "A"}]
    dao.graph_provider = AsyncMock()
    dao.graph_provider.get_cognitive_salience.return_value = [
        {"node_id": "B", "score": 0.8},
    ]
    retriever = HybridRetriever(dao, MagicMock(), MagicMock())

    res = await retriever.get_graph_results("agent1", ["NodeA"])
    assert len(res) >= 1
    assert res[0]["cmb_id"] == "B"
    assert res[0]["source"] == "graph"


@pytest.mark.asyncio
async def test_graph_spreading_no_results():
    dao = AsyncMock()
    dao.find_nodes_by_name.return_value = [{"id": "A"}]
    dao.graph_provider = AsyncMock()
    dao.graph_provider.get_cognitive_salience.return_value = []

    retriever = HybridRetriever(dao, MagicMock(), MagicMock())
    res = await retriever.get_graph_results("agent1", ["NodeA"])
    assert res == []
