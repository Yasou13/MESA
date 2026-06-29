import asyncio
import sys

sys.path.append(".")
import logging

from mesa_storage.dao import MemoryDAO
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.vector_engine import VectorEngine

logging.basicConfig(level=logging.INFO)


async def main() -> None:
    sql_engine = AsyncEngine("./storage/benchmark_mesa_sql.db")
    vec_engine = VectorEngine(uri="./storage/benchmark_mesa_vec")
    dao = MemoryDAO(sqlite_engine=sql_engine, vector_engine=vec_engine)
    await dao.initialize()
    nodes = await dao.get_memories("benchmark_CONF_019")
    print(f"Nodes for CONF_019: {len(nodes)}")
    for n in nodes:
        print(
            f"Node {n['id']} - is_consolidated: {n['is_consolidated']}, invalid_at: {n['invalid_at']}"
        )
        print(f"Content: {n['content_payload'][:50]}")


asyncio.run(main())
