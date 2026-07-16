import time
from typing import Any, Dict

from ..datasets.schemas import BenchmarkQuestion, MemoryContext
from .base import AbstractBenchmarkClient, BenchmarkResponse

try:
    from letta import Letta as LettaClient

    LETTA_AVAILABLE = True
except ImportError:
    LettaClient = None
    LETTA_AVAILABLE = False


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

    def initialize(self, config_params: Dict[str, Any]) -> None:
        if not LETTA_AVAILABLE:
            raise ImportError(
                "Letta library is not installed. Install with: pip install letta"
            )

        base_url = config_params.get("base_url", "http://localhost:8283")
        self.agent_name = config_params.get("agent_name", "mesa_benchmark_agent")

        self.client = LettaClient(base_url=base_url)

        # Create or reuse an agent for benchmarking
        self._ensure_agent()

    def _ensure_agent(self) -> None:
        """Creates a fresh agent or retrieves an existing one."""
        if not self.client:
            return

        # Try to find existing agent
        try:
            agents = self.client.agents.list()
            for agent in agents:
                if getattr(agent, "name", None) == self.agent_name:
                    self.agent_id = agent.id
                    return
        except Exception:
            pass

        # Create new agent
        try:
            agent = self.client.agents.create(
                name=self.agent_name,
                memory_blocks=[],
                description="MESA benchmark evaluation agent",
            )
            self.agent_id = agent.id
        except Exception:
            # Fallback: try older API format
            try:
                agent = self.client.create_agent(
                    name=self.agent_name,
                    description="MESA benchmark evaluation agent",
                )
                self.agent_id = getattr(agent, "id", getattr(agent, "agent_id", None))
            except Exception:
                raise RuntimeError("Failed to create Letta agent for benchmarking.")

    def clear_memory(self) -> None:
        """Deletes and recreates the agent for a clean test environment."""
        if self.client and self.agent_id:
            try:
                self.client.agents.delete(self.agent_id)
            except Exception:
                try:
                    self.client.delete_agent(self.agent_id)
                except Exception:
                    pass
            self.agent_id = None
            self._ensure_agent()

    def add_memory(self, context: MemoryContext) -> Dict[str, Any]:
        start_time = time.time()

        if self.client and self.agent_id:
            # Insert into archival memory (Letta's long-term storage)
            try:
                self.client.agents.archival.create(
                    agent_id=self.agent_id,
                    text=context.text,
                    metadata={"context_id": context.id},
                )
            except Exception:
                # Fallback for older Letta API
                try:
                    self.client.insert_archival_memory(
                        agent_id=self.agent_id,
                        memory=context.text,
                    )
                except Exception:
                    pass

        latency = (time.time() - start_time) * 1000
        return {"latency_ms": latency}

    def answer(self, question: BenchmarkQuestion) -> BenchmarkResponse:
        start_time = time.time()

        retrieved_ids: list[str] = []
        answer_text = ""

        if self.client and self.agent_id:
            # Search archival memory
            try:
                results = self.client.agents.archival.list(
                    agent_id=self.agent_id,
                    query=question.query,
                    limit=5,
                )
            except Exception:
                try:
                    results = self.client.get_archival_memory(
                        agent_id=self.agent_id,
                        query=question.query,
                        limit=5,
                    )
                except Exception:
                    results = []

            chunks = []
            if results:
                for r in results:
                    text = getattr(r, "text", None) or getattr(r, "content", "")
                    if text:
                        chunks.append(str(text))
                    meta = getattr(r, "metadata", {}) or {}
                    if isinstance(meta, dict) and "context_id" in meta:
                        retrieved_ids.append(meta["context_id"])

            answer_text = "\n".join(chunks) if chunks else "No relevant context found."

        latency = (time.time() - start_time) * 1000

        return BenchmarkResponse(
            answer_text=answer_text,
            retrieved_context_ids=retrieved_ids,
            latency_ms=latency,
            metadata={"source": "letta", "backend": "archival_memory"},
        )

    def close(self) -> None:
        """Letta manages its own server connections."""
        self.client = None
        self.agent_id = None
