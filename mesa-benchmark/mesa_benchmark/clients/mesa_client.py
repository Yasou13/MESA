# ruff: noqa: E402
import asyncio
import concurrent.futures
import logging
import queue
import tempfile
import threading
import time
from typing import Any, Dict

from ..datasets.schemas import BenchmarkQuestion, MemoryContext
from .base import AbstractBenchmarkClient, BenchmarkResponse, RetrievedContext

logger = logging.getLogger(__name__)

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


class _MesaEventLoopWorker:
    """One owned async job worker for synchronous benchmark calls.

    A queue is used instead of touching the caller's event loop. Each job gets
    an isolated loop in the worker thread, so notebook loops are never patched
    or re-entered and no per-operation thread is created.
    """

    def __init__(self) -> None:
        self._ready = threading.Event()
        self._closed = False
        self._jobs: queue.Queue[tuple[Any, concurrent.futures.Future[Any]] | None] = (
            queue.Queue()
        )
        self.thread = threading.Thread(
            target=self._run, name="mesa-benchmark-async", daemon=False
        )
        self.thread.start()
        if not self._ready.wait(timeout=5):
            raise RuntimeError("MESA event-loop worker failed to start")

    def _run(self) -> None:
        self._ready.set()
        while True:
            job = self._jobs.get()
            if job is None:
                return
            coroutine, result = job
            try:
                value = asyncio.run(coroutine)
            except BaseException as exc:
                if not result.cancelled():
                    result.set_exception(exc)
            else:
                if not result.cancelled():
                    result.set_result(value)

    def run(self, coroutine: Any, timeout_s: float) -> Any:
        if self._closed:
            raise RuntimeError("MESA event-loop worker is closed")
        future: concurrent.futures.Future[Any] = concurrent.futures.Future()
        self._jobs.put((coroutine, future))
        try:
            return future.result(timeout=timeout_s)
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            raise TimeoutError(
                f"MESA operation exceeded provider timeout {timeout_s:.3f}s"
            ) from exc

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._jobs.put(None)
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            raise RuntimeError("MESA event-loop worker did not stop during close")


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
        self.enable_multi_hop: bool = True
        self.enable_rerank: bool = False
        self.reranker_model: str | None = None
        self.top_n: int = 5
        self.timeout_s: float = 30.0
        self._worker: _MesaEventLoopWorker | None = None
        self.context_id_map: Dict[str, str] = {}

    def initialize(self, config_params: Dict[str, Any]) -> None:
        """Initializes MESA storage engines (SQLite, LanceDB, KùzuDB), HybridRetriever, and CrossEncoder Reranker."""
        self.enable_multi_hop = config_params.get("enable_multi_hop", True)
        self.top_n = config_params.get("top_n", 5)
        self.enable_rerank = (
            config_params.get("enable_rerank", False)
            or "reranker_model" in config_params
        )
        self.reranker_model = config_params.get(
            "reranker_model", "cross-encoder/ms-marco-MiniLM-L-6-v2"
        )
        self.timeout_s = float(config_params.get("timeout_s", 30.0))

        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = f"{self.temp_dir.name}/mesa.db"
        lance_path = f"{self.temp_dir.name}/vector.lance"
        graph_path = f"{self.temp_dir.name}/graph.kuzu"

        async def _init() -> None:
            logger.info("Initializing SQLite engine")
            self.sqlite = AsyncEngine(db_path=db_path)
            await self.sqlite.initialize()
            await initialize_schema(self.sqlite)

            logger.info("Initializing vector engine")
            self.vector = VectorEngine(uri=lance_path, allow_model_loading=True)
            await self.vector.initialize()
            if getattr(self.vector, "_fallback_embedder", True) is True:
                raise RuntimeError(
                    "MESA benchmark requires the cached all-MiniLM-L6-v2 semantic "
                    "embedding model; deterministic/hash fallback is forbidden"
                )

            logger.info("Initializing KùzuDB graph provider")
            # Initialize KùzuDB Graph Engine
            kuzu_initialize_schema(graph_path)
            self.graph_provider = KuzuGraphProvider(db_path=graph_path)
            await self.graph_provider.initialize()

            logger.info("Initializing MemoryDAO")
            self.memory_dao = MemoryDAO(
                sqlite_engine=self.sqlite,
                vector_engine=self.vector,
                graph_provider=self.graph_provider,
            )
            await self.memory_dao.initialize()

            logger.info("Initializing reranker")
            reranker_instance = None
            if self.enable_rerank:
                try:
                    from mesa_memory.retrieval.reranker import CrossEncoderReranker

                    model_to_use = (
                        self.reranker_model or "cross-encoder/ms-marco-MiniLM-L-6-v2"
                    )
                    reranker_instance = CrossEncoderReranker(model_name=model_to_use)
                    logger.info(
                        "CrossEncoderReranker initialized for benchmark client with model: %s",
                        model_to_use,
                    )
                except Exception as e:
                    raise RuntimeError(
                        f"requested CrossEncoderReranker failed to initialize: {e}"
                    ) from e

            llm_adapter = AdapterFactory.get_adapter("auto")
            analyzer = QueryAnalyzer()
            logger.info("Initializing HybridRetriever")
            self.retriever = HybridRetriever(
                dao=self.memory_dao,
                analyzer=analyzer,
                embedder=llm_adapter,
                access_control=BenchmarkAccessControl(),
                reranker=reranker_instance,
            )
            logger.info("MESA client initialization complete")

        self._run(_init())

    def _run(self, coroutine: Any) -> Any:
        if self._worker is None:
            self._worker = _MesaEventLoopWorker()
        return self._worker.run(coroutine, self.timeout_s)

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
                listing = self.vector._db.list_tables()
                table_names = getattr(listing, "tables", listing)
                for table_name in table_names:
                    self.vector._db.drop_table(table_name)
                self.vector._tables.clear()
            if self.graph_provider and hasattr(self.graph_provider, "clear"):
                await self.graph_provider.clear()

        self._run(_clear())
        self.context_id_map.clear()

    def add_memory(self, context: MemoryContext) -> Dict[str, Any]:
        return self.add_memories([context])

    def add_memories(self, contexts: list[MemoryContext]) -> Dict[str, Any]:
        """Two-pass ingest: materialize all context nodes before graph edges."""
        start_time = time.time()

        async def _add() -> None:
            local_entities: dict[str, str] = {}
            inserted: list[tuple[MemoryContext, str]] = []
            for context in contexts:
                embedding = await self.vector.compute_embedding(context.text)
                meta = dict(context.metadata or {})
                meta["original_context_id"] = context.id
                node_name = str(
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
                self.context_id_map[node_id] = context.id
                local_entities[node_name] = node_id
                inserted.append((context, node_id))

            for context, source_id in inserted:
                relations = (context.metadata or {}).get("relations", [])
                for relation in relations:
                    target_name = str(relation.get("target", "")).strip()
                    if not target_name or not self.graph_provider:
                        continue
                    target_id = local_entities.get(target_name)
                    if target_id is None:
                        raise ValueError(
                            f"relation target {target_name!r} is not a materialized "
                            f"node in scenario batch for context {context.id!r}"
                        )
                    await self.graph_provider.insert_edge(
                        source_id=source_id,
                        target_id=target_id,
                        weight=1.0,
                        agent_id="benchmark",
                    )

        self._run(_add())

        latency = (time.time() - start_time) * 1000
        return {"latency_ms": latency, "count": len(contexts)}

    def answer(self, question: BenchmarkQuestion) -> BenchmarkResponse:
        """Queries MESA using HybridRetriever with multi-hop graph traversal enabled."""
        start_time = time.time()

        retrieved_ids = []
        answer_text = ""

        async def _answer() -> Any:
            try:
                results = await asyncio.wait_for(
                    self.retriever.retrieve(
                        query_text=question.query,
                        agent_id="benchmark",
                        session_id="__unset__",
                        top_n=self.top_n,
                        enable_multi_hop=self.enable_multi_hop,
                        collect_diagnostics=True,
                    ),
                    timeout=self.timeout_s,
                )
                return results
            except asyncio.TimeoutError as exc:
                raise TimeoutError(
                    f"MESA retrieval exceeded provider timeout {self.timeout_s}s "
                    f"for question {question.id}"
                ) from exc

        results = self._run(_answer())

        if isinstance(results, dict):
            cmb_ids = results.get("cmb_ids", [])
            multi_hop_path = results.get("multi_hop_path", [])
            all_ids = list(dict.fromkeys(cmb_ids + multi_hop_path))
        elif isinstance(results, list):
            all_ids = results
        else:
            all_ids = []

        valid_chunks = []
        retrieved_contexts: list[RetrievedContext] = []
        for nid in all_ids[: self.top_n]:

            async def _get_node(node_id: str) -> Any:
                return await self.memory_dao.get_memory_by_id("benchmark", node_id)

            node = self._run(_get_node(nid))
            if node:
                payload = node.get("content")
                orig_id = self.context_id_map.get(nid)

                if payload:
                    valid_chunks.append(str(payload))

                # Use original_context_id instead of entity_name for evaluation matching
                if orig_id and orig_id not in retrieved_ids:
                    retrieved_ids.append(orig_id)
                    retrieved_contexts.append(
                        RetrievedContext(
                            id=orig_id,
                            text=str(payload or ""),
                            rank=len(retrieved_contexts) + 1,
                        )
                    )

        if valid_chunks:
            answer_text = "\n".join(valid_chunks)
        else:
            answer_text = "No relevant context found."

        latency = (time.time() - start_time) * 1000

        latency_breakdown = {}
        diagnostics = {}
        if isinstance(results, dict):
            latency_breakdown = results.get("latency_breakdown_ms", {})
            diagnostics = results.get("diagnostics", {})

        return BenchmarkResponse(
            answer_text=answer_text,
            retrieved_context_ids=retrieved_ids,
            retrieved_contexts=retrieved_contexts,
            latency_ms=latency,
            retrieval_latency_ms=latency,
            metadata={
                "mesa_version": "0.6.1",
                "multi_hop_enabled": self.enable_multi_hop,
                "rerank_enabled": self.enable_rerank,
                "graph_backend": "KuzuDB",
                "latency_breakdown_ms": latency_breakdown,
                "diagnostics": diagnostics,
            },
        )

    def close(self) -> None:
        """Cleans up temporary resources."""

        async def _close() -> None:
            for resource in (self.sqlite, self.vector, self.graph_provider):
                close = getattr(resource, "close", None)
                if close is not None:
                    result = close()
                    if asyncio.iscoroutine(result):
                        await result

        if self._worker is not None:
            self._run(_close())
            self._worker.close()
            self._worker = None
        if hasattr(self, "temp_dir") and self.temp_dir:
            self.temp_dir.cleanup()
