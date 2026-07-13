import hashlib
import logging
import os
import shutil
from typing import Any

from mesa_evals.benchmark_adapters.base import BaseMemoryClient, QueryResult
from mesa_memory.adapter.base import BaseUniversalLLMAdapter
from mesa_memory.retrieval.core import QueryAnalyzer
from mesa_memory.retrieval.hybrid import HybridRetriever
from mesa_memory.security.rbac import AccessControl
from mesa_storage.dao import MemoryDAO
from mesa_storage.kuzu_provider import KuzuGraphProvider
from mesa_storage.kuzu_setup import initialize_schema as kuzu_initialize_schema
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.vector_engine import VectorEngine
from mesa_workers.rem_cycle import REMCycleWorker

logger = logging.getLogger("MESA_Client")


class MesaClient(BaseMemoryClient):
    """MESA Memory Client for Antigravity Contradiction Benchmark.

    Supports ablation mode via environment variables:
        - MESA_ENABLE_GRAPH: "True"/"False" — enables/disables KùzuDB graph
        - MESA_ENABLE_CONSENSUS: "True"/"False" — enables/disables REM cycle
    """

    def __init__(self, adapter: BaseUniversalLLMAdapter):
        self._adapter = adapter

        # Read ablation flags (default: all enabled)
        self._enable_graph = (
            os.environ.get("MESA_ENABLE_GRAPH", "True").lower() == "true"
        )
        self._enable_consensus = (
            os.environ.get("MESA_ENABLE_CONSENSUS", "True").lower() == "true"
        )

        # Cleanup past test runs
        db_path = "./storage/benchmark_mesa_sql.db"
        if os.path.exists(db_path):
            os.remove(db_path)
        if os.path.exists(f"{db_path}-wal"):
            os.remove(f"{db_path}-wal")
        if os.path.exists(f"{db_path}-shm"):
            os.remove(f"{db_path}-shm")

        if os.path.exists("./storage/benchmark_mesa_vec"):
            shutil.rmtree("./storage/benchmark_mesa_vec")
        if os.path.exists("./storage/benchmark_mesa_graph"):
            if os.path.isdir("./storage/benchmark_mesa_graph"):
                shutil.rmtree("./storage/benchmark_mesa_graph")
            else:
                os.remove("./storage/benchmark_mesa_graph")

        os.makedirs("./storage", exist_ok=True)

        sql_engine = AsyncEngine("./storage/benchmark_mesa_sql.db")
        vec_engine = VectorEngine(uri="./storage/benchmark_mesa_vec")

        # Conditionally initialize graph — disabled in ablation Naive_Vector_RAG
        graph_provider = None
        if self._enable_graph:
            kuzu_initialize_schema("./storage/benchmark_mesa_graph")
            graph_provider = KuzuGraphProvider(db_path="./storage/benchmark_mesa_graph")

        self._sql_engine = sql_engine

        self._dao = MemoryDAO(
            sqlite_engine=sql_engine,
            vector_engine=vec_engine,
            graph_provider=graph_provider,
        )
        self._analyzer = QueryAnalyzer()

        # Override access control to always permit benchmark reads
        class BenchmarkAccessControl(AccessControl):
            async def check_access(
                self, agent_id: str, session_id: str, mode: str
            ) -> bool:
                return True

        self._retriever = HybridRetriever(
            self._dao,
            self._analyzer,
            self._adapter,
            access_control=BenchmarkAccessControl(),
        )

        # Conditionally initialize REM Worker — disabled in ablation variants
        self._rem_worker: REMCycleWorker | None = None
        if self._enable_consensus:
            self._rem_worker = REMCycleWorker(
                dao=self._dao,
                llm_a=self._adapter,
                llm_b=self._adapter,
                enabled=False,
                activation_threshold=1,
            )

        logger.info(
            "MESA_CLIENT_INIT | graph=%s consensus=%s",
            self._enable_graph,
            self._enable_consensus,
        )

    async def initialize(self) -> None:
        if not self._sql_engine._initialized:
            await self._sql_engine.initialize()
        await initialize_schema(self._sql_engine)

        vec_engine = self._dao.vector_engine
        if not vec_engine.is_initialized:
            await vec_engine.initialize()

        graph = self._dao.graph_provider
        if graph and not getattr(graph, "is_initialized", False):
            await graph.initialize()

        await self._dao.initialize()

    async def shutdown(self) -> None:
        pass

    async def add_memory(
        self, content: str, *, agent_id: str, metadata: dict[str, Any] | None = None
    ) -> str:
        self._validate_agent_id(agent_id)

        # Extract entity name via LLM instead of hardcoding.
        # This ensures the benchmark tests the full extraction pipeline,
        # not a hand-linked shortcut.
        entity_name = await self._extract_entity_name(content)

        embedding = await self._dao.vector_engine.compute_embedding(content)
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

        # Insert raw memory
        node_id = await self._dao.insert_memory(
            agent_id=agent_id,
            entity_name=entity_name,
            content=content,
            embedding=embedding,
            content_hash=content_hash,
            metadata=metadata,
        )

        # Trigger REM Cycle synchronously if consensus is enabled
        if self._rem_worker is not None:
            await self._rem_worker.run_now(agent_id)

        return node_id

    async def _extract_entity_name(self, content: str) -> str:
        """Extract the primary entity/subject from content via LLM.

        Falls back to first 50 chars of content on failure.
        """
        try:
            prompt = (
                "Extract the single most important entity name (person, organization, "
                "product, or concept) from the following text. Respond with ONLY the "
                "entity name, nothing else.\n\n"
                f"Text: {content[:500]}"
            )
            result = await self._adapter.acomplete(prompt)
            if isinstance(result, str):
                text_content = result
            elif hasattr(result, "model_dump_json"):
                text_content = getattr(result, "model_dump_json")()
            else:
                text_content = str(result)
            entity = text_content.strip().strip('"').strip("'")[:100]
            if entity:
                return entity
        except Exception as exc:
            logger.warning("Entity extraction failed: %s — using fallback", exc)

        # Fallback: use first meaningful words
        return content[:50].strip()

    async def query(
        self, question: str, *, agent_id: str, limit: int = 5
    ) -> QueryResult:
        self._validate_agent_id(agent_id)

        try:
            results = await self._retriever.retrieve(
                query_text=question,
                agent_id=agent_id,
                session_id="__unset__",
                top_n=limit,
                enable_multi_hop=True,
            )

            cmb_ids = (
                results.get("cmb_ids", []) if isinstance(results, dict) else results
            )

            nodes = []
            for cid in cmb_ids:
                node = await self._dao.get_memory_by_id(agent_id, cid)
                if node:
                    nodes.append(node)

            if not nodes:
                return QueryResult.empty()

            context = self._retriever.format_working_memory(nodes)

            chunks = [
                {
                    "node_id": n.get("id"),
                    "content": n.get("content_payload", ""),
                    "metadata": n.get("metadata", {}),
                }
                for n in nodes
            ]

            return QueryResult(
                context=context, chunks=chunks, total_chunks=len(nodes), error=None
            )
        except Exception as exc:
            logger.error("MESA_QUERY_FAILED: %s", exc, exc_info=True)
            return QueryResult.from_error(str(exc))

    async def clear_memory(self, *, agent_id: str) -> int:
        self._validate_agent_id(agent_id)
        return await self._dao.purge_memory(agent_id)
