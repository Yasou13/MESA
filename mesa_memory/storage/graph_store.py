import json
import asyncio
from datetime import datetime, timezone

import aiosqlite
import networkx as nx
from uuid_extensions import uuid7 as _uuid7_func
from rocksdict import Rdict

from mesa_memory.config import config


class GraphStorage:
    def __init__(self, db_path: str = "./storage/knowledge_graph.db", rocks_path: str = "./storage/kg_history.rocks"):
        self.db_path = db_path
        self.rocks_path = rocks_path
        self.graph = nx.MultiDiGraph()
        self._lock = asyncio.Lock()

    async def initialize(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS nodes (
                    node_id     TEXT PRIMARY KEY,
                    name        TEXT NOT NULL,
                    type        TEXT NOT NULL,
                    created_at  TEXT NOT NULL,
                    expired_at  TEXT DEFAULT NULL
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS edges (
                    edge_id       TEXT PRIMARY KEY,
                    source_node   TEXT NOT NULL,
                    target_node   TEXT NOT NULL,
                    relation_type TEXT NOT NULL,
                    weight        REAL NOT NULL DEFAULT 1.0,
                    created_at    TEXT NOT NULL,
                    expired_at    TEXT DEFAULT NULL,
                    FOREIGN KEY (source_node) REFERENCES nodes(node_id),
                    FOREIGN KEY (target_node) REFERENCES nodes(node_id)
                )
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_nodes_active
                ON nodes(expired_at) WHERE expired_at IS NULL
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_edges_active
                ON edges(expired_at) WHERE expired_at IS NULL
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_edges_source
                ON edges(source_node) WHERE expired_at IS NULL
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_edges_target
                ON edges(target_node) WHERE expired_at IS NULL
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS cmb_nodes (
                    cmb_id TEXT NOT NULL,
                    node_id TEXT NOT NULL,
                    PRIMARY KEY (cmb_id, node_id)
                )
            """)
            await db.commit()

        await self._load_active_graph()

    async def _load_active_graph(self):
        self.graph.clear()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM nodes WHERE expired_at IS NULL"
            ) as cursor:
                async for row in cursor:
                    row = dict(row)
                    self.graph.add_node(
                        row["node_id"],
                        name=row["name"],
                        type=row["type"],
                        created_at=row["created_at"],
                        expired_at=None,
                    )
            async with db.execute(
                "SELECT * FROM edges WHERE expired_at IS NULL"
            ) as cursor:
                async for row in cursor:
                    row = dict(row)
                    self.graph.add_edge(
                        row["source_node"],
                        row["target_node"],
                        key=row["edge_id"],
                        relation_type=row["relation_type"],
                        weight=row["weight"],
                        created_at=row["created_at"],
                        expired_at=None,
                    )

    async def upsert_node(self, name: str, type: str, cmb_id: str = None) -> str:
        node_id = str(_uuid7_func())
        now = datetime.now(timezone.utc).isoformat()
        old_id = None

        async with self._lock:
            async with aiosqlite.connect(self.db_path, isolation_level=None) as db:
                db.row_factory = aiosqlite.Row
                await db.execute("BEGIN IMMEDIATE")
                try:
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
                            (cmb_id, node_id)
                        )
                    await db.execute("COMMIT")
                except Exception:
                    await db.execute("ROLLBACK")
                    raise

            # Update in-memory graph AFTER successful DB commit
            if old_id is not None:
                for u, v, k, d in list(self.graph.edges(old_id, data=True, keys=True)):
                    self.graph.add_edge(node_id, v, key=k, **d)
                for u, v, k, d in list(self.graph.in_edges(old_id, data=True, keys=True)):
                    self.graph.add_edge(u, node_id, key=k, **d)
                self.graph.remove_node(old_id)

            self.graph.add_node(
                node_id,
                name=name,
                type=type,
                created_at=now,
                expired_at=None,
            )
        return node_id

    async def create_edge(self, source_id: str, target_id: str, relation: str, weight: float = 1.0) -> str:
        edge_id = str(_uuid7_func())
        now = datetime.now(timezone.utc).isoformat()

        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "INSERT INTO edges (edge_id, source_node, target_node, relation_type, weight, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (edge_id, source_id, target_id, relation, weight, now),
                )
                await db.commit()

            self.graph.add_edge(
                source_id,
                target_id,
                key=edge_id,
                relation_type=relation,
                weight=weight,
                created_at=now,
                expired_at=None,
            )
        return edge_id

    async def soft_delete_node(self, node_id: str):
        now = datetime.now(timezone.utc).isoformat()
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "UPDATE nodes SET expired_at = ? WHERE node_id = ? AND expired_at IS NULL",
                    (now, node_id),
                )
                await db.execute(
                    "UPDATE edges SET expired_at = ? WHERE (source_node = ? OR target_node = ?) AND expired_at IS NULL",
                    (now, node_id, node_id),
                )
                await db.commit()

            if self.graph.has_node(node_id):
                self.graph.remove_node(node_id)

    async def soft_delete_edge(self, edge_id: str):
        now = datetime.now(timezone.utc).isoformat()
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT source_node, target_node FROM edges WHERE edge_id = ? AND expired_at IS NULL",
                    (edge_id,),
                ) as cursor:
                    row = await cursor.fetchone()

                if row:
                    await db.execute(
                        "UPDATE edges SET expired_at = ? WHERE edge_id = ? AND expired_at IS NULL",
                        (now, edge_id),
                    )
                    await db.commit()
                    row = dict(row)
                    try:
                        self.graph.remove_edge(row["source_node"], row["target_node"], key=edge_id)
                    except nx.NetworkXError:
                        pass

    def get_active_graph(self) -> nx.MultiDiGraph:
        return self.graph.copy()

    async def soft_delete_by_cmb(self, cmb_id: str):
        now = datetime.now(timezone.utc).isoformat()
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT node_id FROM cmb_nodes WHERE cmb_id = ?",
                    (cmb_id,)
                ) as cursor:
                    rows = await cursor.fetchall()
                
                for row in rows:
                    node_id = row["node_id"]
                    await db.execute(
                        "UPDATE nodes SET expired_at = ? WHERE node_id = ? AND expired_at IS NULL",
                        (now, node_id),
                    )
                    await db.execute(
                        "UPDATE edges SET expired_at = ? WHERE (source_node = ? OR target_node = ?) AND expired_at IS NULL",
                        (now, node_id, node_id),
                    )
                await db.commit()
                
                for row in rows:
                    node_id = row["node_id"]
                    if self.graph.has_node(node_id):
                        self.graph.remove_node(node_id)

    async def offload_to_rocks(self):
        rocks = Rdict(self.rocks_path)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            async with db.execute(
                "SELECT * FROM nodes WHERE expired_at IS NOT NULL"
            ) as cursor:
                expired_nodes = [dict(row) async for row in cursor]

            for node in expired_nodes:
                rocks[f"node:{node['node_id']}"] = json.dumps(node)

            if expired_nodes:
                node_ids = [n["node_id"] for n in expired_nodes]
                placeholders = ",".join("?" for _ in node_ids)
                await db.execute(
                    f"DELETE FROM nodes WHERE node_id IN ({placeholders})",
                    node_ids,
                )

            async with db.execute(
                "SELECT * FROM edges WHERE expired_at IS NOT NULL"
            ) as cursor:
                expired_edges = [dict(row) async for row in cursor]

            for edge in expired_edges:
                rocks[f"edge:{edge['edge_id']}"] = json.dumps(edge)

            if expired_edges:
                edge_ids = [e["edge_id"] for e in expired_edges]
                placeholders = ",".join("?" for _ in edge_ids)
                await db.execute(
                    f"DELETE FROM edges WHERE edge_id IN ({placeholders})",
                    edge_ids,
                )

            await db.commit()
        rocks.close()
