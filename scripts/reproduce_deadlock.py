import asyncio
import sys
import time
import uuid

from mesa_storage.dao import MemoryDAO
from mesa_storage.kuzu_provider import KuzuGraphProvider
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.vector_engine import VectorEngine


async def main():
    print("Setting up test database...")
    db_path = "storage/test_deadlock.db"
    vec_path = "storage/test_deadlock.lance"
    kuzu_path = "storage/test_deadlock_graph"

    sql = AsyncEngine(db_path, max_connections=2)
    vec = VectorEngine(vec_path, max_workers=1)
    kuzu = KuzuGraphProvider(kuzu_path, max_workers=2)

    await sql.initialize()
    await initialize_schema(sql)
    await vec.initialize()

    from mesa_storage import kuzu_setup

    kuzu_setup.initialize_schema(kuzu_path)
    await kuzu.initialize()

    dao = MemoryDAO(sqlite_engine=sql, vector_engine=vec, graph_provider=kuzu)
    await dao.initialize()

    # Create a seed node
    agent_id = "test_agent"
    seed_id = uuid.uuid4().hex
    await dao.insert_memory(
        agent_id,
        node_id=seed_id,
        entity_name="Deadlock_Seed",
        content="Test content",
        embedding=[0.0] * 8,
    )

    # Let's mock a long-running Kuzu query
    async def fake_long_kuzu(*args, **kwargs):
        print("Mocked slow Kuzu query started...")
        await asyncio.sleep(2)
        print("Mocked slow Kuzu query finished.")
        return []

    kuzu.get_cognitive_salience = fake_long_kuzu  # type: ignore[method-assign]
    kuzu.get_neighbors = fake_long_kuzu  # type: ignore[method-assign]

    print("Spawning 2 long Kuzu queries to exhaust the thread pool...")
    tasks = []
    # KuzuGraphProvider max_workers=2, so 2 queries will exhaust it
    assert dao.graph_provider is not None
    t1 = asyncio.create_task(
        dao.graph_provider.get_cognitive_salience(seed_id, agent_id)
    )
    t2 = asyncio.create_task(
        dao.graph_provider.get_cognitive_salience(seed_id, agent_id)
    )
    tasks.extend([t1, t2])

    await asyncio.sleep(0.1)  # Let them start and block the executor

    print("Spawning an insert_memory which requires SQLite lock AND Kuzu write...")
    # This will acquire the SQLite transaction lock, then wait for Kuzu thread pool
    insert_task = asyncio.create_task(
        dao.insert_memory(
            agent_id, entity_name="Wait_For_Kuzu", content="Wait", embedding=[1.0] * 8
        )
    )

    await asyncio.sleep(0.1)

    print("Spawning a simple SQLite-only query (should return instantly)...")

    async def simple_query() -> bool:
        t0 = time.time()
        try:
            # We enforce a timeout. If it deadlocks, it hits the timeout.
            async def _do_query():
                async with sql.connection() as db:
                    async with db.execute("SELECT 1") as cursor:
                        await cursor.fetchone()

            await asyncio.wait_for(_do_query(), timeout=1.0)
            print(f"Simple SQLite query SUCCESS in {time.time() - t0:.2f}s")
            return True
        except asyncio.TimeoutError:
            print("Simple SQLite query TIMEOUT (DEADLOCK DETECTED!)")
            return False

    res = await simple_query()

    # Cleanup
    for t in tasks:
        t.cancel()
    insert_task.cancel()
    await sql.close()
    await vec.close()
    await kuzu.close()

    if not res:
        print("Deadlock confirmed.")
        sys.exit(1)
    else:
        print("No deadlock.")
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
