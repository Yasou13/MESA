from datetime import datetime, timezone

from mesa_storage.sqlite_engine import AsyncEngine

# ---------------------------------------------------------------------------
# Node operations
# ---------------------------------------------------------------------------


async def insert_node(
    engine: AsyncEngine,
    node_id: str,
    entity_name: str,
    node_type: str = "ENTITY",
    content_payload: str = "",
    agent_id: str = "__unset__",
    session_id: str = "__unset__",
    is_consolidated: bool = False,
    confidence: float = 1.0,
    is_quarantined: bool = False,
) -> str:
    """Insert a new node into the graph.

    Returns:
        The node_id of the inserted node.
    """
    if agent_id == "__unset__":
        import warnings

        warnings.warn(
            "insert_node called with sentinel agent_id='__unset__'. "
            "Pass an explicit agent_id in production code.",
            DeprecationWarning,
            stacklevel=2,
        )
    now = datetime.now(timezone.utc).isoformat()

    async with engine.transaction() as db:
        await db.execute(
            "INSERT INTO nodes (id, entity_name, type, content_payload, is_consolidated, "
            "created_at, agent_id, session_id, confidence, is_quarantined) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                node_id,
                entity_name,
                node_type,
                content_payload,
                int(is_consolidated),
                now,
                agent_id,
                session_id,
                float(confidence),
                int(is_quarantined),
            ),
        )
        await db.commit()

    return node_id


async def bulk_insert_nodes(
    engine: AsyncEngine,
    nodes: list[dict],
) -> int:
    """Insert multiple nodes in a single transaction.

    Each dict must contain: id, entity_name.
    Optional keys: type, agent_id, session_id, is_consolidated, confidence, is_quarantined.

    Returns:
        Number of nodes inserted.
    """
    if not nodes:
        return 0

    now = datetime.now(timezone.utc).isoformat()

    async with engine.transaction() as db:
        await db.executemany(
            "INSERT INTO nodes (id, entity_name, type, content_payload, is_consolidated, "
            "created_at, agent_id, session_id, confidence, is_quarantined) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    n["id"],
                    n["entity_name"],
                    n.get("type", "ENTITY"),
                    n.get("content_payload", ""),
                    int(n.get("is_consolidated", False)),
                    now,
                    n.get("agent_id", "__unset__"),
                    n.get("session_id", "__unset__"),
                    float(n.get("confidence", 1.0)),
                    int(n.get("is_quarantined", False)),
                )
                for n in nodes
            ],
        )
        await db.commit()

    return len(nodes)


async def soft_delete_node(engine: AsyncEngine, node_id: str, *, agent_id: str) -> None:
    """Soft-delete a node by setting its invalid_at timestamp.

    RLS: agent_id is mandatory and hardcoded into every WHERE clause.

    .. warning::
       This function does NOT cascade to KùzuDB graph edges.
       Use ``MemoryDAO.invalidate_node()`` instead, which correctly
       cascades the soft-delete to both SQLite and KùzuDB.
       This function is retained for backward compatibility only.
    """
    now = datetime.now(timezone.utc).isoformat()

    async with engine.transaction() as db:
        await db.execute(
            "UPDATE nodes SET invalid_at = ? "
            "WHERE id = ? AND agent_id = ? AND invalid_at IS NULL",
            (now, node_id, agent_id),
        )
        await db.commit()


async def mark_consolidated(
    engine: AsyncEngine, node_id: str, *, agent_id: str
) -> None:
    """Mark a node as consolidated (processed by batch orchestrator).

    RLS: agent_id is mandatory and hardcoded into the WHERE clause.
    """
    async with engine.connection() as db:
        await db.execute(
            "UPDATE nodes SET is_consolidated = 1 "
            "WHERE id = ? AND agent_id = ? AND invalid_at IS NULL",
            (node_id, agent_id),
        )
        await db.commit()


async def get_active_nodes(
    engine: AsyncEngine,
    agent_id: str,
    limit: int | None = None,
) -> list[dict]:
    """Return all active (non-invalidated) nodes.

    RLS: agent_id is **mandatory** — no unscoped global reads allowed.

    Args:
        agent_id: Mandatory tenant isolation key.
        limit: Optional maximum number of rows.

    Returns:
        List of node dicts with keys: id, entity_name, type,
        is_consolidated, created_at, agent_id, session_id.
    """
    query = "SELECT * FROM nodes WHERE agent_id = ? AND invalid_at IS NULL"
    params: list = [agent_id]

    query += " ORDER BY created_at ASC"

    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)

    async with engine.connection() as db:
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def find_nodes_by_name(
    engine: AsyncEngine,
    names: list[str],
    *,
    agent_id: str,
    case_insensitive: bool = True,
) -> list[dict]:
    """Find active nodes whose entity_name matches any in names.

    RLS: agent_id is mandatory and hardcoded into the WHERE clause.
    """
    if not names:
        return []

    if case_insensitive:
        conditions = " OR ".join("LOWER(entity_name) = ?" for _ in names)
        params: list = [agent_id] + [n.lower() for n in names]
    else:
        conditions = " OR ".join("entity_name = ?" for _ in names)
        params = [agent_id] + list(names)

    query = (
        f"SELECT * FROM nodes WHERE agent_id = ? "
        f"AND invalid_at IS NULL AND ({conditions})"
    )

    async with engine.connection() as db:
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
