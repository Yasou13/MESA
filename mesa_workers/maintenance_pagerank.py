# MESA v0.6.1 — Phase 4.1: Self-Healing Graphs — Damped PageRank Quarantine Scanner
# Background worker that detects and quarantines hallucinated nodes by
# computing a Damped PageRank variant where epistemic_uncertainty acts
# as a per-edge damping penalty.
#
# Architecture:
#   - Extracts the agent-scoped subgraph from KùzuDB via MemoryDAO
#   - Builds a sparse adjacency matrix using scipy.sparse (no NetworkX)
#   - Runs power-iteration PageRank with per-edge damping from
#     epistemic_uncertainty values
#   - Nodes whose quarantine index (1.0 - normalised_pagerank) exceeds
#     the threshold are marked is_quarantined = true in KùzuDB
#
# Invariants:
#   - Zero-Trust: EVERY database read/write is parameterised with agent_id
#   - I/O Integrity: ALL synchronous KùzuDB calls and heavy math are
#     wrapped in asyncio.run_in_executor to prevent event loop blocking
#   - This worker is designed to be invoked as a periodic cron-job,
#     from the existing MaintenanceWorker, or as a standalone asyncio task
"""
Damped PageRank quarantine scanner for MESA self-healing graphs.

Periodically analyses the per-agent knowledge graph to detect nodes that
are structurally suspect — i.e. nodes whose incoming edges carry high
``epistemic_uncertainty`` (injected during ingestion from the Dual-LLM
consensus pipeline).

The algorithm computes a variant of PageRank where each edge's
contribution is damped by ``(1.0 - epistemic_uncertainty)``.  Nodes
that receive rank almost exclusively through uncertain edges will have
a low PageRank score, translating to a high **quarantine index** (QI).
Nodes exceeding the configurable ``threshold_qi`` are marked as
quarantined in KùzuDB.

Usage::

    from mesa_workers.maintenance_pagerank import run_quarantine_scan

    # Called from a background scheduler or maintenance worker:
    await run_quarantine_scan(
        agent_id="agent_alpha",
        graph_provider=kuzu_provider,
        threshold_qi=0.85,
    )

Dependencies:
    - numpy (already in pyproject.toml)
    - scipy (already in pyproject.toml)
    - No NetworkX required
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import numpy as np
from scipy import sparse

from mesa_storage.kuzu_provider import KuzuGraphProvider

logger = logging.getLogger("MESA_PageRank")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Default damping factor for PageRank (standard Google value)
_DEFAULT_DAMPING_FACTOR: float = 0.85

# Power-iteration convergence threshold
_CONVERGENCE_EPSILON: float = 1e-6

# Maximum power-iteration steps to prevent infinite loops
_MAX_ITERATIONS: int = 100

# Minimum graph size to run quarantine analysis (below this, the
# algorithm produces unstable results and quarantine is skipped)
_MIN_GRAPH_SIZE: int = 3


# ---------------------------------------------------------------------------
# Public API — async entry point
# ---------------------------------------------------------------------------


async def run_quarantine_scan(
    agent_id: str,
    graph_provider: KuzuGraphProvider,
    *,
    threshold_qi: float = 0.85,
    damping_factor: float = _DEFAULT_DAMPING_FACTOR,
) -> dict[str, Any]:
    """Detect and quarantine hallucinated nodes via Damped PageRank.

    Extracts the agent-scoped subgraph from KùzuDB, computes Damped
    PageRank where ``epistemic_uncertainty`` penalises edge contributions,
    and sets ``is_quarantined = true`` on nodes whose quarantine index
    (``1.0 - normalised_pagerank``) exceeds ``threshold_qi``.

    **Zero-Trust**: Every KùzuDB read/write is parameterised with
    ``agent_id``.

    **I/O Integrity**: All synchronous KùzuDB calls and numpy/scipy
    computation are wrapped in ``asyncio.run_in_executor`` to prevent
    FastAPI event loop blocking.

    Args:
        agent_id: Tenant isolation key — scopes all graph queries.
        graph_provider: Initialised ``KuzuGraphProvider`` instance.
        threshold_qi: Quarantine index threshold (0.0–1.0).  Nodes with
            ``qi >= threshold_qi`` are quarantined.  Default 0.85 means
            only nodes with very low structural authority are flagged.
        damping_factor: Standard PageRank damping factor (default 0.85).

    Returns:
        Summary dict with keys::

            {
                "agent_id": str,
                "total_nodes": int,
                "quarantined_count": int,
                "quarantined_ids": list[str],
                "elapsed_ms": float,
            }
    """
    t_start = time.monotonic()

    logger.info(
        "QUARANTINE_SCAN_START | agent_id=%s threshold_qi=%.3f",
        agent_id,
        threshold_qi,
    )

    # ---- 1. Extract agent-scoped subgraph (executor-offloaded) --------
    nodes, edges = await _extract_subgraph(graph_provider, agent_id)

    if len(nodes) < _MIN_GRAPH_SIZE:
        logger.info(
            "QUARANTINE_SCAN_SKIP | agent_id=%s nodes=%d reason=graph_too_small",
            agent_id,
            len(nodes),
        )
        return {
            "agent_id": agent_id,
            "total_nodes": len(nodes),
            "quarantined_count": 0,
            "quarantined_ids": [],
            "elapsed_ms": (time.monotonic() - t_start) * 1000,
        }

    # ---- 2. Compute Damped PageRank (executor-offloaded — CPU-bound) --
    loop = asyncio.get_running_loop()
    pagerank_scores = await loop.run_in_executor(
        None,
        _compute_damped_pagerank,
        nodes,
        edges,
        damping_factor,
    )

    # ---- 3. Identify quarantine candidates ----------------------------
    quarantine_ids: list[str] = []
    for node_id, pr_score in pagerank_scores.items():
        qi = 1.0 - pr_score  # quarantine index
        if qi >= threshold_qi:
            quarantine_ids.append(node_id)

    logger.info(
        "QUARANTINE_SCAN_CANDIDATES | agent_id=%s total_nodes=%d candidates=%d",
        agent_id,
        len(nodes),
        len(quarantine_ids),
    )

    # ---- 4. Mark quarantined nodes in KùzuDB (executor-offloaded) -----
    if quarantine_ids:
        await _quarantine_nodes(graph_provider, agent_id, quarantine_ids)

    elapsed_ms = (time.monotonic() - t_start) * 1000
    logger.info(
        "QUARANTINE_SCAN_DONE | agent_id=%s quarantined=%d elapsed_ms=%.1f",
        agent_id,
        len(quarantine_ids),
        elapsed_ms,
    )

    return {
        "agent_id": agent_id,
        "total_nodes": len(nodes),
        "quarantined_count": len(quarantine_ids),
        "quarantined_ids": quarantine_ids,
        "elapsed_ms": elapsed_ms,
    }


# ---------------------------------------------------------------------------
# Subgraph extraction — async wrapper around sync KùzuDB calls
# ---------------------------------------------------------------------------


async def _extract_subgraph(
    provider: KuzuGraphProvider,
    agent_id: str,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Extract all Entity nodes and Observed edges for an agent.

    All Cypher queries are parameterised with ``agent_id`` for
    Zero-Trust isolation.

    Returns:
        Tuple of (node_ids, edge_dicts) where each edge dict contains
        ``source_id``, ``target_id``, ``weight``, ``epistemic_uncertainty``.
    """
    # ---- Fetch nodes (agent-scoped) -----------------------------------
    node_rows = await provider.execute_query(
        "MATCH (n:Entity {agent_id: $agent_id}) "
        "WHERE n.is_quarantined = false OR n.is_quarantined IS NULL "
        "RETURN n.id",
        {"agent_id": agent_id},
    )
    node_ids = [row[0] for row in node_rows]

    # ---- Fetch edges (agent-scoped, triple agent_id enforcement) ------
    edge_rows = await provider.execute_query(
        "MATCH (a:Entity {agent_id: $agent_id})"
        "-[r:Observed]->"
        "(b:Entity {agent_id: $agent_id}) "
        "WHERE r.agent_id = $agent_id "
        "RETURN a.id, b.id, r.weight, r.epistemic_uncertainty",
        {"agent_id": agent_id},
    )

    edges = [
        {
            "source_id": row[0],
            "target_id": row[1],
            "weight": float(row[2]) if row[2] is not None else 1.0,
            "epistemic_uncertainty": float(row[3]) if row[3] is not None else 0.0,
        }
        for row in edge_rows
    ]

    logger.debug(
        "SUBGRAPH_EXTRACTED | agent_id=%s nodes=%d edges=%d",
        agent_id,
        len(node_ids),
        len(edges),
    )

    return node_ids, edges


# ---------------------------------------------------------------------------
# Damped PageRank — scipy.sparse power iteration (runs in executor)
# ---------------------------------------------------------------------------


def _compute_damped_pagerank(
    node_ids: list[str],
    edges: list[dict[str, Any]],
    damping_factor: float,
) -> dict[str, float]:
    """Compute Damped PageRank with epistemic uncertainty penalty.

    Standard PageRank formula::

        PR(v) = (1 - d) / N + d * Σ (PR(u) * w(u→v) / out_weight(u))

    Our variant modifies edge weights by their epistemic certainty::

        effective_weight(u→v) = weight * (1.0 - epistemic_uncertainty)

    So edges with high uncertainty contribute less rank, causing nodes
    reachable only through uncertain paths to have low PR and high QI.

    **This function is CPU-bound** and MUST be called inside
    ``asyncio.run_in_executor``.

    Args:
        node_ids: List of Entity node IDs in the subgraph.
        edges: List of edge dicts with ``source_id``, ``target_id``,
               ``weight``, ``epistemic_uncertainty``.
        damping_factor: Standard PageRank damping (0.0–1.0).

    Returns:
        Dict mapping node_id → normalised PageRank score (0.0–1.0).
        Scores are normalised so max(scores) == 1.0.
    """
    n = len(node_ids)
    if n == 0:
        return {}

    # Build node index
    id_to_idx: dict[str, int] = {nid: i for i, nid in enumerate(node_ids)}

    # Build sparse adjacency with damped weights
    row_indices: list[int] = []
    col_indices: list[int] = []
    values: list[float] = []

    for edge in edges:
        src_idx = id_to_idx.get(edge["source_id"])
        tgt_idx = id_to_idx.get(edge["target_id"])

        if src_idx is None or tgt_idx is None:
            continue  # Skip edges to/from unknown nodes

        # Damped effective weight: high uncertainty → low contribution
        certainty = 1.0 - edge["epistemic_uncertainty"]
        effective_weight = edge["weight"] * max(certainty, 0.0)

        if effective_weight > 0.0:
            row_indices.append(tgt_idx)  # Column-stochastic: A[to, from]
            col_indices.append(src_idx)
            values.append(effective_weight)

    if not values:
        # No edges — all nodes get uniform rank
        uniform = 1.0
        return {nid: uniform for nid in node_ids}

    # Build sparse transition matrix (column-stochastic)
    A = sparse.csc_matrix(
        (np.array(values, dtype=np.float64), (row_indices, col_indices)),
        shape=(n, n),
    )

    # Normalise columns (out-degree normalisation)
    col_sums = np.array(A.sum(axis=0)).flatten()
    col_sums[col_sums == 0] = 1.0  # Avoid division by zero (dangling nodes)
    D_inv = sparse.diags(1.0 / col_sums)
    M = A @ D_inv  # Column-stochastic transition matrix

    # Power iteration
    pr = np.ones(n, dtype=np.float64) / n
    teleport = (1.0 - damping_factor) / n

    for iteration in range(_MAX_ITERATIONS):
        pr_new = damping_factor * (M @ pr) + teleport
        delta = np.abs(pr_new - pr).sum()
        pr = pr_new

        if delta < _CONVERGENCE_EPSILON:
            logger.debug(
                "PAGERANK_CONVERGED | iterations=%d delta=%.2e",
                iteration + 1,
                delta,
            )
            break

    # Normalise to [0, 1] range (max = 1.0)
    max_pr = pr.max()
    if max_pr > 0:
        pr = pr / max_pr

    return {node_ids[i]: float(pr[i]) for i in range(n)}


# ---------------------------------------------------------------------------
# Quarantine execution — mark nodes in KùzuDB
# ---------------------------------------------------------------------------


async def _quarantine_nodes(
    provider: KuzuGraphProvider,
    agent_id: str,
    node_ids: list[str],
) -> None:
    """Set ``is_quarantined = true`` on specified Entity nodes.

    Each UPDATE is parameterised with ``agent_id`` for Zero-Trust
    isolation.  Failures on individual nodes are caught and logged
    without aborting the batch.

    Args:
        provider: Initialised ``KuzuGraphProvider``.
        agent_id: Tenant isolation key (mandatory).
        node_ids: List of Entity node IDs to quarantine.
    """
    for node_id in node_ids:
        try:
            await provider.execute_write(
                "MATCH (n:Entity {id: $id, agent_id: $agent_id}) "
                "SET n.is_quarantined = true",
                {"id": node_id, "agent_id": agent_id},
            )
            logger.info(
                "NODE_QUARANTINED | agent_id=%s node_id=%s",
                agent_id,
                node_id,
            )
        except Exception as exc:
            logger.warning(
                "QUARANTINE_FAILED | agent_id=%s node_id=%s error=%s",
                agent_id,
                node_id,
                exc,
            )


async def schedule_pagerank_worker(dao: Any, interval_sec: int = 3600) -> None:
    """Background loop to periodically run quarantine scans across all agents.

    Runs continuously in the background, querying all distinct agent_ids
    via the DAO layer and running the quarantine scan for each.
    """
    logger.info("PageRank quarantine worker scheduled (interval=%ds)", interval_sec)

    while True:
        try:
            agent_ids = await dao.get_all_active_agent_ids()

            for agent_id in agent_ids:
                if dao.graph_provider is None:
                    continue
                try:
                    await run_quarantine_scan(
                        agent_id=agent_id,
                        graph_provider=dao.graph_provider,
                    )
                except Exception as exc:
                    logger.error("PageRank scan failed for agent %s: %s", agent_id, exc)

        except asyncio.CancelledError:
            logger.info("PageRank worker cancelled.")
            break
        except Exception as exc:
            logger.error("PageRank worker encountered an error: %s", exc)

        await asyncio.sleep(interval_sec)
