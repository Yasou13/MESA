"""Dataset ownership filtering shared by live and rebuild retrieval paths."""

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

V4_RRF_DEFAULT_K = 60
V4_RRF_LANE_ORDER = ("vector", "bm25", "assertion", "graph")
V4_RRF_LANE_WEIGHTS: dict[str, float] = {
    "vector": 1.0,
    "bm25": 1.0,
    "assertion": 1.0,
    "graph": 1.0,
}


def compute_rrf_lane_score(
    rank: int,
    *,
    lane: str,
    k: int = V4_RRF_DEFAULT_K,
    weights: Mapping[str, float] | None = None,
) -> float:
    """Calculate the RRF score contribution for a given 1-based rank in a specified lane."""
    if rank < 1:
        raise ValueError(f"rank must be >= 1, got {rank}")
    w_map = weights if weights is not None else V4_RRF_LANE_WEIGHTS
    weight = w_map.get(lane, 1.0)
    return weight / (k + rank)


def rrf_fuse_lanes(
    lane_rankings: Mapping[str, Sequence[str]],
    *,
    k: int = V4_RRF_DEFAULT_K,
    weights: Mapping[str, float] | None = None,
) -> list[tuple[str, float, dict[str, int]]]:
    """Deterministic, weighted multi-lane Reciprocal Rank Fusion.

    Args:
        lane_rankings: Mapping of lane name to an ordered sequence of candidate IDs.
        k: Smoothing constant (default: 60).
        weights: Optional mapping of lane name to weight multiplier.

    Returns:
        List of (candidate_id, fused_rrf_score, lane_ranks_dict), sorted
        deterministically by (-fused_rrf_score, candidate_id).
    """
    scores: dict[str, float] = {}
    lane_ranks: dict[str, dict[str, int]] = {}

    for lane, ranking in lane_rankings.items():
        for rank, cand_id in enumerate(ranking, start=1):
            contrib = compute_rrf_lane_score(rank, lane=lane, k=k, weights=weights)
            scores[cand_id] = scores.get(cand_id, 0.0) + contrib
            if cand_id not in lane_ranks:
                lane_ranks[cand_id] = {}
            lane_ranks[cand_id][lane] = rank

    ordered = sorted(scores.keys(), key=lambda cid: (-scores[cid], cid))
    return [(cid, scores[cid], lane_ranks[cid]) for cid in ordered]


def build_v4_lexical_query(*, dataset_count: int) -> str:
    """Build an FTS query whose ownership predicate runs before rank/limit."""
    if dataset_count < 1:
        raise ValueError("dataset scope cannot be empty")
    placeholders = ",".join("?" for _ in range(dataset_count))
    return (
        "SELECT e.entity_id FROM v4_entities_fts f "
        "JOIN v4_entities e ON e.rowid = f.rowid "
        "WHERE v4_entities_fts MATCH ? AND e.tenant_id = ? "
        "AND e.status = 'ACTIVE' AND EXISTS ("
        "SELECT 1 FROM artifact_registry r "
        "JOIN artifact_sources s ON s.registry_id = r.registry_id "
        "JOIN memory_mutations m ON m.mutation_id = s.mutation_id "
        "AND s.state = 'ACTIVE' "
        "WHERE r.tenant_id = e.tenant_id "
        "AND r.physical_artifact_id = e.entity_id "
        "AND r.state = 'ACTIVE' "
        "AND r.artifact_kind IN ('ENTITY', 'ENTITY_VECTOR') "
        "AND m.agent_id = ? "
        f"AND s.dataset_id IN ({placeholders})"
        ") ORDER BY rank, e.entity_id LIMIT ?"
    )


def scope_vector_result_ids(
    rows: Iterable[Mapping[str, Any]], *, allowed_ids: set[str]
) -> list[str]:
    """Keep ranked vector identities owned by the requested dataset scope."""
    scoped: list[str] = []
    seen: set[str] = set()
    for row in rows:
        node_id = str(row.get("node_id", ""))
        if node_id in allowed_ids and node_id not in seen:
            scoped.append(node_id)
            seen.add(node_id)
    return scoped
