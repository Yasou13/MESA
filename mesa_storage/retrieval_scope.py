"""Dataset ownership filtering shared by live and rebuild retrieval paths."""

from collections.abc import Iterable, Mapping
from typing import Any

V4_RRF_LANE_ORDER = ("vector", "bm25", "graph")


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
        "AND s.state = 'ACTIVE' "
        "WHERE r.tenant_id = e.tenant_id "
        "AND r.physical_artifact_id = e.entity_id "
        "AND r.state = 'ACTIVE' "
        "AND r.artifact_kind IN ('ENTITY', 'ENTITY_VECTOR') "
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
