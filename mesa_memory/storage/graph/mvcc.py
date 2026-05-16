from typing import Optional

import aiosqlite


class MVCCManager:
    """
    Manages Multi-Version Concurrency Control (MVCC) in SQLite.
    Handles temporal record versioning, rollbacks, and soft deletes.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path

    async def upsert_node_version(
        self,
        db: aiosqlite.Connection,
        name: str,
        type: str,
        node_id: str,
        now: str,
        cmb_id: Optional[str] = None,
    ) -> Optional[str]:
        """
        Executes the MVCC upsert logic within the current transaction.
        Returns the old_id if a node was versioned out, else None.
        """
        db.row_factory = aiosqlite.Row
        old_id = None
        async with db.execute(
            "SELECT node_id FROM nodes WHERE name = ? AND expired_at IS NULL",
            (name,),
        ) as cursor:
            existing = await cursor.fetchone()

        if existing:
            old_id = existing["node_id"]
            # Re-link edges to the new node_id before expiring the old node
            await db.execute(
                "UPDATE edges SET source_node = ? WHERE source_node = ? AND expired_at IS NULL",
                (node_id, old_id),
            )
            await db.execute(
                "UPDATE edges SET target_node = ? WHERE target_node = ? AND expired_at IS NULL",
                (node_id, old_id),
            )
            await db.execute(
                "UPDATE nodes SET expired_at = ? WHERE node_id = ? AND expired_at IS NULL",
                (now, old_id),
            )

        await db.execute(
            "INSERT INTO nodes (node_id, name, type, created_at) VALUES (?, ?, ?, ?)",
            (node_id, name, type, now),
        )
        if cmb_id:
            await db.execute(
                "INSERT OR IGNORE INTO cmb_nodes (cmb_id, node_id) VALUES (?, ?)",
                (cmb_id, node_id),
            )

        return old_id

    async def soft_delete_node(
        self, db: aiosqlite.Connection, node_id: str, timestamp: str
    ) -> None:
        """Expire a specific node and its active edges."""
        await db.execute(
            "UPDATE nodes SET expired_at = ? WHERE node_id = ? AND expired_at IS NULL",
            (timestamp, node_id),
        )
        await db.execute(
            "UPDATE edges SET expired_at = ? WHERE (source_node = ? OR target_node = ?) AND expired_at IS NULL",
            (timestamp, node_id, node_id),
        )

    async def soft_delete_edge(
        self, db: aiosqlite.Connection, edge_id: str, timestamp: str
    ) -> None:
        """Expire a specific edge."""
        await db.execute(
            "UPDATE edges SET expired_at = ? WHERE edge_id = ? AND expired_at IS NULL",
            (timestamp, edge_id),
        )

    async def get_active_edge(
        self, db: aiosqlite.Connection, edge_id: str
    ) -> Optional[dict]:
        """Fetch an active edge to check if it exists before soft deleting."""
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT source_node, target_node FROM edges WHERE edge_id = ? AND expired_at IS NULL",
            (edge_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return dict(row)
        return None

    async def get_cmb_nodes(self, db: aiosqlite.Connection, cmb_id: str) -> list[str]:
        """Get all node IDs associated with a specific CMB."""
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT node_id FROM cmb_nodes WHERE cmb_id = ?",
            (cmb_id,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [row["node_id"] for row in rows]

    async def remove_cmb_link(self, db: aiosqlite.Connection, cmb_id: str) -> None:
        """Remove links between a CMB and its nodes."""
        await db.execute("DELETE FROM cmb_nodes WHERE cmb_id = ?", (cmb_id,))
