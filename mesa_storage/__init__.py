# MESA v0.3.0 — Phase 3 & 4: Asynchronous Storage, Graph Layer & DAO
"""
mesa_storage: Non-blocking SQLite engine, graph schema, FTS5 lexical
pre-filtering, and disk-backed vector engine for the MESA knowledge graph.

Public API::

    from mesa_storage.sqlite_engine import AsyncEngine
    from mesa_storage.vector_engine import VectorEngine
    from mesa_storage.schemas import (
        initialize_schema,
        validate_schema,
        insert_node,
        bulk_insert_nodes,
        insert_edge,
        upsert_edge,
        get_neighbors,
        k_hop_neighbors,
        fts5_search,
        fts5_rebuild,
    )
"""

from mesa_storage.schemas import (
    bulk_insert_nodes,
    find_nodes_by_name,
    fts5_rebuild,
    fts5_search,
    get_active_edges,
    get_active_nodes,
    get_neighbors,
    initialize_schema,
    insert_edge,
    insert_node,
    k_hop_neighbors,
    mark_consolidated,
    soft_delete_edge,
    soft_delete_node,
    upsert_edge,
    validate_schema,
)
from mesa_storage.dao import MemoryDAO
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.vector_engine import VectorEngine

__all__ = [
    "AsyncEngine",
    "VectorEngine",
    "MemoryDAO",
    "initialize_schema",
    "validate_schema",
    "insert_node",
    "bulk_insert_nodes",
    "soft_delete_node",
    "mark_consolidated",
    "get_active_nodes",
    "find_nodes_by_name",
    "insert_edge",
    "upsert_edge",
    "soft_delete_edge",
    "get_neighbors",
    "get_active_edges",
    "k_hop_neighbors",
    "fts5_search",
    "fts5_rebuild",
]
