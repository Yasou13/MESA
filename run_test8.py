import sys
sys.path.insert(0, ".")
import os
from mesa_memory.storage.vector_index import VectorStorage
from tests.test_storage import _make_cmb, TEST_STORAGE_DIR

def test():
    uri = os.path.join(TEST_STORAGE_DIR, "vector_test.lance")
    vs = VectorStorage(uri=uri)
    table = vs.db.open_table("mesa_memory_768")
    res = table.search([0.7]*768).where("expired_at IS NULL").to_list()
    print("search results length:", len(res))
    for r in res:
        print("cmb_id:", r["cmb_id"], "expired_at:", r["expired_at"])

test()
