import networkx as nx


def find_path(
    graph: nx.Graph, source_entity: str, target_entity: str, max_hops: int = 3
) -> list:
    """Find the shortest path between two entities in the knowledge graph."""
    try:
        path = nx.shortest_path(graph, source=source_entity, target=target_entity)
        if len(path) - 1 <= max_hops:
            return path
        return []
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return []
