import logging
import os
import time
import uuid
from copy import deepcopy
from typing import Any, Dict

from ..datasets.schemas import BenchmarkQuestion, MemoryContext
from .base import AbstractBenchmarkClient, BenchmarkResponse, RetrievedContext

try:
    from mem0 import Memory
except ImportError:
    Memory = None

logger = logging.getLogger(__name__)


def _embedding_dimensions(model: str, configured: Any = None) -> int:
    if configured is not None:
        dimensions = int(configured)
        if dimensions < 1:
            raise ValueError("Mem0 embedding_dims must be positive")
        return dimensions

    normalized = model.casefold().removeprefix("sentence-transformers/")
    if normalized == "all-minilm-l6-v2":
        return 384
    if normalized.startswith("nomic-embed-text"):
        return 768
    raise ValueError(
        f"Unknown embedding dimensions for {model!r}; "
        "set client.parameters.embedding_dims"
    )


class Mem0ClientAdapter(AbstractBenchmarkClient):
    """
    Adapter for the Mem0 system (Baseline).
    Uses Mem0's built-in OpenAI-compatible provider, which reads
    OPENAI_API_KEY and OPENAI_BASE_URL from environment variables automatically.
    """

    def __init__(self) -> None:
        self.memory = None
        self.current_user_id = str(uuid.uuid4())
        self.context_id_by_memory_id: dict[str, str] = {}
        self.top_n = 5
        self.timeout_s = 30.0
        self.infer = True

    def initialize(self, config_params: Dict[str, Any]) -> None:
        if Memory is None:
            raise ImportError(
                "mem0ai library is not installed. Run 'pip install mem0ai'"
            )

        # Mem0 reads OPENAI_API_KEY and OPENAI_BASE_URL from env vars.
        self.top_n = int(config_params.get("top_n", 5))
        self.timeout_s = float(config_params.get("timeout_s", 30.0))
        self.infer = bool(config_params.get("infer", True))
        mem0_config = config_params.get("mem0_config")
        if mem0_config is None:
            model = os.environ.get("BENCHMARK_GENERATOR_MODEL")
            if not model and self.infer:
                raise ValueError(
                    "Mem0 requires BENCHMARK_GENERATOR_MODEL or client.parameters.mem0_config"
                )
            if self.infer:
                llm_config = {"model": model}
            else:
                # Mem0 constructs an LLM provider even when add(..., infer=False).
                # It is never called in direct-ingest quality/capacity runs.
                llm_config = {
                    "model": model or "unused-direct-ingest",
                    "api_key": "not-used",
                    "openai_base_url": "http://127.0.0.1:9/v1",
                }
            embedding_model = config_params.get("embedding_model") or os.environ.get(
                "BENCHMARK_EMBEDDING_MODEL", "nomic-embed-text:latest"
            )
            embedding_provider = (
                "huggingface" if config_params.get("embedding_model") else "ollama"
            )
            embedding_dims = _embedding_dimensions(
                str(embedding_model), config_params.get("embedding_dims")
            )
            embedder_config: dict[str, Any] = {
                "model": embedding_model,
                "embedding_dims": embedding_dims,
            }
            if embedding_provider == "ollama":
                embedder_config["ollama_base_url"] = os.environ.get(
                    "BENCHMARK_OLLAMA_URL"
                )
            mem0_config = {
                "vector_store": {
                    "provider": "qdrant",
                    "config": {
                        "path": ":memory:",
                        "embedding_model_dims": embedding_dims,
                    },
                },
                "llm": {
                    "provider": "openai",
                    "config": llm_config,
                },
                "embedder": {
                    "provider": embedding_provider,
                    "config": embedder_config,
                },
            }
        else:
            mem0_config = deepcopy(mem0_config)

        self.memory = Memory.from_config(mem0_config)
        configured_timeout = False
        for component_name in ("llm", "embedding_model"):
            component = getattr(self.memory, component_name, None)
            client = getattr(component, "client", None)
            if client is not None and hasattr(client, "timeout"):
                client.timeout = self.timeout_s
                configured_timeout = True
        if not configured_timeout:
            self.memory = None
            raise RuntimeError(
                "Mem0 SDK does not expose a timeout-capable provider client; "
                "refusing a benchmark run without native I/O deadlines"
            )

    def clear_memory(self) -> None:
        """Purge the previous namespace before creating a fresh benchmark user."""
        if not self.memory:
            raise RuntimeError("Mem0 client is not initialized")
        self.memory.delete_all(user_id=self.current_user_id)
        self.current_user_id = str(uuid.uuid4())
        self.context_id_by_memory_id.clear()

    def add_memory(self, context: MemoryContext) -> Dict[str, Any]:
        start_time = time.time()

        if not self.memory:
            raise RuntimeError("Mem0 client is not initialized")
        added = self.memory.add(
            context.text,
            user_id=self.current_user_id,
            metadata={"id": context.id},
            infer=self.infer,
        )
        records = added.get("results", []) if isinstance(added, dict) else added
        if isinstance(records, list):
            for record in records:
                if not isinstance(record, dict):
                    continue
                memory_id = record.get("id")
                if memory_id:
                    self.context_id_by_memory_id[str(memory_id)] = context.id

        latency = (time.time() - start_time) * 1000
        return {"latency_ms": latency}

    def answer(self, question: BenchmarkQuestion) -> BenchmarkResponse:
        start_time = time.time()

        retrieved_ids: list[str] = []
        answer_text = ""

        if not self.memory:
            raise RuntimeError("Mem0 client is not initialized")
        raw_results = self.memory.search(
            question.query,
            filters={"user_id": self.current_user_id},
            top_k=self.top_n,
        )
        results = (
            raw_results.get("results", [])
            if isinstance(raw_results, dict)
            else raw_results
        )
        answer_text = str(results)
        retrieved_contexts = []
        if results:
            for res in results[: self.top_n]:
                meta = res.get("metadata") or {}
                context_id = meta.get("id") or self.context_id_by_memory_id.get(
                    str(res.get("id", ""))
                )
                if context_id:
                    retrieved_ids.append(context_id)
                    retrieved_contexts.append(
                        RetrievedContext(
                            id=context_id,
                            text=str(res.get("memory") or res.get("text") or ""),
                            rank=len(retrieved_contexts) + 1,
                            score=res.get("score"),
                        )
                    )

        latency = (time.time() - start_time) * 1000

        return BenchmarkResponse(
            answer_text=answer_text,
            retrieved_context_ids=retrieved_ids,
            retrieved_contexts=retrieved_contexts,
            token_usage={},
            latency_ms=latency,
            retrieval_latency_ms=latency,
            metadata={"source": "mem0"},
        )

    def close(self) -> None:
        if self.memory is None:
            return
        self.memory.delete_all(user_id=self.current_user_id)
        self.memory = None
        self.context_id_by_memory_id.clear()
