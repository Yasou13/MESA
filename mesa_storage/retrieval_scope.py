"""Dataset ownership filtering shared by live and rebuild retrieval paths."""

from collections.abc import Iterable, Mapping
from typing import Any

V4_RRF_LANE_ORDER = ("vector", "bm25", "graph")


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
