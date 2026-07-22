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


class Mem0ClientAdapter(AbstractBenchmarkClient):
    """
    Adapter for the Mem0 system (Baseline).
    Uses Mem0's built-in OpenAI-compatible provider, which reads
    OPENAI_API_KEY and OPENAI_BASE_URL from environment variables automatically.
    """

    def __init__(self) -> None:
        self.memory = None
        self.current_user_id = str(uuid.uuid4())
        self.top_n = 5
        self.timeout_s = 30.0

    def initialize(self, config_params: Dict[str, Any]) -> None:
        if Memory is None:
            raise ImportError(
                "mem0ai library is not installed. Run 'pip install mem0ai'"
            )

        # Mem0 reads OPENAI_API_KEY and OPENAI_BASE_URL from env vars.
        # For embedding, it uses the Ollama provider directly via OLLAMA_HOST.
        self.top_n = int(config_params.get("top_n", 5))
        self.timeout_s = float(config_params.get("timeout_s", 30.0))
        mem0_config = config_params.get("mem0_config")
        if mem0_config is None:
            model = os.environ.get("BENCHMARK_GENERATOR_MODEL")
            if not model:
                raise ValueError(
                    "Mem0 requires BENCHMARK_GENERATOR_MODEL or client.parameters.mem0_config"
                )
            mem0_config = {
                "llm": {
                    "provider": "openai",
                    "config": {"model": model, "timeout": self.timeout_s},
                },
                "embedder": {
                    "provider": "ollama",
                    "config": {
                        "model": os.environ.get(
                            "BENCHMARK_EMBEDDING_MODEL", "nomic-embed-text:latest"
                        ),
                        "timeout": self.timeout_s,
                    },
                },
            }
        else:
            mem0_config = deepcopy(mem0_config)
            for key in ("llm", "embedder"):
                component = mem0_config.setdefault(key, {})
                component.setdefault("config", {}).setdefault("timeout", self.timeout_s)

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

    def add_memory(self, context: MemoryContext) -> Dict[str, Any]:
        start_time = time.time()

        if not self.memory:
            raise RuntimeError("Mem0 client is not initialized")
        self.memory.add(
            context.text,
            user_id=self.current_user_id,
            metadata={"id": context.id},
        )

        latency = (time.time() - start_time) * 1000
        return {"latency_ms": latency}

    def answer(self, question: BenchmarkQuestion) -> BenchmarkResponse:
        start_time = time.time()

        retrieved_ids: list[str] = []
        answer_text = ""

        if not self.memory:
            raise RuntimeError("Mem0 client is not initialized")
        raw_results = self.memory.search(
            question.query, user_id=self.current_user_id, limit=self.top_n
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
                meta = res.get("metadata", {})
                context_id = meta.get("id")
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
