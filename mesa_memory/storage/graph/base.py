"""
P0-B: Abstract async interface for knowledge graph storage providers.

All methods are ``async``.  Providers must internally manage concurrency,
persistence, and any in-memory caching.  Business logic **must not** depend
on any concrete graph library (e.g. NetworkX) — only on this interface.
"""

from abc import ABC, abstractmethod
from typing import Optional

from mesa_memory.security.rbac_constants import _UNSET_IDENTITY


class BaseGraphProvider(ABC):
    """Provider-agnostic async contract for the MESA knowledge graph layer.

    Every concrete provider (NetworkX, future backends) implements this ABC.
    Consumers (``ConsolidationLoop``, ``HybridRetriever``, ``StorageFacade``)
    depend exclusively on this interface.
    """

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @abstractmethod
    async def initialize(self) -> None:
        """Prepare the backend (create tables, hydrate caches, etc.)."""
        ...

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    @abstractmethod
    async def upsert_node(
        self,
        name: str,
        type: str,
        cmb_id: Optional[str] = None,
        agent_id: str = _UNSET_IDENTITY,
        session_id: str = _UNSET_IDENTITY,
    ) -> str:
        """Insert or update a node by name.

        If a node with the same *active* name exists, the old node is
        soft-expired and a new node is created.  Existing edges are
        re-linked to the new node.

        Args:
            name:   Entity name (case-sensitive identity key).
            type:   Node type (e.g. ``"ENTITY"``).
            cmb_id: Optional cognitive memory block ID for provenance.

        Returns:
            The ``node_id`` (UUID7 string) of the created / updated node.
        """
        ...

    @abstractmethod
    async def create_edge(
        self,
        source_id: str,
        target_id: str,
        relation: str,
        weight: float = 1.0,
        agent_id: str = _UNSET_IDENTITY,
        session_id: str = _UNSET_IDENTITY,
    ) -> str:
        """Create a directed edge between two existing nodes.

        Args:
            source_id: Source node ID.
            target_id: Target node ID.
            relation:  Relation type label.
            weight:    Edge weight (``1.0`` = high confidence,
                       ``0.5`` = uncertain zone).

        Returns:
            The ``edge_id`` (UUID7 string) of the created edge.
        """
        ...

    # ------------------------------------------------------------------
    # Soft-delete operations
    # ------------------------------------------------------------------

    @abstractmethod
    async def soft_delete_node(self, node_id: str) -> None:
        """Soft-delete a node and all its connected edges.

        Sets ``expired_at`` timestamp; does not physically remove data.
        """
        ...

    @abstractmethod
    async def soft_delete_edge(self, edge_id: str) -> None:
        """Soft-delete a single edge by its ID."""
        ...

    @abstractmethod
    async def soft_delete_by_cmb(self, cmb_id: str) -> None:
        """Soft-delete all nodes (and their edges) linked to a CMB ID.

        Used for privacy compliance and data retraction.
        """
        ...

    # ------------------------------------------------------------------
    # Read operations  (decomposed from legacy get_active_graph)
    # ------------------------------------------------------------------

    @abstractmethod
    async def get_node_by_id(self, node_id: str) -> Optional[dict]:
        """Retrieve a single active node by its ID.

        Returns:
            Dict ``{node_id, name, type, created_at}`` or ``None``.
        """
        ...

    @abstractmethod
    async def get_neighbors(
        self,
        node_id: str,
        direction: str = "both",
    ) -> list[dict]:
        """Return neighboring nodes connected by active edges.

        Args:
            node_id:   Node to query.
            direction: ``"out"``, ``"in"``, or ``"both"``.

        Returns:
            List of ``{node_id, name, type, edge_id, relation, weight}``.
        """
        ...

    @abstractmethod
    async def get_node_degree(self, node_id: str) -> int:
        """Return the total degree (in + out) of an active node.

        Returns ``0`` if the node does not exist.
        """
        ...

    @abstractmethod
    async def find_nodes_by_name(
        self,
        names: list[str],
        case_insensitive: bool = True,
    ) -> list[dict]:
        """Find active nodes whose names match any in *names*.

        Args:
            names:            List of entity name strings.
            case_insensitive: If ``True``, comparison is lowercased.

        Returns:
            List of ``{node_id, name, type, created_at}``.
        """
        ...

    @abstractmethod
    async def get_subgraph(
        self,
        node_ids: list[str],
        depth: int = 1,
    ) -> dict:
        """Extract a neighbourhood subgraph around *node_ids*.

        Args:
            node_ids: Seed node IDs.
            depth:    BFS expansion depth from seeds.

        Returns:
            ``{"nodes": [dict], "edges": [dict]}``.
        """
        ...

    @abstractmethod
    async def get_all_active_nodes(self) -> list[dict]:
        """Return every non-expired node.

        Returns:
            List of ``{node_id, name, type, created_at}``.
        """
        ...

    # ------------------------------------------------------------------
    # Graph analytics
    # ------------------------------------------------------------------

    @abstractmethod
    async def compute_pagerank(
        self,
        personalization: Optional[dict[str, float]] = None,
        alpha: float = 0.15,
        max_iter: int = 100,
        tol: float = 1e-6,
    ) -> dict[str, float]:
        """Compute (Personalized) PageRank over the active graph.

        Args:
            personalization: Optional ``{node_id: weight}`` for PPR.
            alpha:           Damping factor.
            max_iter:        Max iterations.
            tol:             Convergence tolerance.

        Returns:
            ``{node_id: score}`` mapping.
        """
        ...

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    @abstractmethod
    async def offload_expired(self) -> int:
        """Archive expired nodes/edges to cold storage and purge from hot.

        Returns:
            Total number of records archived.
        """
        ...
