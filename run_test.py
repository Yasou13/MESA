import sys
sys.path.insert(0, ".")
import os
import asyncio
from mesa_memory.storage.vector_index import VectorStorage
from tests.test_storage import _make_cmb, TEST_STORAGE_DIR
import lancedb

def test():
    uri = os.path.join(TEST_STORAGE_DIR, "vector_test.lance")
    vs = VectorStorage(uri=uri)
    vs.get_or_create_table(dimension=768)
    cmb_a = _make_cmb("vector A", embedding=[0.5] * 768)
    vs._check_memory_limit = lambda: None
    vs.upsert_vector(cmb_id=cmb_a.cmb_id, embedding=cmb_a.embedding, content_payload=cmb_a.content_payload, source=cmb_a.source, fitness_score=cmb_a.fitness_score, created_at=cmb_a.created_at.isoformat())
    
    # manual soft delete
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    try:
        table = vs.db.open_table("mesa_memory_768")
        table.update(where=f"cmb_id = '{cmb_a.cmb_id}'", values={"expired_at": now})
        print("Update succeeded!")
    except Exception as e:
        print("Update failed:", e)

test()
