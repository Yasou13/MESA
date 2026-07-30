# MESA — PageRank graph-observation worker.
#
# PageRank describes graph topology; it does not establish whether a fact is
# true. A new or deliberately narrow fact is often a leaf, so PageRank must
# never be used as an automatic quarantine signal.
#
# Architecture:
#   - Extracts the agent-scoped subgraph from KùzuDB via MemoryDAO
#   - Builds a sparse adjacency matrix using scipy.sparse (no NetworkX)
#   - Runs power-iteration PageRank with per-edge damping from
#     epistemic_uncertainty values
#   - Low-rank nodes are reported as structural-review candidates only
#
# Invariants:
#   - Zero-Trust: EVERY database read/write is parameterised with agent_id
#   - I/O Integrity: ALL synchronous KùzuDB calls and heavy math are
#     wrapped in asyncio.run_in_executor to prevent event loop blocking
#   - This worker is designed to be invoked as a periodic cron-job,
#     from the existing MaintenanceWorker, or as a standalone asyncio task
"""
Damped PageRank graph-observation worker for MESA.

Periodically analyses the per-agent knowledge graph and reports nodes whose
incoming edges carry high ``epistemic_uncertainty`` (injected during
ingestion from the Dual-LLM consensus pipeline).

The algorithm computes a variant of PageRank where each edge's
contribution is damped by ``(1.0 - epistemic_uncertainty)``.  Nodes
that receive rank almost exclusively through uncertain edges will have
a low PageRank score, translating to a high **structural-review index**.
These scores are telemetry only. Validation, contradiction assertions and
their provenance remain the only evidence that can affect retrieval state.

Usage::

    from mesa_workers.maintenance_pagerank import run_pagerank_observation

    # Called from a background scheduler or maintenance worker:
    await run_pagerank_observation(
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

# Minimum graph size to produce stable structural-review telemetry.
_MIN_GRAPH_SIZE: int = 3


# ---------------------------------------------------------------------------
# Public API — async entry point
# ---------------------------------------------------------------------------


async def run_pagerank_observation(
    agent_id: str,
    graph_provider: KuzuGraphProvider,
    *,
    threshold_qi: float = 0.85,
    damping_factor: float = _DEFAULT_DAMPING_FACTOR,
) -> dict[str, Any]:
    """Report low-authority graph nodes without changing retrieval state.

    Extracts the agent-scoped subgraph from KùzuDB, computes Damped
    PageRank where ``epistemic_uncertainty`` penalises edge contributions.
    A low score is a topological property, not evidence of hallucination,
    so this function never writes ``is_quarantined`` in any store.

    **Zero-Trust**: Every KùzuDB read/write is parameterised with
    ``agent_id``.

    **I/O Integrity**: All synchronous KùzuDB calls and numpy/scipy
    computation are wrapped in ``asyncio.run_in_executor`` to prevent
    FastAPI event loop blocking.

    Args:
        agent_id: Legacy graph partition key; v4 tenant security is enforced
            independently by the authorized catalog/session context.
        graph_provider: Initialised ``KuzuGraphProvider`` instance.
        threshold_qi: Structural-review threshold (0.0–1.0). Nodes with
            ``1 - normalised_pagerank >= threshold_qi`` are reported as
            candidates only. Default 0.85 flags very low authority nodes.
        damping_factor: Standard PageRank damping factor (default 0.85).

    Returns:
        Summary dict with keys::

            {
                "agent_id": str,
                "total_nodes": int,
                "review_candidate_count": int,
                "review_candidate_ids": list[str],
                "mode": "OBSERVE_ONLY",
                "quarantined_count": 0,
                "quarantined_ids": [],
                "elapsed_ms": float,
            }
    """
    t_start = time.monotonic()

    logger.info(
        "PAGERANK_OBSERVATION_START | agent_id=%s threshold_qi=%.3f",
        agent_id,
        threshold_qi,
    )

    # ---- 1. Extract agent-scoped subgraph (executor-offloaded) --------
    nodes, edges = await _extract_subgraph(graph_provider, agent_id)

    if len(nodes) < _MIN_GRAPH_SIZE:
        logger.info(
            "PAGERANK_OBSERVATION_SKIP | agent_id=%s nodes=%d reason=graph_too_small",
            agent_id,
            len(nodes),
        )
        return {
            "agent_id": agent_id,
            "total_nodes": len(nodes),
            "review_candidate_count": 0,
            "review_candidate_ids": [],
            "mode": "OBSERVE_ONLY",
            # Legacy callers may still read these fields. They are pinned to
            # zero because PageRank no longer has quarantine authority.
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

    # ---- 3. Identify structural-review candidates ---------------------
    review_candidate_ids: list[str] = []
    for node_id, pr_score in pagerank_scores.items():
        qi = 1.0 - pr_score  # quarantine index
        if qi >= threshold_qi:
            review_candidate_ids.append(node_id)

    logger.info(
        "PAGERANK_OBSERVATION_CANDIDATES | agent_id=%s total_nodes=%d candidates=%d",
        agent_id,
        len(nodes),
        len(review_candidate_ids),
    )

    elapsed_ms = (time.monotonic() - t_start) * 1000
    logger.info(
        "PAGERANK_OBSERVATION_DONE | agent_id=%s candidates=%d elapsed_ms=%.1f",
        agent_id,
        len(review_candidate_ids),
        elapsed_ms,
    )

    return {
        "agent_id": agent_id,
        "total_nodes": len(nodes),
        "review_candidate_count": len(review_candidate_ids),
        "review_candidate_ids": review_candidate_ids,
        "mode": "OBSERVE_ONLY",
        "quarantined_count": 0,
        "quarantined_ids": [],
        "elapsed_ms": elapsed_ms,
    }


# Backwards-compatible import name. It deliberately has observation-only
# behaviour: PageRank has no mutation authority in V4.
run_quarantine_scan = run_pagerank_observation


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
        "MATCH (n:Entity {agent_id: $agent_id}) RETURN n.id",
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


async def schedule_pagerank_worker(dao: Any, interval_sec: int = 3600) -> None:
    """Background loop to periodically collect PageRank telemetry.

    Runs continuously in the background, querying all distinct agent_ids
    via the DAO layer and running an observation-only scan for each.
    """
    logger.info("PageRank observation worker scheduled (interval=%ds)", interval_sec)

    while True:
        try:
            agent_ids = await dao.get_all_active_agent_ids()

            for agent_id in agent_ids:
                if dao.graph_provider is None:
                    continue
                try:
                    await run_pagerank_observation(
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
