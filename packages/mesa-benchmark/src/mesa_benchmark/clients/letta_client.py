import os
import time
from typing import Any, Dict

from ..datasets.schemas import BenchmarkQuestion, MemoryContext
from .base import AbstractBenchmarkClient, BenchmarkResponse, RetrievedContext

LettaClient: Any = None
LETTA_AVAILABLE = False

try:
    from letta_client import Letta as _LettaClient

    LettaClient = _LettaClient
    LETTA_AVAILABLE = True
except ImportError:
    try:
        from letta import Letta as _LettaClientAlt

        LettaClient = _LettaClientAlt
        LETTA_AVAILABLE = True
    except ImportError:
        pass


class LettaClientAdapter(AbstractBenchmarkClient):
    """
    Adapter for the Letta (formerly MemGPT) memory system.

    Letta provides stateful AI agents with in-context memory management,
    archival storage, and recall memory. This adapter maps the benchmark
    interface to Letta's agent-based memory operations.
    """

    def __init__(self) -> None:
        self.client: Any = None
        self.agent_id: str | None = None
        self.agent_name: str = "mesa_benchmark_agent"
        self.context_id_by_text: dict[str, str] = {}
        self.top_n = 5
        self.timeout_s = 30.0
        self.agent_model: str | None = None
        self.embedding_model: str | None = None

    def initialize(self, config_params: Dict[str, Any]) -> None:
        if not LETTA_AVAILABLE:
            raise ImportError(
                "Letta library is not installed. Install with: pip install letta"
            )

        base_url = config_params.get("base_url") or os.environ.get("LETTA_BASE_URL")
        if not base_url:
            raise ValueError(
                "Letta requires client.parameters.base_url or LETTA_BASE_URL"
            )
        self.agent_name = config_params.get("agent_name", "mesa_benchmark_agent")
        self.top_n = int(config_params.get("top_n", 5))
        self.timeout_s = float(config_params.get("timeout_s", 30.0))

        self.agent_model = config_params.get("agent_model") or os.environ.get(
            "LETTA_AGENT_MODEL"
        )
        self.embedding_model = config_params.get(
            "letta_embedding_model"
        ) or os.environ.get("LETTA_EMBEDDING_MODEL")
        self.client = LettaClient(
            base_url=base_url,
            api_key=os.environ.get("LETTA_API_KEY"),
        )

        # Create or reuse an agent for benchmarking
        self._ensure_agent()

    def _ensure_agent(self) -> None:
        """Creates a fresh agent or retrieves an existing one."""
        if not self.client:
            return

        if hasattr(self.client, "agents"):
            agents = self.client.agents.list(request_options=self._request_options())
            for agent in agents:
                if getattr(agent, "name", None) == self.agent_name:
                    self.agent_id = agent.id
                    return

        if hasattr(self.client, "agents"):
            create_options: dict[str, Any] = {
                "name": self.agent_name,
                "memory_blocks": [],
                "description": "MESA benchmark evaluation agent",
                "request_options": self._request_options(),
            }
            if self.agent_model:
                create_options["model"] = self.agent_model
            if self.embedding_model:
                create_options["embedding"] = self.embedding_model
            agent = self.client.agents.create(
                **create_options,
            )
            self.agent_id = agent.id
        else:
            agent = self.client.create_agent(
                name=self.agent_name,
                description="MESA benchmark evaluation agent",
            )
            self.agent_id = getattr(agent, "id", getattr(agent, "agent_id", None))
        if not self.agent_id:
            raise RuntimeError("Failed to create Letta agent for benchmarking")

    def _request_options(self) -> dict[str, float]:
        return {"timeout_in_seconds": self.timeout_s, "max_retries": 0}

    def clear_memory(self) -> None:
        """Deletes and recreates the agent for a clean test environment."""
        if not self.client or not self.agent_id:
            raise RuntimeError("Letta client is not initialized")
        if hasattr(self.client, "agents"):
            self.client.agents.delete(
                self.agent_id, request_options=self._request_options()
            )
        else:
            self.client.delete_agent(self.agent_id)
        self.agent_id = None
        self._ensure_agent()
        if not self.agent_id:
            raise RuntimeError("Failed to recreate Letta benchmark agent")
        self.context_id_by_text.clear()

    def add_memory(self, context: MemoryContext) -> Dict[str, Any]:
        start_time = time.time()

        if not self.client or not self.agent_id:
            raise RuntimeError("Letta client is not initialized")
        if hasattr(self.client, "agents"):
            # Insert into archival memory (Letta's long-term storage)
            self.client.agents.archival.create(
                agent_id=self.agent_id,
                text=context.text,
                metadata={"context_id": context.id},
                request_options=self._request_options(),
            )
        else:
            self.client.insert_archival_memory(
                agent_id=self.agent_id,
                memory=context.text,
            )
        self.context_id_by_text[context.text] = context.id

        latency = (time.time() - start_time) * 1000
        return {"latency_ms": latency}

    def answer(self, question: BenchmarkQuestion) -> BenchmarkResponse:
        start_time = time.time()

        retrieved_ids: list[str] = []
        answer_text = ""
        retrieved_contexts: list[RetrievedContext] = []

        if not self.client or not self.agent_id:
            raise RuntimeError("Letta client is not initialized")
        if hasattr(self.client, "agents"):
            # Search archival memory
            results = self.client.agents.archival.list(
                agent_id=self.agent_id,
                query=question.query,
                limit=self.top_n,
                request_options=self._request_options(),
            )
        else:
            results = self.client.get_archival_memory(
                agent_id=self.agent_id,
                query=question.query,
                limit=self.top_n,
            )

        chunks = []
        if results:
            for r in results:
                text = getattr(r, "text", None) or getattr(r, "content", "")
                if text:
                    chunks.append(str(text))
                meta = getattr(r, "metadata", {}) or {}
                if isinstance(meta, dict) and "context_id" in meta:
                    context_id = meta["context_id"]
                else:
                    context_id = self.context_id_by_text.get(str(text), "")
                if context_id:
                    retrieved_ids.append(context_id)
                    retrieved_contexts.append(
                        RetrievedContext(
                            id=context_id,
                            text=str(text),
                            rank=len(retrieved_contexts) + 1,
                        )
                    )

        answer_text = "\n".join(chunks) if chunks else "No relevant context found."

        latency = (time.time() - start_time) * 1000

        return BenchmarkResponse(
            answer_text=answer_text,
            retrieved_context_ids=retrieved_ids,
            retrieved_contexts=retrieved_contexts,
            latency_ms=latency,
            retrieval_latency_ms=latency,
            metadata={"source": "letta", "backend": "archival_memory"},
        )

    def close(self) -> None:
        """Delete the final benchmark agent before releasing the SDK client."""
        if self.client and self.agent_id:
            if hasattr(self.client, "agents"):
                self.client.agents.delete(
                    self.agent_id, request_options=self._request_options()
                )
            else:
                self.client.delete_agent(self.agent_id)
        self.client = None
        self.agent_id = None
