import sys
sys.path.insert(0, ".")
import os
import asyncio
from mesa_memory.storage.vector_index import VectorStorage
from tests.test_storage import _make_cmb, TEST_STORAGE_DIR

def test():
    uri = os.path.join(TEST_STORAGE_DIR, "vector_test.lance")
    vs = VectorStorage(uri=uri)
    vs.get_or_create_table(dimension=768)
    cmb_a = _make_cmb("vector A", embedding=[0.5] * 768)
    cmb_b = _make_cmb("vector B", embedding=[0.9] * 768)
    vs._check_memory_limit = lambda: None
    vs.upsert_vector(cmb_id=cmb_a.cmb_id, embedding=cmb_a.embedding, content_payload=cmb_a.content_payload, source=cmb_a.source, fitness_score=cmb_a.fitness_score, created_at=cmb_a.created_at.isoformat())
    vs.upsert_vector(cmb_id=cmb_b.cmb_id, embedding=cmb_b.embedding, content_payload=cmb_b.content_payload, source=cmb_b.source, fitness_score=cmb_b.fitness_score, created_at=cmb_b.created_at.isoformat())
    
    vs.soft_delete(cmb_a.cmb_id)
    results = vs.search(query_vector=[0.7] * 768, limit=10)
    returned_ids = [r["cmb_id"] for r in results]
    print("returned_ids:", returned_ids)
    print("a:", cmb_a.cmb_id)
    print("b:", cmb_b.cmb_id)

test()
