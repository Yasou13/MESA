import time
import uuid
from typing import Any, Dict

from ..datasets.schemas import BenchmarkQuestion, MemoryContext
from .base import AbstractBenchmarkClient, BenchmarkResponse, RetrievedContext

Zep: Any = None
Message: Any = None
ZEP_AVAILABLE = False

try:
    from zep_cloud.client import Zep as ZepCloud
    from zep_cloud.types import Message as ZepCloudMessage

    Zep = ZepCloud
    Message = ZepCloudMessage
    ZEP_AVAILABLE = True
except ImportError:
    try:
        from zep_python import ZepClient as ZepPython
        from zep_python.message import Message as ZepPythonMessage

        Zep = ZepPython
        Message = ZepPythonMessage
        ZEP_AVAILABLE = True
    except ImportError:
        pass


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
        self.session_prefix: str = "benchmark_session"
        self.top_n: int = 5

    def initialize(self, config_params: Dict[str, Any]) -> None:
        if not ZEP_AVAILABLE:
            raise ImportError(
                "Zep library is not installed. "
                "Install with: pip install zep-cloud  (or pip install zep-python)"
            )

        api_key = config_params.get("api_key", "")
        base_url = config_params.get("base_url")
        timeout_s = float(config_params.get("timeout_s", 30.0))
        self.top_n = int(config_params.get("top_n", 5))

        if base_url:
            self.client = Zep(api_key=api_key, base_url=base_url, timeout=timeout_s)
        else:
            self.client = Zep(api_key=api_key, timeout=timeout_s)

        self.session_prefix = config_params.get("session_id", "benchmark_session")
        self.session_id = f"{self.session_prefix}_{uuid.uuid4().hex}"

        # Ensure session exists
        self.client.memory.add_session(
            session_id=self.session_id,
            metadata={"benchmark": True},
        )

    def clear_memory(self) -> None:
        """Deletes and recreates the session to flush all memory."""
        if not self.client:
            raise RuntimeError("Zep client is not initialized")
        self.client.memory.delete(session_id=self.session_id)
        self.session_id = f"{self.session_prefix}_{uuid.uuid4().hex}"
        self.client.memory.add_session(
            session_id=self.session_id,
            metadata={"benchmark": True},
        )

    def add_memory(self, context: MemoryContext) -> Dict[str, Any]:
        start_time = time.time()

        if not self.client:
            raise RuntimeError("Zep client is not initialized")
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
        retrieved_contexts: list[RetrievedContext] = []

        if not self.client:
            raise RuntimeError("Zep client is not initialized")
        # Zep search_memory returns relevant memory fragments
        results = self.client.memory.search(
            session_id=self.session_id,
            text=question.query,
            search_type="mmr",  # Maximal Marginal Relevance
            limit=self.top_n,
        )

        chunks = []
        if results:
            for r in results:
                content = getattr(r, "content", None) or getattr(r, "summary", "")
                if content:
                    chunks.append(str(content))
                meta = getattr(r, "metadata", {}) or {}
                if "context_id" in meta:
                    context_id = meta["context_id"]
                    retrieved_ids.append(context_id)
                    retrieved_contexts.append(
                        RetrievedContext(
                            id=context_id,
                            text=str(content or ""),
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
            metadata={"source": "zep", "search_type": "mmr"},
        )

    def close(self) -> None:
        """Delete the final benchmark session before releasing the SDK client."""
        if self.client:
            self.client.memory.delete(session_id=self.session_id)
        self.client = None
