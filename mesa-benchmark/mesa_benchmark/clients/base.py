from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator

from ..datasets.schemas import BenchmarkQuestion, MemoryContext


class RetrievedContext(BaseModel):
    """A ranked context returned by a benchmarked memory system."""

    id: str
    text: str = ""
    rank: int = Field(ge=1)
    score: Optional[float] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class BenchmarkResponse(BaseModel):
    """Standardized response from any target memory system."""

    answer_text: str = Field(
        "", description="Generated answer, or raw contexts for legacy adapters."
    )
    retrieved_context_ids: List[str] = Field(
        default_factory=list, description="IDs of contexts retrieved by the system."
    )
    retrieved_contexts: List[RetrievedContext] = Field(
        default_factory=list,
        description="Ranked retrieval payload used by the common QA generator.",
    )
    latency_ms: float = Field(
        ..., description="Time taken to return the answer in milliseconds."
    )
    retrieval_latency_ms: Optional[float] = Field(
        None, ge=0.0, description="Memory retrieval latency, excluding generation."
    )
    generation_latency_ms: Optional[float] = Field(
        None, ge=0.0, description="Common answer-generation latency."
    )
    token_usage: Dict[str, int] = Field(
        default_factory=dict,
        description="Number of tokens used, e.g. {'prompt': 150, 'completion': 50}.",
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict, description="Any other system-specific metadata."
    )

    @model_validator(mode="after")
    def synchronize_legacy_fields(self) -> "BenchmarkResponse":
        if self.retrieval_latency_ms is None:
            self.retrieval_latency_ms = self.latency_ms
        if self.retrieved_contexts and not self.retrieved_context_ids:
            self.retrieved_context_ids = [item.id for item in self.retrieved_contexts]
        return self

    def enforce_top_k(self, top_k: int) -> "BenchmarkResponse":
        """Return a copy whose retrieval payload cannot exceed the shared Top-K."""
        if top_k < 1:
            raise ValueError("top_k must be positive")
        contexts = self.retrieved_contexts[:top_k]
        ids = self.retrieved_context_ids[:top_k]
        if contexts:
            ids = [item.id for item in contexts]
        return self.model_copy(
            update={"retrieved_contexts": contexts, "retrieved_context_ids": ids}
        )


class AbstractBenchmarkClient(ABC):
    """
    Abstract Base Class that all target memory systems MUST implement.
    This enforces the 'Apple-to-Apple' rule.
    """

    @abstractmethod
    def initialize(self, config_params: Dict[str, Any]) -> None:
        """
        Initializes the client with given parameters (e.g., API keys, URLs).
        """
        pass

    @abstractmethod
    def clear_memory(self) -> None:
        """
        Completely flushes the memory of the target system to ensure isolated test runs.
        """
        pass

    @abstractmethod
    def add_memory(self, context: MemoryContext) -> Dict[str, Any]:
        """
        Ingests a piece of context into the memory system.
        Should return a dictionary containing at least 'latency_ms'.
        """
        pass

    def add_memories(self, contexts: List[MemoryContext]) -> Dict[str, Any]:
        """Batch hook; adapters may override it for graph-aware two-pass ingestion."""
        total_latency_ms = 0.0
        for context in contexts:
            result = self.add_memory(context)
            total_latency_ms += float(result.get("latency_ms", 0.0))
        return {"latency_ms": total_latency_ms, "count": len(contexts)}

    def storage_size_bytes(self) -> Optional[int]:
        """Return measurable benchmark-owned storage, or ``None`` if unavailable.

        Adapters must not estimate opaque provider-side storage. A missing value is
        reported as unavailable instead of being silently converted to zero.
        """
        return None

    @abstractmethod
    def answer(self, question: BenchmarkQuestion) -> BenchmarkResponse:
        """
        Queries the target system for the answer to the given question.
        """
        pass

    @abstractmethod
    def close(self) -> None:
        """
        Cleanly shuts down the client and releases any acquired resources (e.g., temporary directories, connection pools).
        """
        pass
