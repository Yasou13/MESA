# ruff: noqa: E402
import asyncio
import logging
import os

import nest_asyncio

nest_asyncio.apply()
import sys
import tempfile
import time
from typing import Any, Dict

from ..datasets.schemas import BenchmarkQuestion, MemoryContext
from .base import AbstractBenchmarkClient, BenchmarkResponse

logger = logging.getLogger(__name__)

# Add parent directory of mesa_benchmark to path to find mesa_storage & mesa_memory
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../")))

from mesa_memory.adapter.factory import AdapterFactory
from mesa_memory.retrieval.core import QueryAnalyzer
from mesa_memory.retrieval.hybrid import HybridRetriever
from mesa_memory.security.rbac import AccessControl
from mesa_storage.dao import MemoryDAO
from mesa_storage.kuzu_provider import KuzuGraphProvider
from mesa_storage.kuzu_setup import initialize_schema as kuzu_initialize_schema
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.vector_engine import VectorEngine


class BenchmarkAccessControl(AccessControl):
    """Bypasses RBAC for benchmark read queries."""

    async def check_access(self, agent_id: str, session_id: str, mode: str) -> bool:
        return True


class MesaClientAdapter(AbstractBenchmarkClient):
    """
    Adapter for the MESA framework.
    Translates benchmark requests into MESA MemoryDAO and HybridRetriever calls,
    leveraging KùzuDB graph traversal and multi-hop spreading activation.
    """

    def __init__(self) -> None:
        self.memory_dao: Any = None
        self.sqlite: Any = None
        self.vector: Any = None
        self.graph_provider: Any = None
        self.retriever: Any = None
        self.temp_dir: Any = None
        self.loop = asyncio.new_event_loop()

    def initialize(self, config_params: Dict[str, Any]) -> None:
        """Initializes MESA storage engines (SQLite, LanceDB, KùzuDB) and HybridRetriever."""
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = f"{self.temp_dir.name}/mesa.db"
        lance_path = f"{self.temp_dir.name}/vector.lance"
        graph_path = f"{self.temp_dir.name}/graph.kuzu"

        async def _init() -> None:
            self.sqlite = AsyncEngine(db_path=db_path)
            await self.sqlite.initialize()
            await initialize_schema(self.sqlite)

            self.vector = VectorEngine(uri=lance_path)
            await self.vector.initialize()

            # Initialize KùzuDB Graph Engine
            kuzu_initialize_schema(graph_path)
            self.graph_provider = KuzuGraphProvider(db_path=graph_path)
            await self.graph_provider.initialize()

            self.memory_dao = MemoryDAO(
                sqlite_engine=self.sqlite,
                vector_engine=self.vector,
                graph_provider=self.graph_provider,
            )
            await self.memory_dao.initialize()

            llm_adapter = AdapterFactory.get_adapter("auto")
            analyzer = QueryAnalyzer()
            self.retriever = HybridRetriever(
                dao=self.memory_dao,
                analyzer=analyzer,
                embedder=llm_adapter,
                access_control=BenchmarkAccessControl(),
            )

        self.loop.run_until_complete(_init())

    def clear_memory(self) -> None:
        """Flushes the database for a clean test environment."""

        async def _clear() -> None:
            if self.sqlite:
                await self.sqlite.execute_script(
                    "DELETE FROM nodes;"
                    "DELETE FROM lancedb_wal;"
                    "DELETE FROM raw_logs;"
                    "DELETE FROM routing_telemetry;"
                )
            if self.vector and hasattr(self.vector, "_db") and self.vector._db:
                for table_name in self.vector._db.table_names():
                    self.vector._db.drop_table(table_name)
                self.vector._tables.clear()
            if self.graph_provider and hasattr(self.graph_provider, "clear"):
                await self.graph_provider.clear()

        self.loop.run_until_complete(_clear())

    def add_memory(self, context: MemoryContext) -> Dict[str, Any]:
        """Ingests context into MESA vector, relational, and graph storage."""
        start_time = time.time()

        async def _add() -> None:
            embedding = await self.vector.compute_embedding(context.text)

            meta = context.metadata or {}
            # Fallback to a truncated version of the text if no title/entity_name exists
            node_name = (
                meta.get("entity_name")
                or meta.get("title")
                or (
                    context.text[:50] + "..."
                    if len(context.text) > 50
                    else context.text
                )
            )

            node_id = await self.memory_dao.insert_memory(
                "benchmark",
                content=context.text,
                entity_name=node_name,
                embedding=embedding,
                metadata=meta,
            )

            if self.graph_provider:
                try:
                    await self.graph_provider.insert_node(
                        node_id=node_id,
                        name=node_name,
                        agent_id="benchmark",
                    )
                except Exception as e:
                    logger.warning("Failed to insert graph node %s: %s", node_name, e)

            # Insert edges if relations are specified in metadata
            relations = (context.metadata or {}).get("relations", [])
            for rel in relations:
                target_entity = rel.get("target")
                if target_entity and self.graph_provider:
                    target_node_id = target_entity
                    try:
                        if self.sqlite:
                            async with self.sqlite.connection() as conn:
                                cursor = await conn.execute(
                                    "SELECT id FROM nodes WHERE agent_id = 'benchmark' AND entity_name = ? LIMIT 1",
                                    (target_entity,),
                                )
                                row = await cursor.fetchone()
                                if row:
                                    target_node_id = row[0]

                        # Ensure target node exists in KuzuDB
                        await self.graph_provider.insert_node(
                            node_id=target_node_id,
                            name=target_entity,
                            agent_id="benchmark",
                        )
                        await self.graph_provider.insert_edge(
                            source_id=node_id,
                            target_id=target_node_id,
                            weight=1.0,
                            agent_id="benchmark",
                        )
                    except Exception as e:
                        logger.warning(
                            "Failed to insert graph edge %s -> %s: %s",
                            context.id,
                            target_entity,
                            e,
                        )

        self.loop.run_until_complete(_add())

        latency = (time.time() - start_time) * 1000
        return {"latency_ms": latency}

    def answer(self, question: BenchmarkQuestion) -> BenchmarkResponse:
        """Queries MESA using HybridRetriever with multi-hop graph traversal enabled."""
        start_time = time.time()

        retrieved_ids = []
        answer_text = ""

        async def _answer() -> Any:
            results = await self.retriever.retrieve(
                query_text=question.query,
                agent_id="benchmark",
                session_id="__unset__",
                top_n=5,
                enable_multi_hop=True,
            )
            return results

        results = self.loop.run_until_complete(_answer())

        if isinstance(results, dict):
            cmb_ids = results.get("cmb_ids", [])
            multi_hop_path = results.get("multi_hop_path", [])
            all_ids = list(dict.fromkeys(cmb_ids + multi_hop_path))
        elif isinstance(results, list):
            all_ids = results
        else:
            all_ids = []

        valid_chunks = []
        for nid in all_ids:

            async def _get_node(node_id: str) -> Any:
                return await self.memory_dao.get_memory_by_id("benchmark", node_id)

            node = self.loop.run_until_complete(_get_node(nid))
            if node:
                payload = node.get("content_payload")
                entity = node.get("entity_name")
                if payload:
                    valid_chunks.append(str(payload))
                if entity and entity not in retrieved_ids:
                    retrieved_ids.append(entity)

        if valid_chunks:
            answer_text = "\n".join(valid_chunks)
        else:
            answer_text = "No relevant context found."

        latency = (time.time() - start_time) * 1000

        return BenchmarkResponse(
            answer_text=answer_text,
            retrieved_context_ids=retrieved_ids,
            latency_ms=latency,
            metadata={
                "mesa_version": "0.5.1",
                "multi_hop_enabled": True,
                "graph_backend": "KuzuDB",
            },
        )

    def close(self) -> None:
        """Cleans up temporary resources."""
        if hasattr(self, "temp_dir") and self.temp_dir:
            self.temp_dir.cleanup()
