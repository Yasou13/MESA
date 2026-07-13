# ruff: noqa: E402
import asyncio
import os
import sys
import tempfile

import nest_asyncio

nest_asyncio.apply()

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from mesa_memory.adapter.factory import AdapterFactory  # noqa: E402
from mesa_memory.retrieval.core import QueryAnalyzer  # noqa: E402
from mesa_memory.retrieval.hybrid import HybridRetriever  # noqa: E402
from mesa_memory.security.rbac import AccessControl  # noqa: E402
from mesa_storage.dao import MemoryDAO  # noqa: E402
from mesa_storage.kuzu_provider import KuzuGraphProvider  # noqa: E402
from mesa_storage.kuzu_setup import (
    initialize_schema as kuzu_initialize_schema,
)  # noqa: E402
from mesa_storage.schemas import initialize_schema  # noqa: E402
from mesa_storage.sqlite_engine import AsyncEngine  # noqa: E402
from mesa_storage.vector_engine import VectorEngine  # noqa: E402


class BenchmarkAccessControl(AccessControl):
    async def check_access(self, agent_id: str, session_id: str, mode: str) -> bool:
        return True


async def _init():
    temp_dir = tempfile.TemporaryDirectory()
    db_path = f"{temp_dir.name}/mesa.db"
    lance_path = f"{temp_dir.name}/vector.lance"
    graph_path = f"{temp_dir.name}/graph.kuzu"

    print("Initializing sqlite...")
    sqlite = AsyncEngine(db_path=db_path)
    await sqlite.initialize()
    await initialize_schema(sqlite)
    print("Sqlite initialized.")

    print("Initializing vector...")
    vector = VectorEngine(uri=lance_path)
    await vector.initialize()
    print("Vector initialized.")

    print("Initializing kuzu...")
    kuzu_initialize_schema(graph_path)
    graph_provider = KuzuGraphProvider(db_path=graph_path)
    await graph_provider.initialize()
    print("Kuzu initialized.")

    print("Initializing DAO...")
    memory_dao = MemoryDAO(
        sqlite_engine=sqlite,
        vector_engine=vector,
        graph_provider=graph_provider,
    )
    await memory_dao.initialize()
    print("DAO initialized.")

    print("Getting LLM adapter...")
    llm_adapter = AdapterFactory.get_adapter("auto")
    print("LLM adapter obtained.")

    print("Initializing HybridRetriever...")
    analyzer = QueryAnalyzer()
    _ = HybridRetriever(
        dao=memory_dao,
        analyzer=analyzer,
        embedder=llm_adapter,
        access_control=BenchmarkAccessControl(),
    )
    print("HybridRetriever initialized.")
    return temp_dir  # keep alive


print("Running loop...")
loop = asyncio.new_event_loop()
td = loop.run_until_complete(_init())
print("Loop completed.")
