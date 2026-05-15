from typing import Optional
from mesa_memory.storage.graph.base import BaseGraphProvider
from mesa_memory.security.rbac_constants import _UNSET_IDENTITY


class MemgraphProvider(BaseGraphProvider):
    async def initialize(self) -> None:
        raise NotImplementedError("MemgraphProvider is planned for the roadmap.")

    async def upsert_node(
        self,
        name: str,
        type: str,
        cmb_id: Optional[str] = None,
        agent_id: str = _UNSET_IDENTITY,
        session_id: str = _UNSET_IDENTITY,
    ) -> str:
        pass

    async def create_edge(
        self,
        source_id: str,
        target_id: str,
        relation: str,
        weight: float = 1.0,
        agent_id: str = _UNSET_IDENTITY,
        session_id: str = _UNSET_IDENTITY,
    ) -> str:
        pass

    async def soft_delete_node(self, node_id: str) -> None:
        pass

    async def soft_delete_edge(self, edge_id: str) -> None:
        pass

    async def soft_delete_by_cmb(self, cmb_id: str) -> None:
        pass

    async def get_node_by_id(self, node_id: str) -> Optional[dict]:
        pass

    async def get_neighbors(self, node_id: str, direction: str = "both") -> list[dict]:
        return []

    async def get_node_degree(self, node_id: str) -> int:
        return 0

    async def find_nodes_by_name(
        self, names: list[str], case_insensitive: bool = True
    ) -> list[dict]:
        return []

    async def get_subgraph(self, node_ids: list[str], depth: int = 1) -> dict:
        return {}

    async def get_all_active_nodes(self) -> list[dict]:
        return []

    async def compute_pagerank(
        self,
        personalization: Optional[dict[str, float]] = None,
        alpha: float = 0.15,
        max_iter: int = 100,
        tol: float = 1e-6,
    ) -> dict[str, float]:
        return {}

    async def offload_expired(self) -> int:
        return 0
