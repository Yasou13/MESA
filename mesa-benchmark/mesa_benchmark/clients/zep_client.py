import time
from typing import Any, Dict

from ..datasets.schemas import BenchmarkQuestion, MemoryContext
from .base import AbstractBenchmarkClient, BenchmarkResponse

try:
    from zep_cloud.client import Zep
    from zep_cloud.types import Message

    ZEP_AVAILABLE = True
except ImportError:
    try:
        from zep_python import ZepClient as Zep
        from zep_python.message import Message

        ZEP_AVAILABLE = True
    except ImportError:
        Zep = None
        Message = None
        ZEP_AVAILABLE = False


class ZepClientAdapter(AbstractBenchmarkClient):
    """
    Adapter for the Zep memory system.

    Zep provides long-term memory for AI assistants with automatic summarization,
    entity extraction, and temporal awareness. This adapter uses the same
    interface contract to ensure fair apple-to-apple comparison with MESA.
    """

    def __init__(self) -> None:
        self.client: Any = None
        self.session_id: str = "benchmark_session"

    def initialize(self, config_params: Dict[str, Any]) -> None:
        if not ZEP_AVAILABLE:
            raise ImportError(
                "Zep library is not installed. "
                "Install with: pip install zep-cloud  (or pip install zep-python)"
            )

        api_key = config_params.get("api_key", "")
        base_url = config_params.get("base_url")

        if base_url:
            self.client = Zep(api_key=api_key, base_url=base_url)
        else:
            self.client = Zep(api_key=api_key)

        self.session_id = config_params.get("session_id", "benchmark_session")

        # Ensure session exists
        try:
            self.client.memory.add_session(
                session_id=self.session_id,
                metadata={"benchmark": True},
            )
        except Exception:
            pass  # Session may already exist

    def clear_memory(self) -> None:
        """Deletes and recreates the session to flush all memory."""
        if self.client:
            try:
                self.client.memory.delete(session_id=self.session_id)
            except Exception:
                pass
            try:
                self.client.memory.add_session(
                    session_id=self.session_id,
                    metadata={"benchmark": True},
                )
            except Exception:
                pass

    def add_memory(self, context: MemoryContext) -> Dict[str, Any]:
        start_time = time.time()

        if self.client:
            messages = [
                Message(
                    role="user",
                    content=context.text,
                    metadata={"context_id": context.id},
                )
            ]
            self.client.memory.add(
                session_id=self.session_id,
                messages=messages,
            )

        latency = (time.time() - start_time) * 1000
        return {"latency_ms": latency}

    def answer(self, question: BenchmarkQuestion) -> BenchmarkResponse:
        start_time = time.time()

        retrieved_ids: list[str] = []
        answer_text = ""

        if self.client:
            # Zep search_memory returns relevant memory fragments
            results = self.client.memory.search(
                session_id=self.session_id,
                text=question.query,
                search_type="mmr",  # Maximal Marginal Relevance
                limit=5,
            )

            chunks = []
            if results:
                for r in results:
                    content = getattr(r, "content", None) or getattr(r, "summary", "")
                    if content:
                        chunks.append(str(content))
                    meta = getattr(r, "metadata", {}) or {}
                    if "context_id" in meta:
                        retrieved_ids.append(meta["context_id"])

            answer_text = "\n".join(chunks) if chunks else "No relevant context found."

        latency = (time.time() - start_time) * 1000

        return BenchmarkResponse(
            answer_text=answer_text,
            retrieved_context_ids=retrieved_ids,
            latency_ms=latency,
            metadata={"source": "zep", "search_type": "mmr"},
        )

    def close(self) -> None:
        """Zep manages its own connections."""
        self.client = None
