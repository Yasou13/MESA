"""
Graph Analytics & Archiving — Extracted from ``networkx_provider.py``.

This module owns CPU-heavy analytical operations and cold-storage archival
that do not belong in the core CRUD provider:

- **PageRank computation**: Offloaded to a thread pool to protect the
  async event loop from CPU-bound blocking.
- **Expired record offload**: Archives soft-deleted nodes/edges from
  SQLite into RocksDB cold storage, then purges them from the hot path.

Both functions operate on external state (graph copies, DB connections,
RocksDB handles) passed in by the provider, keeping the analytics module
stateless and independently testable.
"""

import asyncio
import json
import logging
from typing import Optional

import aiosqlite
import networkx as nx
from rocksdict import Rdict

logger = logging.getLogger("MESA_Graph")


async def compute_pagerank(
    graph: nx.MultiDiGraph,
    lock: asyncio.Lock,
    personalization: Optional[dict[str, float]] = None,
    alpha: float = 0.15,
    max_iter: int = 100,
    tol: float = 1e-6,
) -> dict[str, float]:
    """Compute (Personalized) PageRank over an in-memory NetworkX graph.

    Takes a snapshot of the graph under the provided lock to avoid
    mutations during computation, then offloads the CPU-heavy
    ``nx.pagerank`` call to a thread pool.

    Args:
        graph: The live in-memory MultiDiGraph.
        lock: The provider's asyncio.Lock for safe snapshotting.
        personalization: Optional ``{node_id: weight}`` for PPR.
        alpha: Damping factor.
        max_iter: Max iterations.
        tol: Convergence tolerance.

    Returns:
        ``{node_id: score}`` mapping. Empty dict on empty graph or error.
    """
    if len(graph.nodes) == 0:
        return {}

    # Snapshot the graph to avoid mutations during computation
    async with lock:
        graph_copy = graph.copy()

    def _compute() -> dict[str, float]:
        return nx.pagerank(
            graph_copy,
            alpha=alpha,
            personalization=personalization,
            max_iter=max_iter,
            tol=tol,
        )

    # CPU-heavy: offload to thread pool to protect the event loop
    try:
        return await asyncio.to_thread(_compute)
    except nx.NetworkXError:
        return {}


async def offload_expired(
    db_path: str,
    rocks_path: str,
) -> int:
    """Archive expired nodes/edges from SQLite into RocksDB cold storage.

    Reads all soft-deleted records from the ``nodes`` and ``edges`` tables,
    writes them to RocksDB keyed by ``node:<id>`` / ``edge:<id>``, then
    purges the expired rows from SQLite.

    Args:
        db_path: Path to the aiosqlite knowledge graph database.
        rocks_path: Path to the RocksDB cold archive directory.

    Returns:
        Total number of records archived.
    """
    rocks = await asyncio.to_thread(Rdict, rocks_path)
    total_archived = 0

    try:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row

            async with db.execute(
                "SELECT * FROM nodes WHERE expired_at IS NOT NULL"
            ) as cursor:
                expired_nodes = [dict(row) async for row in cursor]

            if expired_nodes:

                def _write_nodes():
                    for node in expired_nodes:
                        rocks[f"node:{node['node_id']}"] = json.dumps(node)

                await asyncio.to_thread(_write_nodes)

                node_ids = [n["node_id"] for n in expired_nodes]
                placeholders = ",".join("?" for _ in node_ids)
                await db.execute(
                    f"DELETE FROM nodes WHERE node_id IN ({placeholders})",
                    node_ids,
                )
                total_archived += len(expired_nodes)

            async with db.execute(
                "SELECT * FROM edges WHERE expired_at IS NOT NULL"
            ) as cursor:
                expired_edges = [dict(row) async for row in cursor]

            if expired_edges:

                def _write_edges():
                    for edge in expired_edges:
                        rocks[f"edge:{edge['edge_id']}"] = json.dumps(edge)

                await asyncio.to_thread(_write_edges)

                edge_ids = [e["edge_id"] for e in expired_edges]
                placeholders = ",".join("?" for _ in edge_ids)
                await db.execute(
                    f"DELETE FROM edges WHERE edge_id IN ({placeholders})",
                    edge_ids,
                )
                total_archived += len(expired_edges)

            await db.commit()
    finally:
        await asyncio.to_thread(rocks.close)

    return total_archived
