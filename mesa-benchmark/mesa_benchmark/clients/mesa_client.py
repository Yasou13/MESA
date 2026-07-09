import asyncio
import os
import sys
import tempfile
import time
from typing import Any, Dict

from ..datasets.schemas import BenchmarkQuestion, MemoryContext
from .base import AbstractBenchmarkClient, BenchmarkResponse

# Add parent directory of mesa_benchmark to path to find mesa_storage
sys.path.append(
    os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../"))
)

from mesa_storage.dao import MemoryDAO
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.vector_engine import VectorEngine


class MesaClientAdapter(AbstractBenchmarkClient):
    """
    Adapter for the MESA framework.
    Translates benchmark requests into MESA MemoryDAO calls.
    """

    def __init__(self) -> None:
        self.memory_dao: Any = None
        self.sqlite: Any = None
        self.vector: Any = None
        self.temp_dir: Any = None

    def initialize(self, config_params: Dict[str, Any]) -> None:
        """Initializes MESA components."""
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = f"{self.temp_dir.name}/mesa.db"
        lance_path = f"{self.temp_dir.name}/vector.lance"

        async def _init() -> None:
            self.sqlite = AsyncEngine(db_path=db_path)
            await self.sqlite.initialize()
            await initialize_schema(self.sqlite)

            self.vector = VectorEngine(uri=lance_path)
            await self.vector.initialize()

            self.memory_dao = MemoryDAO(sqlite_engine=self.sqlite, vector_engine=self.vector)
            await self.memory_dao.initialize()

        asyncio.run(_init())

    def clear_memory(self) -> None:
        """Flushes the database for a clean test environment."""
        async def _clear() -> None:
            if self.sqlite:
                try:
                    await self.sqlite.execute_script("DELETE FROM memory_nodes; DELETE FROM memory_edges;")
                except Exception:
                    pass
            if self.vector and hasattr(self.vector, 'db'):
                try:
                    self.vector.db.drop_table("memories")
                    self.vector.table = None
                    await self.vector.initialize()
                except Exception:
                    pass
        asyncio.run(_clear())

    def add_memory(self, context: MemoryContext) -> Dict[str, Any]:
        """Ingests context into MESA."""
        start_time = time.time()

        async def _add() -> None:
            embedding = await self.vector.compute_embedding(context.text)
            await self.memory_dao.insert_memory(
                content=context.text,
                agent_id="benchmark",
                entity_name=context.id,
                embedding=embedding,
                metadata=context.metadata
            )

        asyncio.run(_add())

        latency = (time.time() - start_time) * 1000
        return {"latency_ms": latency}

    def answer(self, question: BenchmarkQuestion) -> BenchmarkResponse:
        """Queries MESA and returns the response."""
        start_time = time.time()

        retrieved_ids = []
        answer_text = ""

        async def _answer() -> Any:
            query_embedding = await self.vector.compute_embedding(question.query)
            results = await self.memory_dao.search_memory(
                query_vector=query_embedding,
                agent_id="benchmark",
                limit=5,
                include_graph=True
            )
            return results

        results = asyncio.run(_answer())

        if results:
            for r in results:
                entity = r.get("graph", {}).get("entity_name")
                if entity:
                    retrieved_ids.append(entity)

            # Synthesize an answer for the LLM judge using the chunks
            answer_text = "\\n".join([str(r.get("graph", {}).get("content_payload", "")) for r in results])
        else:
            answer_text = "No relevant context found."

        latency = (time.time() - start_time) * 1000

        return BenchmarkResponse(
            answer_text=answer_text,
            retrieved_context_ids=retrieved_ids,
            latency_ms=latency,
            metadata={"mesa_version": "0.5.1"},
        )
