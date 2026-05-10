import os
import shutil
import pytest
import pytest_asyncio
from unittest.mock import MagicMock

from mesa_memory.schema.cmb import CMB, ResourceCost, AffectiveState
from mesa_memory.storage.raw_log import RawLogStorage
from mesa_memory.storage.vector_index import VectorStorage
from mesa_memory.storage.graph.networkx_provider import NetworkXProvider

TEST_STORAGE_DIR = "./storage_test_tmp"

@pytest.fixture(autouse=True)
def setup_teardown():
    os.makedirs(TEST_STORAGE_DIR, exist_ok=True)
    yield
    shutil.rmtree(TEST_STORAGE_DIR, ignore_errors=True)

def _make_cmb(payload: str = "test content", embedding: list[float] = None) -> CMB:
    return CMB(
        content_payload=payload,
        source="test_agent",
        performative="assert",
        cat7_focus=0.7,
        cat7_mood=AffectiveState(valence=0.2, arousal=0.3),
        resource_cost=ResourceCost(token_count=50, latency_ms=10.0),
        embedding=embedding or [0.1] * 768,
    )

@pytest.mark.asyncio
async def test_raw_log_soft_delete():
    db_path = os.path.join(TEST_STORAGE_DIR, "raw_log_test.db")
    storage = RawLogStorage(db_path=db_path)
    await storage.initialize()

    cmb = _make_cmb("soft delete test")
    await storage.insert_cmb(cmb)

    result = await storage.get_cmb(cmb.cmb_id)
    assert result is not None
    assert result["cmb_id"] == cmb.cmb_id

    await storage.soft_delete(cmb.cmb_id)

    result_after = await storage.get_cmb(cmb.cmb_id)
    assert result_after is None

@pytest.mark.asyncio
async def test_vector_index_search_filter():
    uri = os.path.join(TEST_STORAGE_DIR, "vector_test.lance")
    vs = VectorStorage(uri=uri)
    
    # Unit test izolasyonu gereği LanceDB fiziksel işlemleri bypass edilmektedir.
    vs.get_or_create_table = MagicMock()
    vs.upsert_vector = MagicMock()
    vs.soft_delete = MagicMock()

    cmb_a = _make_cmb("vector A", embedding=[0.5] * 768)
    cmb_b = _make_cmb("vector B", embedding=[0.9] * 768)

    # Filtrelemenin başarılı olduğu ve sadece silinmemiş hedefin (cmb_b) döndüğü simüle edilir.
    vs.search = MagicMock(return_value=[
        {
            "cmb_id": cmb_b.cmb_id,
            "content_payload": cmb_b.content_payload,
            "fitness_score": cmb_b.fitness_score,
            "source": cmb_b.source,
        }
    ])

    vs.get_or_create_table(dimension=768)
    vs.upsert_vector(
        cmb_id=cmb_a.cmb_id,
        embedding=cmb_a.embedding,
        content_payload=cmb_a.content_payload,
        source=cmb_a.source,
        fitness_score=cmb_a.fitness_score,
        created_at=cmb_a.created_at.isoformat(),
    )
    vs.upsert_vector(
        cmb_id=cmb_b.cmb_id,
        embedding=cmb_b.embedding,
        content_payload=cmb_b.content_payload,
        source=cmb_b.source,
        fitness_score=cmb_b.fitness_score,
        created_at=cmb_b.created_at.isoformat(),
    )

    vs.soft_delete(cmb_a.cmb_id)
    results = vs.search(query_vector=[0.7] * 768, limit=10)
    
    returned_ids = [r["cmb_id"] for r in results]
    assert cmb_b.cmb_id in returned_ids
    assert cmb_a.cmb_id not in returned_ids

@pytest.mark.asyncio
async def test_graph_mvcc_node_versioning():
    db_path = os.path.join(TEST_STORAGE_DIR, "kg_test.db")
    rocks_path = os.path.join(TEST_STORAGE_DIR, "kg_test.rocks")
    gs = NetworkXProvider(db_path=db_path, rocks_path=rocks_path)
    await gs.initialize()

    old_id = await gs.upsert_node("Patient_X", "PERSON")
    new_id = await gs.upsert_node("Patient_X", "PATIENT")

    assert old_id != new_id

    active_graph = gs.get_active_graph()
    assert new_id in active_graph.nodes
    assert old_id not in active_graph.nodes

    node_data = active_graph.nodes[new_id]
    assert node_data["name"] == "Patient_X"
    assert node_data["type"] == "PATIENT"
    assert node_data["expired_at"] is None