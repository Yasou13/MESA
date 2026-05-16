"""
P0-B: NetworkX-backed implementation of :class:`BaseGraphProvider`.

This provider preserves the exact persistence and concurrency semantics of the
original ``GraphStorage`` class while conforming to the fully-async ABC.

Backing stores:
- **In-memory**: ``networkx.MultiDiGraph`` (hot path, microsecond reads).
- **Persistent**: ``aiosqlite`` (WAL-mode SQLite, crash-safe writes).
- **Cold archive**: ``rocksdict.Rdict`` (RocksDB, expired record offload).

Sync → Async strategy:
- SQLite ops are natively async via ``aiosqlite``.
- NetworkX mutations (``add_node``, ``remove_node``, etc.) are O(1) and execute
  under ``asyncio.Lock`` — no thread-pool needed.
- CPU-heavy analytics (``nx.pagerank``) are offloaded via
  ``asyncio.to_thread`` to avoid blocking the event loop.
"""

import asyncio
from datetime import datetime, timezone
from typing import Optional

import aiosqlite
import networkx as nx
from uuid6 import uuid7 as _uuid7_func

from mesa_memory.security.rbac import AccessControl
from mesa_memory.security.rbac_constants import _UNSET_IDENTITY
from mesa_memory.storage.graph import analytics as graph_analytics
from mesa_memory.storage.graph.base import BaseGraphProvider
from mesa_memory.storage.graph.cache import GraphCache
from mesa_memory.storage.graph.mvcc import MVCCManager


class NetworkXProvider(BaseGraphProvider):
    """Concrete graph provider backed by NetworkX + aiosqlite + RocksDB."""

    def __init__(
        self,
        db_path: str = "./storage/knowledge_graph.db",
        rocks_path: str = "./storage/kg_history.rocks",
        access_control: AccessControl | None = None,
    ):
        self.db_path = db_path
        self.rocks_path = rocks_path
        self.access_control = access_control
        self._cache = GraphCache()
        self._mvcc = MVCCManager(db_path=self.db_path)
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
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

    async def _load_active_graph(self) -> None:
        """Hydrate the in-memory graph from the persistent store."""
        self._cache.clear()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM nodes WHERE expired_at IS NULL"
            ) as cursor:
                async for row in cursor:
                    node_row = dict(row)
                    self._cache.add_node(
                        node_row["node_id"],
                        name=node_row["name"],
                        type=node_row["type"],
                        created_at=node_row["created_at"],
                        expired_at=None,
                    )
            async with db.execute(
                "SELECT * FROM edges WHERE expired_at IS NULL"
            ) as cursor:
                async for row in cursor:
                    edge_row = dict(row)
                    self._cache.add_edge(
                        edge_row["source_node"],
                        edge_row["target_node"],
                        key=edge_row["edge_id"],
                        relation_type=edge_row["relation_type"],
                        weight=edge_row["weight"],
                        created_at=edge_row["created_at"],
                        expired_at=None,
                    )

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    async def upsert_node(
        self,
        name: str,
        type: str,
        cmb_id: Optional[str] = None,
        agent_id: str = _UNSET_IDENTITY,
        session_id: str = _UNSET_IDENTITY,
    ) -> str:
        if self.access_control and not self.access_control.check_access(
            agent_id, session_id, "WRITE"
        ):
            raise PermissionError(
                f"Agent '{agent_id}' lacks WRITE access for session '{session_id}'"
            )

        node_id = str(_uuid7_func())
        now = datetime.now(timezone.utc).isoformat()
        old_id = None

        async with self._lock:
            async with aiosqlite.connect(self.db_path, isolation_level=None) as db:
                db.row_factory = aiosqlite.Row
                await db.execute("BEGIN IMMEDIATE")
                try:
                    old_id = await self._mvcc.upsert_node_version(
                        db, name, type, node_id, now, cmb_id
                    )
                    await db.execute("COMMIT")
                except Exception:
                    await db.execute("ROLLBACK")
                    raise

            # Update in-memory graph AFTER successful DB commit
            if old_id is not None:
                for u, v, k, d in self._cache.get_out_edges(old_id):
                    self._cache.add_edge(node_id, v, key=k, **d)
                for u, v, k, d in self._cache.get_in_edges(old_id):
                    self._cache.add_edge(u, node_id, key=k, **d)
                self._cache.remove_node(old_id)

            self._cache.add_node(
                node_id,
                name=name,
                type=type,
                created_at=now,
                expired_at=None,
            )
        return node_id

    async def create_edge(
        self,
        source_id: str,
        target_id: str,
        relation: str,
        weight: float = 1.0,
        agent_id: str = _UNSET_IDENTITY,
        session_id: str = _UNSET_IDENTITY,
    ) -> str:
        if self.access_control and not self.access_control.check_access(
            agent_id, session_id, "WRITE"
        ):
            raise PermissionError(
                f"Agent '{agent_id}' lacks WRITE access for session '{session_id}'"
            )

        edge_id = str(_uuid7_func())
        now = datetime.now(timezone.utc).isoformat()

        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "INSERT INTO edges (edge_id, source_node, target_node, relation_type, weight, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (edge_id, source_id, target_id, relation, weight, now),
                )
                await db.commit()

            self._cache.add_edge(
                source_id,
                target_id,
                key=edge_id,
                relation_type=relation,
                weight=weight,
                created_at=now,
                expired_at=None,
            )
        return edge_id

    # ------------------------------------------------------------------
    # Soft-delete operations
    # ------------------------------------------------------------------

    async def soft_delete_node(self, node_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                await self._mvcc.soft_delete_node(db, node_id, now)
                await db.commit()

            self._cache.remove_node(node_id)

    async def soft_delete_edge(self, edge_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                edge_data = await self._mvcc.get_active_edge(db, edge_id)
                if edge_data:
                    await self._mvcc.soft_delete_edge(db, edge_id, now)
                    await db.commit()
                    self._cache.remove_edge(
                        edge_data["source_node"],
                        edge_data["target_node"],
                        key=edge_id,
                    )

    async def soft_delete_by_cmb(self, cmb_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                nodes = await self._mvcc.get_cmb_nodes(db, cmb_id)
                for node_id in nodes:
                    await self._mvcc.soft_delete_node(db, node_id, now)
                await self._mvcc.remove_cmb_link(db, cmb_id)
                await db.commit()

                for node_id in nodes:
                    self._cache.remove_node(node_id)

    # ------------------------------------------------------------------
    # Read operations  (decomposed from legacy get_active_graph)
    # ------------------------------------------------------------------

    async def get_node_by_id(self, node_id: str) -> Optional[dict]:
        async with self._lock:
            data = self._cache.get_node_data(node_id)
            if not data:
                return None
            return {
                "node_id": node_id,
                "name": data.get("name", ""),
                "type": data.get("type", ""),
                "created_at": data.get("created_at", ""),
            }

    async def get_neighbors(
        self,
        node_id: str,
        direction: str = "both",
    ) -> list[dict]:
        async with self._lock:
            if not self._cache.has_node(node_id):
                return []

            results: list[dict] = []

            if direction in ("out", "both"):
                for _, target, key, data in self._cache.get_out_edges(node_id):
                    target_data = self._cache.get_node_data(target) or {}
                    results.append(
                        {
                            "node_id": target,
                            "name": target_data.get("name", ""),
                            "type": target_data.get("type", ""),
                            "edge_id": key,
                            "relation": data.get("relation_type", ""),
                            "weight": data.get("weight", 1.0),
                        }
                    )

            if direction in ("in", "both"):
                for source, _, key, data in self._cache.get_in_edges(node_id):
                    source_data = self._cache.get_node_data(source) or {}
                    results.append(
                        {
                            "node_id": source,
                            "name": source_data.get("name", ""),
                            "type": source_data.get("type", ""),
                            "edge_id": key,
                            "relation": data.get("relation_type", ""),
                            "weight": data.get("weight", 1.0),
                        }
                    )

            return results

    async def get_node_degree(self, node_id: str) -> int:
        async with self._lock:
            return self._cache.get_node_degree(node_id)

    async def find_nodes_by_name(
        self,
        names: list[str],
        case_insensitive: bool = True,
    ) -> list[dict]:
        lookup = {n.lower() for n in names} if case_insensitive else set(names)
        results: list[dict] = []
        async with self._lock:
            for node_id, data in self._cache.get_all_nodes():
                name = data.get("name", "")
                match_name = name.lower() if case_insensitive else name
                if match_name in lookup:
                    results.append(
                        {
                            "node_id": node_id,
                            "name": name,
                            "type": data.get("type", ""),
                            "created_at": data.get("created_at", ""),
                        }
                    )
        return results

    async def get_subgraph(
        self,
        node_ids: list[str],
        depth: int = 1,
    ) -> dict:
        async with self._lock:
            collected_nodes: set[str] = set(node_ids)
            frontier: set[str] = {nid for nid in node_ids if self._cache.has_node(nid)}

            for _ in range(depth):
                next_frontier: set[str] = set()
                for nid in frontier:
                    next_frontier.update(self._cache.get_successors(nid))
                    next_frontier.update(self._cache.get_predecessors(nid))
                collected_nodes.update(next_frontier)
                frontier = next_frontier

            sub = self._cache.get_subgraph(collected_nodes)
            nodes = [
                {
                    "node_id": n,
                    "name": d.get("name", ""),
                    "type": d.get("type", ""),
                    "created_at": d.get("created_at", ""),
                }
                for n, d in sub.nodes(data=True)
            ]
            edges = [
                {
                    "edge_id": k,
                    "source_id": u,
                    "target_id": v,
                    "relation": d.get("relation_type", ""),
                    "weight": d.get("weight", 1.0),
                }
                for u, v, k, d in sub.edges(data=True, keys=True)
            ]
            return {"nodes": nodes, "edges": edges}

    async def get_all_active_nodes(self) -> list[dict]:
        async with self._lock:
            return [
                {
                    "node_id": n,
                    "name": d.get("name", ""),
                    "type": d.get("type", ""),
                    "created_at": d.get("created_at", ""),
                }
                for n, d in self._cache.get_all_nodes()
            ]

    # ------------------------------------------------------------------
    # Graph analytics (delegated to analytics module)
    # ------------------------------------------------------------------

    async def compute_pagerank(
        self,
        personalization: Optional[dict[str, float]] = None,
        alpha: float = 0.15,
        max_iter: int = 100,
        tol: float = 1e-6,
    ) -> dict[str, float]:
        return await graph_analytics.compute_pagerank(
            graph=self._cache.get_raw_graph(),
            lock=self._lock,
            personalization=personalization,
            alpha=alpha,
            max_iter=max_iter,
            tol=tol,
        )

    # ------------------------------------------------------------------
    # Maintenance (delegated to analytics module)
    # ------------------------------------------------------------------

    async def offload_expired(self) -> int:
        return await graph_analytics.offload_expired(
            db_path=self.db_path,
            rocks_path=self.rocks_path,
        )

    # ------------------------------------------------------------------
    # Provider-specific (NOT in ABC — backward compatibility)
    # ------------------------------------------------------------------

    def get_active_graph(self) -> nx.MultiDiGraph:
        """Return a copy of the in-memory graph.

        .. deprecated::
            This method is **not** part of ``BaseGraphProvider``.  It is
            retained solely for backward compatibility during the P0-B
            migration.  Consumers must migrate to the decomposed query
            methods (``find_nodes_by_name``, ``get_node_degree``, etc.).
        """
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return self._cache.copy()

        if loop.is_running():
            # Usually get_active_graph is deprecated and might be called synchronously
            # If so, locking sync is hard from async code if it returns nx.MultiDiGraph directly
            # For backward compatibility, just return the copy directly
            pass

        return self._cache.copy()
