from mesa_memory.schema.cmb import CMB
from mesa_memory.storage.raw_log import RawLogStorage
from mesa_memory.storage.vector_index import VectorStorage
from mesa_memory.storage.graph.base import BaseGraphProvider
from mesa_memory.storage.graph.networkx_provider import NetworkXProvider
from mesa_memory.security.rbac import AccessControl, sanitize_cmb_content

import logging

logger = logging.getLogger("MESA_Storage")


class StorageFacade:
    def __init__(
        self,
        raw_log_path: str = "./storage/raw_log.db",
        vector_uri: str = "./storage/vector_index.lance",
        graph_db_path: str = "./storage/knowledge_graph.db",
        graph_rocks_path: str = "./storage/kg_history.rocks",
        access_control: AccessControl | None = None,
        graph_provider: BaseGraphProvider | None = None,
    ):
        self.access_control = access_control or AccessControl()
        self.raw_log = RawLogStorage(db_path=raw_log_path)
        self.vector = VectorStorage(uri=vector_uri, access_control=self.access_control)
        
        if graph_provider is not None:
            self.graph: BaseGraphProvider = graph_provider
        else:
            self.graph: BaseGraphProvider = NetworkXProvider(
                db_path=graph_db_path,
                rocks_path=graph_rocks_path,
                access_control=self.access_control
            )

    async def initialize_all(self):
        await self.raw_log.initialize()
        await self.graph.initialize()
        orphan_count = await self.reconcile_orphans()
        if orphan_count:
            logger.warning(f"Reconciled {orphan_count} orphaned raw_log records on startup")

    async def reconcile_orphans(self) -> int:
        """Find raw_log records with no matching vector and soft-delete them.

        This handles the crash-between-stores scenario where a SQLite insert
        succeeds but the LanceDB write fails or the process dies before completion.
        """
        try:
            all_active_ids = await self.raw_log.fetch_all_active_ids()
            vector_ids = self.vector.get_all_cmb_ids()
            if not all_active_ids or not vector_ids:
                return 0
            orphans = [cid for cid in all_active_ids if cid not in vector_ids]
            for orphan_id in orphans:
                logger.warning(f"Reconciling orphan record: {orphan_id}")
                await self.raw_log.soft_delete(orphan_id)
            return len(orphans)
        except (RuntimeError, OSError, Exception) as e:
            logger.error("Reconciliation failed: %s", e, exc_info=True)
            return 0

    async def persist_cmb(self, cmb: CMB, agent_id: str, session_id: str):
        if not self.access_control.check_access(agent_id, session_id, "WRITE"):
            raise PermissionError(
                f"Agent '{agent_id}' lacks WRITE access for session '{session_id}'"
            )

        cmb = cmb.model_copy(update={"content_payload": sanitize_cmb_content(cmb.content_payload)})

        await self.raw_log.insert_cmb(cmb)

        data = cmb.model_dump()
        try:
            self.vector.upsert_vector(
                cmb_id=data["cmb_id"],
                embedding=data["embedding"],
                content_payload=data["content_payload"],
                source=data["source"],
                fitness_score=data["fitness_score"],
                created_at=data["created_at"].isoformat(),
                agent_id=agent_id,
                session_id=session_id,
            )
        except Exception as e:
            # Revert the raw_log insert if vector insert fails for ANY reason
            await self.raw_log.soft_delete(cmb.cmb_id)
            raise RuntimeError(f"Vector write failed, raw_log reverted: {e}") from e

    async def get_cmb(self, cmb_id: str, agent_id: str, session_id: str) -> dict | None:
        if not self.access_control.check_access(agent_id, session_id, "READ"):
            raise PermissionError(
                f"Agent '{agent_id}' lacks READ access for session '{session_id}'"
            )
        return await self.raw_log.get_cmb(cmb_id)

    async def soft_delete_all(self, cmb_id: str):
        """Purge a CMB record from ALL storage layers (SQLite, LanceDB, NetworkX).

        Executes deletions sequentially in dependency order.  If a downstream
        store fails, the already-completed deletions are logged as a partial
        purge so operators can manually reconcile via the dead-letter audit log.

        Raises:
            RuntimeError: If any storage layer fails after partial completion.
        """
        completed_layers: list[str] = []
        try:
            await self.raw_log.soft_delete(cmb_id)
            completed_layers.append("raw_log")

            self.vector.soft_delete(cmb_id)
            completed_layers.append("vector")

            await self.graph.soft_delete_by_cmb(cmb_id)
            completed_layers.append("graph")
        except Exception as exc:
            all_layers = ["raw_log", "vector", "graph"]
            failed_layer = next(
                (l for l in all_layers if l not in completed_layers), "unknown"
            )
            logger.error(
                f"PARTIAL PURGE for cmb_id={cmb_id}: "
                f"completed={completed_layers}, failed_at={failed_layer}, error={exc}"
            )
            raise RuntimeError(
                f"soft_delete_all failed at '{failed_layer}' for cmb_id={cmb_id}. "
                f"Completed layers: {completed_layers}. Manual reconciliation required."
            ) from exc

