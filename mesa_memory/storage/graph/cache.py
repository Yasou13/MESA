from typing import Optional

import networkx as nx


class GraphCache:
    """
    In-memory cache for the active knowledge graph.
    Encapsulates the raw NetworkX graph and provides bounded access.
    """

    def __init__(self):
        self._graph = nx.MultiDiGraph()

    def clear(self) -> None:
        self._graph.clear()

    def add_node(self, node_id: str, **kwargs) -> None:
        self._graph.add_node(node_id, **kwargs)

    def remove_node(self, node_id: str) -> None:
        if self._graph.has_node(node_id):
            self._graph.remove_node(node_id)

    def add_edge(self, source: str, target: str, key: str, **kwargs) -> None:
        self._graph.add_edge(source, target, key=key, **kwargs)

    def remove_edge(self, source: str, target: str, key: str) -> None:
        try:
            self._graph.remove_edge(source, target, key=key)
        except nx.NetworkXError:
            pass

    def has_node(self, node_id: str) -> bool:
        return self._graph.has_node(node_id)

    def get_node_data(self, node_id: str) -> Optional[dict]:
        if self._graph.has_node(node_id):
            return dict(self._graph.nodes[node_id])
        return None

    def get_node_degree(self, node_id: str) -> int:
        if self._graph.has_node(node_id):
            return self._graph.degree(node_id)
        return 0

    def get_out_edges(self, node_id: str) -> list[tuple]:
        if not self._graph.has_node(node_id):
            return []
        return list(self._graph.edges(node_id, data=True, keys=True))

    def get_in_edges(self, node_id: str) -> list[tuple]:
        if not self._graph.has_node(node_id):
            return []
        return list(self._graph.in_edges(node_id, data=True, keys=True))

    def get_all_nodes(self) -> list[tuple]:
        return list(self._graph.nodes(data=True))

    def get_successors(self, node_id: str) -> list[str]:
        if not self._graph.has_node(node_id):
            return []
        return list(self._graph.successors(node_id))

    def get_predecessors(self, node_id: str) -> list[str]:
        if not self._graph.has_node(node_id):
            return []
        return list(self._graph.predecessors(node_id))

    def get_subgraph(self, nodes: set[str]) -> nx.MultiDiGraph:
        return self._graph.subgraph(nodes).copy()

    def get_raw_graph(self) -> nx.MultiDiGraph:
        """Returns reference to raw graph for analytics operations."""
        return self._graph

    def copy(self) -> nx.MultiDiGraph:
        """Returns a deep copy of the underlying graph structure."""
        return self._graph.copy()
