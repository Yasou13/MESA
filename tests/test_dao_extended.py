import os
import shutil
import uuid

import pytest
import pytest_asyncio

from mesa_storage.dao import MemoryDAO
from mesa_storage.kuzu_provider import KuzuGraphProvider
from mesa_storage.kuzu_setup import initialize_schema as init_kuzu_schema
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.vector_engine import VectorEngine

TEST_DIR = os.path.join(os.path.dirname(__file__), ".test_storage_tmp", "dao_ext")


@pytest.fixture(autouse=True)
def _clean():
    os.makedirs(TEST_DIR, exist_ok=True)
    yield
    shutil.rmtree(TEST_DIR, ignore_errors=True)


@pytest_asyncio.fixture
async def dao_full():
    uid = uuid.uuid4().hex[:8]
    db = os.path.join(TEST_DIR, f"dao_{uid}.db")
    vec = os.path.join(TEST_DIR, f"vec_{uid}.lance")
    graph_path = os.path.join(TEST_DIR, f"graph_{uid}.kuzu")

    sql = AsyncEngine(db, max_connections=2)
    vec_eng = VectorEngine(vec, max_workers=1)

    init_kuzu_schema(graph_path)
    graph_eng = KuzuGraphProvider(db_path=graph_path)

    await sql.initialize()
    await initialize_schema(sql)
    await vec_eng.initialize()
    await graph_eng.initialize()

    dao = MemoryDAO(sqlite_engine=sql, vector_engine=vec_eng, graph_provider=graph_eng)

    yield dao

    await sql.close()
    await vec_eng.close()
    await graph_eng.close()


@pytest.mark.asyncio
async def test_dao_initialize(dao_full):
    # Should call initialize on components implicitly or explicitly
    await dao_full.initialize()


@pytest.mark.asyncio
async def test_update_entity_description(dao_full):
    agent_id = "test_agent"
    node_id = await dao_full.insert_memory(
        agent_id,
        content="Old content",
        entity_name="OldEntity",
        node_type="ENTITY",
        embedding=[0.1] * 768,
    )

    await dao_full.update_entity_description(
        agent_id, node_id=node_id, new_content="New content", new_embedding=[0.2] * 768
    )

    node = await dao_full.get_memory_by_id(agent_id, node_id=node_id)
    assert node is not None
    assert node["content_payload"] == "New content"


@pytest.mark.asyncio
async def test_get_all_edges_and_degree(dao_full):
    agent_id = "test_agent"
    n1 = await dao_full.insert_memory(
        agent_id,
        content="Node 1",
        entity_name="Node1Entity",
        node_type="ENTITY",
        embedding=[0.1] * 768,
    )
    n2 = await dao_full.insert_memory(
        agent_id,
        content="Node 2",
        entity_name="Node2Entity",
        node_type="ENTITY",
        embedding=[0.1] * 768,
    )

    await dao_full.insert_edge(
        agent_id, source_id=n1, target_id=n2, weight=1.5, relation_type="RELATED_TO"
    )

    edges = await dao_full.get_all_edges(agent_id)
    print("\nAll edges:", edges)

    graph = dao_full._require_graph()
    q1 = await graph.execute_query(
        "MATCH (a:Entity {id: $node_id})-[r:Observed]->() RETURN count(r)",
        {"node_id": n1},
    )
    print("Q1 (directed out):", q1)

    q2 = await graph.execute_query(
        "MATCH (a:Entity {id: $node_id})<-[r:Observed]-() RETURN count(r)",
        {"node_id": n1},
    )
    print("Q2 (directed in):", q2)

    q3 = await graph.execute_query(
        "MATCH (a:Entity {id: $node_id})-[r:Observed]-() RETURN count(r)",
        {"node_id": n1},
    )
    print("Q3 (undirected):", q3)

    q4 = await graph.execute_query(
        "MATCH (a:Entity {id: $node_id}) RETURN a.id", {"node_id": n1}
    )
    print("Q4 (node id):", q4)

    degree = await dao_full.get_node_degree(agent_id, node_id=n1)
    print("Degree from dao:", degree)
    assert len(edges) == 1
    assert edges[0]["source_id"] == n1
    assert edges[0]["target_id"] == n2
    assert edges[0]["weight"] == 1.5

    assert degree == 1
    degree_in = await dao_full.get_node_degree(agent_id, node_id=n2)
    assert degree_in == 1


@pytest.mark.asyncio
async def test_find_consolidated_nodes_by_name(dao_full):
    agent_id = "test_agent"
    n1 = await dao_full.insert_memory(
        agent_id,
        content="Content",
        node_type="ENTITY",
        embedding=[0.1] * 768,
        entity_name="MyEntity",
    )
    await dao_full.mark_consolidated(agent_id, node_id=n1)

    res = await dao_full.find_consolidated_nodes_by_name(
        agent_id, entity_name="MyEntity"
    )
    assert len(res) == 1
    assert res[0]["id"] == n1


@pytest.mark.asyncio
async def test_search_memory_fts(dao_full):
    agent_id = "test_agent"
    await dao_full.insert_memory(
        agent_id,
        content="Unique keyword text",
        node_type="ENTITY",
        embedding=[0.1] * 768,
        entity_name="NamedEntity",
    )

    res = await dao_full.search_memory_fts(agent_id, query="NamedEntity", limit=5)
    assert len(res) == 1
    assert res[0]["entity_name"] == "NamedEntity"


@pytest.mark.asyncio
async def test_get_epistemic_data_for_nodes(dao_full):
    agent_id = "test_agent"
    n1 = await dao_full.insert_memory(
        agent_id,
        content="Content",
        entity_name="Entity1",
        node_type="ENTITY",
        embedding=[0.1] * 768,
    )
    n2 = await dao_full.insert_memory(
        agent_id,
        content="Content",
        entity_name="Entity2",
        node_type="ENTITY",
        embedding=[0.1] * 768,
    )

    await dao_full.insert_edge(
        agent_id,
        source_id=n1,
        target_id=n2,
        weight=1.0,
        relation_type="RELATED_TO",
        epistemic_uncertainty=0.5,
    )

    data = await dao_full.get_epistemic_data_for_nodes(agent_id, node_ids=[n1, n2])
    assert isinstance(data, dict)


@pytest.mark.asyncio
async def test_align_memory_space(dao_full):
    # Calling align_memory_space with dummy arguments
    await dao_full.align_memory_space(transformation_matrix=None, golden_dataset=[])


@pytest.mark.asyncio
async def test_reconcile_orphaned_nodes(dao_full):
    await dao_full._reconcile_orphaned_nodes()
