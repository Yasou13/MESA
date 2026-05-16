import asyncio
from unittest.mock import MagicMock, patch

import aiosqlite
import networkx as nx
import pytest

from mesa_memory.storage.graph.analytics import compute_pagerank, offload_expired


@pytest.mark.asyncio
async def test_compute_pagerank_empty():
    graph = nx.MultiDiGraph()
    lock = asyncio.Lock()
    result = await compute_pagerank(graph, lock)
    assert result == {}


@pytest.mark.asyncio
async def test_compute_pagerank_populated():
    graph = nx.MultiDiGraph()
    graph.add_edge("A", "B")
    lock = asyncio.Lock()
    result = await compute_pagerank(graph, lock)
    assert "A" in result
    assert "B" in result


@pytest.mark.asyncio
async def test_compute_pagerank_error():
    graph = nx.MultiDiGraph()
    graph.add_edge("A", "B")
    lock = asyncio.Lock()
    with patch("networkx.pagerank", side_effect=nx.NetworkXError):
        result = await compute_pagerank(graph, lock)
        assert result == {}


@pytest.mark.asyncio
async def test_offload_expired(tmp_path):
    db_path = str(tmp_path / "test.db")
    rocks_path = str(tmp_path / "rocks")

    async with aiosqlite.connect(db_path) as db:
        await db.execute("CREATE TABLE nodes (node_id TEXT, expired_at TEXT)")
        await db.execute("CREATE TABLE edges (edge_id TEXT, expired_at TEXT)")
        await db.execute("INSERT INTO nodes VALUES ('n1', '2023')")
        await db.execute("INSERT INTO nodes VALUES ('n2', NULL)")
        await db.execute("INSERT INTO edges VALUES ('e1', '2023')")
        await db.commit()

    with patch("mesa_memory.storage.graph.analytics.Rdict") as mock_rdict:
        mock_rdict_instance = MagicMock()
        mock_rdict.return_value = mock_rdict_instance

        count = await offload_expired(db_path, rocks_path)

        assert count == 2
        async with aiosqlite.connect(db_path) as db:
            async with db.execute("SELECT COUNT(*) FROM nodes") as c:
                row = await c.fetchone()
                assert row is not None and row[0] == 1
            async with db.execute("SELECT COUNT(*) FROM edges") as c:
                row = await c.fetchone()
                assert row is not None and row[0] == 0
