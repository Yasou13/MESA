import asyncio
import os
import shutil
import uuid

import pytest

from mesa_storage.dao import MemoryDAO
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.vector_engine import VectorEngine

TEST_DIR = os.path.join(os.path.dirname(__file__), ".test_storage_tmp", "conflict")


@pytest.fixture(autouse=True)
def _clean():
    os.makedirs(TEST_DIR, exist_ok=True)
    yield
    shutil.rmtree(TEST_DIR, ignore_errors=True)


@pytest.fixture
def dao_env():
    uid = uuid.uuid4().hex[:8]
    db = os.path.join(TEST_DIR, f"dao_{uid}.db")
    vec = os.path.join(TEST_DIR, f"vec_{uid}.lance")
    sql = AsyncEngine(db, max_connections=2)
    vec_eng = VectorEngine(vec, max_workers=1)
    loop = asyncio.new_event_loop()
    loop.run_until_complete(sql.initialize())
    loop.run_until_complete(initialize_schema(sql))
    loop.run_until_complete(vec_eng.initialize())
    dao = MemoryDAO(sqlite_engine=sql, vector_engine=vec_eng)
    yield dao, sql, vec_eng, loop
    loop.run_until_complete(sql.close())
    loop.run_until_complete(vec_eng.close())
    loop.close()


class TestSemanticConflictResolution:
    def test_semantic_conflict_soft_deletes_old_triplet(self, dao_env):
        dao, sql, vec_eng, loop = dao_env
        agent_id = "agent-conflict-1"

        # We need realistic embeddings to trigger distance < 0.15
        # Highly similar: we can just use the same embedding
        emb_a = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
        emb_b = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]  # Identical

        # Step 1: Insert Triplet A
        node_a = loop.run_until_complete(
            dao.insert_memory(
                agent_id=agent_id,
                entity_name="Twitter",
                content='{"subject": "Twitter", "predicate": "is owned by", "object": "Public Shareholders"}',
                embedding=emb_a,
            )
        )

        # Step 2: Insert Triplet B (Conflict)
        node_b = loop.run_until_complete(
            dao.insert_memory(
                agent_id=agent_id,
                entity_name="Twitter",
                content='{"subject": "Twitter", "predicate": "is owned by", "object": "Elon Musk"}',
                embedding=emb_b,
            )
        )

        # Step 3: Assert Triplet A is soft-deleted
        # It should have invalid_at set in SQLite and not be returned by get_memories
        memories = loop.run_until_complete(dao.get_memories(agent_id))
        active_ids = [m["id"] for m in memories]

        assert node_b in active_ids, "Triplet B should be active"
        assert node_a not in active_ids, "Triplet A should be soft-deleted"

        # Verify Triplet A has invalid_at set in SQLite
        async def fetch_node(nid):
            async with sql.connection() as conn:
                async with conn.execute(
                    "SELECT invalid_at FROM nodes WHERE id = ?", (nid,)
                ) as cur:
                    return await cur.fetchone()

        row_a = loop.run_until_complete(fetch_node(node_a))
        assert row_a is not None
        assert row_a[0] is not None, "Triplet A must have invalid_at set"

        # Step 4: Assert querying LanceDB returns Triplet B but NOT Triplet A
        search_res = loop.run_until_complete(
            dao.search_memory(
                agent_id=agent_id, query_vector=emb_b, limit=5, include_graph=False
            )
        )
        returned_nids = [r["node_id"] for r in search_res]
        assert node_b in returned_nids
        assert node_a not in returned_nids, (
            "Triplet A should have been soft-deleted from LanceDB"
        )
